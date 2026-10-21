"""单条会话消息的意图判断。

TypeSafe Choice 必须给选项，但不能把业务意图写死成固定 7 类。
起步词表只作规则捷径和常见参考；组长纠正过的样本会进入下一轮题目。
原文保持中文，说明用英文。
"""
from __future__ import annotations
import asyncio
import json
import re

import httpx

from .config import LLM_CONNECT_TIMEOUT, LLM_TIMEOUT_CLASSIFY, httpx_proxy, resolve_intent_llm
from .party import sender_of
from .llm import LlmError, extract_json_lenient, _strip_json_fence

_INTENTS = {
    "send_form": "发送申报书",
    "request_revision": "要求修改申报书",
    "supply_material": "要求补材料",
    "ask": "核对提问",
    "confirm": "确认收到",
    "chat": "闲聊",
    "other": "其他",
}
INTENTS = _INTENTS
SKIP_INTENT_CODES = frozenset({"confirm", "chat"})
SKIP_INTENT_LABELS = frozenset({"确认收到", "闲聊", "应过滤"})
_CRITERIA_CAP = 48


def normalize_intent(code: str = "", label: str = "") -> tuple[str, str]:
    """把种子英文码、中文名、组长自填名收成 (id, 显示名)。未知意图原样保留。"""
    c = str(code or "").strip()[:40]
    lab = str(label or "").strip()[:40]
    if c in _INTENTS:
        return c, _INTENTS[c]
    if lab in _INTENTS:
        return lab, _INTENTS[lab]
    for k, v in _INTENTS.items():
        if c == v:
            return k, v
        if lab == v:
            return k, v
    if not c:
        c = lab
    if not lab:
        lab = _INTENTS.get(c, c)
    return (c or "other"), (lab or "其他")


def intent_label(code: str) -> str:
    _, lab = normalize_intent(code)
    return lab or "其他"


def is_skip_intent(code: str = "", label: str = "") -> bool:
    c = str(code or "").strip()
    lab = str(label or "").strip()
    return c in SKIP_INTENT_CODES or c in SKIP_INTENT_LABELS or lab in SKIP_INTENT_CODES or lab in SKIP_INTENT_LABELS


def parse_intent_json(text: str) -> dict:
    data = extract_json_lenient(text)
    if not isinstance(data, dict):
        raise ValueError("意图结果不是 JSON")
    code, lab = normalize_intent(
        str(data.get("intent") or "").strip(),
        str(data.get("intentLabel") or data.get("label") or "").strip(),
    )
    need, action = _NEED_ACTION.get(code, ("", ""))
    return {
        "read": True,
        "readLabel": "已读",
        "intent": code,
        "intentLabel": lab,
        "need": str(data.get("need") or need).strip()[:80],
        "action": str(data.get("action") or action).strip()[:80],
    }


def _blank_intent(note: str = "", error: str = "") -> dict:
    return {
        "read": True,
        "readLabel": "已读",
        "intent": "other",
        "intentLabel": "其他",
        "need": "",
        "action": note,
        "error": error,
        "score": 0,
    }


def score_of(row) -> int:
    """把 Jev / TypeSafe 的 confidence、score、choice 收成 0–100。"""
    if row is None:
        return 0
    if isinstance(row, (int, float)):
        n = float(row)
        if 0 <= n <= 1:
            return int(round(n * 100))
        return int(max(0, min(100, round(n))))
    if not isinstance(row, dict):
        s = str(row).strip()
        if s.isdigit():
            return int(max(0, min(100, int(s))))
        return 0
    for key in ("score", "confidence", "prob", "probability"):
        val = row.get(key)
        if val is None or val == "":
            continue
        got = score_of(val)
        if got:
            return got
        if val in (0, 0.0, "0"):
            return 0
    choice = str(row.get("choice") or "").strip()
    if choice.isdigit():
        return int(max(0, min(100, int(choice))))
    return 0


def parse_intent_list(text: str, n: int) -> list:
    """按输入条数对齐的意图数组。缺条或坏条记为其他。"""
    s = _strip_json_fence(str(text or ""))
    data = None
    stripped = s.lstrip()
    if stripped.startswith("["):
        try:
            data, _ = json.JSONDecoder().raw_decode(stripped, 0)
        except json.JSONDecodeError:
            data = None
    if data is None:
        obj = extract_json_lenient(s)
        if isinstance(obj, list):
            data = obj
        elif isinstance(obj, dict):
            for key in ("items", "intents", "results", "messages", "data"):
                if isinstance(obj.get(key), list):
                    data = obj[key]
                    break
            if data is None and ("intent" in obj or "read" in obj):
                data = [obj]
    if not isinstance(data, list):
        raise ValueError("意图结果不是数组")
    out = []
    for i in range(max(0, int(n))):
        row = data[i] if i < len(data) and isinstance(data[i], dict) else None
        if not isinstance(row, dict):
            out.append(_blank_intent(error="这条没有解析出意图"))
            continue
        try:
            out.append(parse_intent_json(json.dumps(row, ensure_ascii=False)))
        except (ValueError, TypeError, json.JSONDecodeError):
            out.append(_blank_intent(error="这条没有解析出意图"))
    return out


def _has_intent_body(message: dict) -> bool:
    if not isinstance(message, dict):
        return False
    text = str(message.get("text") or message.get("snippet") or "").strip()
    filename = str(message.get("filename") or message.get("attachment_name") or "").strip()
    return bool(text or filename)


def _slim(message: dict, context: list) -> dict:
    def one(m):
        if not isinstance(m, dict):
            return None
        text = str(m.get("text") or m.get("snippet") or "").strip()
        filename = str(m.get("filename") or m.get("attachment_name") or "").strip()
        if len(text) > 400:
            text = text[:400]
        return {
            "sender": sender_of(m)[:40],
            "time": str(m.get("time") or m.get("time_text") or "")[:32],
            "filename": filename[:120],
            "text": text,
        }

    prev = []
    for row in (context or [])[-6:]:
        item = one(row)
        if item:
            prev.append(item)
    return {"context": prev, "message": one(message) or {}}


_INTENT_CRITERIA = {
    "send_form": "ONLY if they attach the 申报书 file or say they are sending it (申报书发给你). A form field like 是否缴纳社保-否 is NOT this. OK/收到 is NOT this.",
    "request_revision": "They give a NEW form-field VALUE to write, e.g. 是否缴纳社保-否, 护照号改为X. A yes/no check, blank-field note, or status chase is not this. Prefer a finer label (要求改护照号) when it fits.",
    "supply_material": "They ask to SEND a missing PROOF FILE (护照扫描件, 学位证, 营业执照). Blank 形式审核 / 未填字段 / 进度催办 is NOT this. Prefer 要求补护照 / 要求补学历 when named.",
    "ask": "Generic yes/no (吗/是否/对吗/确认一下) about whether the form is correct, and no finer 核对薪资 / 核对社保 applies. Not 收到. Not a rewrite request.",
    "confirm": "ONLY short ack with no question and no new ask: 收到 / 好的 / 知道了 / 嗯 / ok / 1. 确认一下…吗 is NOT this.",
    "chat": "Small talk with no work content. 申报书 / 薪资 / 材料 questions are not chat.",
    "other": "Work-related but no listed label is precise. Stretching 要求补材料 / 要求修改申报书 is worse than other.",
}

_SUGGESTED_CRITERIA = {
    "形式审核缺项": "The 形式审核 / form-review section is blank (形式审核部分不填 / 形式审核不填). NOT 要求补材料 — no proof file is requested.",
    "漏填字段": "A named form field is empty (XX未填 / 不填 / 空着 / 漏填). Not a scan/diploma request.",
    "催改进度": "They chase whether the revision is done (改好了吗 / 什么时候改完). Not a new field value.",
    "询问进度": "They ask current status of the application (进度如何 / 办到哪了). Not a field change.",
    "催交材料": "Follow-up on materials already requested (材料什么时候交 / 还没收到材料). Prefer this over generic 要求补材料 when tone is chasing.",
    "询问交付时间": "They ask when the 申报书 / revision will be ready (什么时候能好 / 几点给).",
    "核对薪资": "They ask whether salary / 工资 / 月薪 matches the form. More specific than generic 核对提问.",
    "核对社保": "They ask whether 社保 / 是否缴纳社保 is consistent. A value like 是否缴纳社保-否 is request_revision, not this.",
    "匹配进度": "They ask about talent matching progress (匹配进度 / 匹配得怎么样).",
    "发送修改意见": "They send or mention a 修改意见 / 意见 file, not the 申报书 itself.",
    "重发申报书": "They send an updated 申报书 after a previous version. Only if a file is being sent.",
    "要求改护照号": "They specifically ask to change the passport number. Prefer this over generic request_revision.",
    "要求补护照": "They ask for the passport scan / 护照复印件. Prefer this over generic 要求补材料.",
    "要求补学历": "They ask for diploma / 学位证 / 学历证明. Prefer this over generic 要求补材料.",
}

_INTENT_INSTRUCTIONS = (
    "Pick the ONE most specific intent of the CURRENT message. "
    "Criteria mix common labels, finer suggested labels, and leader-named labels; they are not a closed taxonomy. "
    "If a finer listed label is precise, use it. Do not dump everything into 要求补材料/supply_material or 要求修改申报书/request_revision. "
    "supply_material is ONLY a request to send a missing proof file. Blank form sections are 形式审核缺项 or 漏填字段. "
    "request_revision needs an actual new value to write (改为X / 字段-值). Asking 填了吗 / 是否一致 is not this. "
    "A generic yes/no (吗/是否/对吗/确认一下) is 核对提问/ask only when no more specific 核对薪资/核对社保 applies. "
    "Short 收到/好的 with no question is 确认收到. Chat has no 申报书/薪资/材料. "
    "If none is precise, pick other — stretching a coarse label is worse than other."
)

_NEED_ACTION = {
    "send_form": ("申报书文件", "核对并配对意见"),
    "request_revision": ("修改申报书内容", "按意见改申报书"),
    "supply_material": ("补充材料", "收集并上传材料"),
    "ask": ("核对申报信息", "答复是否与申报书一致"),
    "confirm": ("", ""),
    "chat": ("", ""),
    "other": ("", ""),
    "形式审核缺项": ("补全形式审核字段", "按缺项补填申报书"),
    "漏填字段": ("补全未填字段", "按原文补填"),
    "催改进度": ("告知修改进度", "回复预计完成时间"),
    "询问进度": ("说明当前进度", "回复进展"),
    "催交材料": ("尽快提供材料", "跟进材料"),
    "询问交付时间": ("给出交付时间", "回复截止日期"),
    "核对薪资": ("核对薪资是否一致", "对照申报书答复"),
    "核对社保": ("核对社保字段", "对照申报书答复"),
    "匹配进度": ("说明匹配进展", "回复匹配状态"),
    "发送修改意见": ("修改意见文件", "配对申报书"),
    "重发申报书": ("更新后的申报书", "核对版本"),
    "要求改护照号": ("修改护照号", "按意见改申报书"),
    "要求补护照": ("护照扫描件", "收集并上传"),
    "要求补学历": ("学历证明", "收集并上传"),
}


def suggested_intent_labels() -> dict[str, str]:
    """建议细分名，不是封闭词表。"""
    return {k: k for k in _SUGGESTED_CRITERIA}

_CONFIRM_RE = re.compile(
    r"^(然后|那好|那就|好吧)?\s*"
    r"(收到|好的|好滴|好哒|知道了|明白了?|ok(?:ay)?|1|对|是|好)"
    r"[。.!！~，,、\s👍👌🙏😊😁]*$",
    re.I,
)
_SEND_RE = re.compile(
    r"(发(给|你|我|来|去|一下)?.{0,8}申报书|申报书.{0,8}(发|发送|发给|发你|发我|发来)|附件.{0,8}申报书)",
    re.I,
)
_SEND_NAME_RE = re.compile(r"(申报书|申请表).*\.(docx?|pdf|wps)$", re.I)
_MATERIAL_RE = re.compile(
    r"((补|提供|交|传|上传|缺).{0,10}(材料|护照|学位证|扫描件|身份证|营业执照|劳动合同|在职证明)"
    r"|(材料|护照|学位证|扫描件).{0,6}(补|发|交|传))"
)
_REVISION_RE = re.compile(
    r"(改成|改为|修改|更正|请改|改一下|改下|把.{1,16}(改|换|删)|删掉|加上.{0,8}(句|字|字段))"
)
_FIELD_VALUE_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z0-9()（）/]{2,30}\s*[-：:=]\s*\S+$"
)
_ASK_RE = re.compile(r"(吗[?？]?$|[?？]$|对吗|是不是|一致吗|确认一下|对不对|是否一致)")
_WORK_RE = re.compile(r"(申报书|薪资|材料|护照|学位|社保|匹配|意见)")
_CHAT_HINT_RE = re.compile(r"(吃饭|喝咖啡|周末愉快|天气|哈哈+|呵呵+|早上好|晚安)")


def _msg_text(message: dict) -> tuple[str, str]:
    row = message or {}
    text = str(row.get("text") or row.get("snippet") or "").strip()
    filename = str(row.get("filename") or row.get("attachment_name") or "").strip()
    return text, filename


def _pack_rule(code: str, score: int) -> dict:
    need, action = _NEED_ACTION.get(code, ("", ""))
    item = {
        "intent": code,
        "intentLabel": intent_label(code),
        "score": int(score),
        "need": need,
        "action": action,
    }
    return {
        "read": True,
        "readLabel": "已读",
        "source": "rule",
        "candidates": [item],
        **item,
    }


def looks_like_send(message: dict) -> bool:
    text, filename = _msg_text(message)
    if filename and _SEND_NAME_RE.search(filename):
        return True
    return bool(text and _SEND_RE.search(text))


def rule_intent(message: dict) -> dict | None:
    """诊断用完整规则。评分路径请用 rule_intent_for_score，避免业务句被收成粗类。"""
    text, filename = _msg_text(message)
    compact = re.sub(r"[\s\[\]\"“”]+", "", text)
    if filename and _SEND_NAME_RE.search(filename):
        return _pack_rule("send_form", 95)
    if compact and _CONFIRM_RE.match(compact):
        return _pack_rule("confirm", 95)
    if text and _SEND_RE.search(text):
        return _pack_rule("send_form", 92)
    if text and _MATERIAL_RE.search(text):
        return _pack_rule("supply_material", 90)
    if text and _REVISION_RE.search(text):
        return _pack_rule("request_revision", 90)
    if compact and _FIELD_VALUE_RE.match(compact):
        return _pack_rule("request_revision", 88)
    if text and _ASK_RE.search(text):
        return _pack_rule("ask", 88)
    if compact and _CHAT_HINT_RE.search(compact) and not _WORK_RE.search(compact):
        return _pack_rule("chat", 85)
    return None


def rule_intent_for_score(message: dict) -> dict | None:
    """只拦极明显的短确认、申报书附件/发送、闲聊；补材料/请改/提问交给 Jev 细分。"""
    text, filename = _msg_text(message)
    compact = re.sub(r"[\s\[\]\"“”]+", "", text)
    if filename and _SEND_NAME_RE.search(filename):
        return _pack_rule("send_form", 95)
    if compact and _CONFIRM_RE.match(compact):
        return _pack_rule("confirm", 95)
    if text and _SEND_RE.search(text):
        return _pack_rule("send_form", 92)
    if compact and _CHAT_HINT_RE.search(compact) and not _WORK_RE.search(compact):
        return _pack_rule("chat", 85)
    return None


_SCORE_RULE = (
    " If this is NOT the primary intent of the current message, you MUST choose 0. "
    "Mentioning 申报书 is not enough. At most one or two intents should score 70+."
)
_EXCLUSIVE_INTENTS = {"confirm", "chat"}
_KEEP_MIN = 70
_KEEP_GAP = 12

_SYSTEMONE_QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": _INTENT_INSTRUCTIONS,
        "criteria": _INTENT_CRITERIA,
    }
}

_SCORE_QUESTION = {
    "score": {
        "type": "choice",
        "instructions": "Confidence that the intent is correct, as a 0-100 band. Pick the closest.",
        "criteria": {
            "95": "Almost certain: explicit file, named field change, or clear request.",
            "85": "Clear intent with only minor ambiguity.",
            "75": "Likely intent, worth a leader review.",
            "60": "Possible but weak.",
            "40": "Mostly a guess.",
        },
    }
}

_SCORE_BANDS = {
    "95": "Almost certain this intent.",
    "85": "Clear match.",
    "75": "Likely match, worth review.",
    "60": "Possible.",
    "40": "Weak.",
    "0": "Not this intent.",
}


def _example_snip(row: dict) -> str:
    blob = ((row.get("filename") or "") + " " + (row.get("text") or "")).strip()
    blob = " ".join(blob.split())
    return blob[:80]


def _criteria_with_examples(examples: list | None = None, extra_intents: list | None = None) -> dict:
    grouped: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for row in examples or []:
        if not isinstance(row, dict):
            continue
        code, lab = normalize_intent(
            str(row.get("intent") or row.get("label") or "").strip(),
            str(row.get("intentLabel") or row.get("intent_label") or "").strip(),
        )
        if not code or code == "other":
            continue
        names[code] = lab
        snip = _example_snip(row)
        if snip:
            grouped.setdefault(code, []).append(snip)

    def with_samples(code: str, desc: str) -> str:
        samples = grouped.get(code) or []
        if samples:
            return desc + " Leader examples: " + " || ".join(samples[:4])
        return desc

    out = {}
    for code, desc in _INTENT_CRITERIA.items():
        if code == "other":
            continue
        out[code] = with_samples(code, desc)
    for code, desc in _SUGGESTED_CRITERIA.items():
        if code in out:
            continue
        out[code] = with_samples(code, desc)
    learned = [c for c in names if c not in out]
    learned.sort(key=lambda c: (-len(grouped.get(c) or []), c))
    extras: list[tuple[str, str]] = []
    seen_ex: set[str] = set()
    for row in extra_intents or []:
        if isinstance(row, dict):
            code, lab = normalize_intent(
                str(row.get("id") or row.get("intent") or row.get("label") or "").strip(),
                str(row.get("name") or row.get("intentLabel") or row.get("intent_label") or "").strip(),
            )
        else:
            code, lab = normalize_intent(str(row or "").strip())
        if not code or code == "other" or code in out or code in seen_ex:
            continue
        seen_ex.add(code)
        extras.append((code, lab))
    room = max(0, _CRITERIA_CAP - len(out) - 1)
    for code in learned:
        if room <= 0:
            break
        if code in out:
            continue
        lab = names.get(code) or intent_label(code)
        desc = "Leader-named intent: " + lab + ". Use this when it is more precise than the common labels."
        samples = grouped.get(code) or []
        if samples:
            desc += " Examples: " + " || ".join(samples[:4])
        out[code] = desc
        room -= 1
    for code, lab in extras:
        if room <= 0:
            break
        if code in out:
            continue
        out[code] = "Previously used intent: " + lab + ". Use when more precise than the common labels."
        room -= 1
    out["other"] = with_samples("other", _INTENT_CRITERIA["other"])
    return out


def rank_questions(examples: list | None = None, extra_intents: list | None = None) -> dict:
    """一条消息只问一个互斥选择题。选项含常见类、更细建议类和组长命名，不是封闭 7 类。"""
    return {
        "intent": {
            "type": "choice",
            "instructions": _INTENT_INSTRUCTIONS,
            "criteria": _criteria_with_examples(examples, extra_intents),
        },
        "score": {
            "type": "choice",
            "instructions": str(_SCORE_QUESTION["score"]["instructions"]),
            "criteria": dict(_SCORE_QUESTION["score"]["criteria"]),
        },
    }


def _iter_scored_intents(answers: dict) -> list[tuple[str, int]]:
    scored: list[tuple[str, int]] = []
    seen: set[str] = set()

    def add(code, sc) -> None:
        code, _ = normalize_intent(str(code or "").strip())
        if not code or code in seen:
            return
        seen.add(code)
        scored.append((code, int(sc or 0)))

    intent_row = answers.get("intent") if isinstance(answers, dict) else None
    overall = 0
    if isinstance(answers, dict):
        overall = score_of(answers.get("score")) or score_of(intent_row)
    if isinstance(intent_row, dict):
        probs = intent_row.get("probabilities")
        chosen = str(intent_row.get("choice") or "").strip()
        if isinstance(probs, dict) and probs:
            for code, p in probs.items():
                sc = score_of(p)
                if chosen and str(code) == chosen and overall:
                    sc = overall
                add(code, sc)
            return scored
        if chosen:
            add(chosen, overall or score_of(intent_row))
            return scored
    if isinstance(answers, dict):
        for code, val in answers.items():
            if code in ("intent", "score", "need", "action", "customLabel"):
                continue
            add(code, score_of(val))
    return scored


def ranked_intents(answers: dict, limit: int = 3) -> list[dict]:
    """按得分列出沾边意图。不把整份词表都摊出来。"""
    limit = max(1, min(int(limit or 3), 5))
    scored = _iter_scored_intents(answers if isinstance(answers, dict) else {})
    if not scored and isinstance(answers, dict) and answers.get("intent") is not None:
        one = answers_to_intent(answers)
        return [{
            "intent": one["intent"],
            "intentLabel": one["intentLabel"],
            "score": int(one.get("score") or 0),
            "need": one.get("need") or "",
            "action": one.get("action") or "",
        }][:limit]
    scored.sort(key=lambda x: -x[1])
    if not scored:
        return []
    top_code, top_sc = scored[0]
    exclusive = top_code in _EXCLUSIVE_INTENTS or is_skip_intent(top_code)
    # 独立打分时仍用高门槛，避免 7 路都偏高；概率分布则放宽，只丢掉明显无关的。
    independent = isinstance(answers, dict) and not (
        isinstance(answers.get("intent"), dict) and answers.get("intent", {}).get("probabilities")
    )
    if exclusive:
        floor = top_sc
    elif independent:
        floor = max(_KEEP_MIN, top_sc - _KEEP_GAP)
    else:
        floor = max(10, min(25, int(top_sc) // 5))
    out = []
    for code, sc in scored:
        if sc <= 0:
            continue
        if exclusive and code != top_code:
            continue
        if out and sc < floor:
            continue
        code, lab = normalize_intent(code)
        need, action = _NEED_ACTION.get(code, ("", ""))
        out.append({
            "intent": code,
            "intentLabel": lab,
            "score": int(sc),
            "need": need,
            "action": action,
        })
        if len(out) >= limit:
            break
    return out


def answers_to_intent(answers: dict) -> dict:
    row = answers.get("intent") if isinstance(answers, dict) else None
    raw = ""
    if isinstance(row, dict):
        raw = str(row.get("choice") or row.get("text") or row.get("value") or "").strip()
    elif isinstance(row, str):
        raw = row.strip()
    code, lab = normalize_intent(raw)
    need, action = _NEED_ACTION.get(code, ("", ""))
    score = 0
    if isinstance(answers, dict):
        score = score_of(answers.get("score")) or score_of(row)
    return {
        "read": True,
        "readLabel": "已读",
        "intent": code,
        "intentLabel": lab,
        "need": need,
        "action": action,
        "score": score,
    }


async def _systemone_raw(state, *, prof: dict | None = None, questions: dict | None = None) -> dict:
    prof = prof or resolve_intent_llm()
    url = str(prof.get("baseUrl") or "").rstrip("/") + "/v1/systemone"
    timeout = min(float(prof.get("timeoutSec") or 60), LLM_TIMEOUT_CLASSIFY)
    body = {
        "model": str(prof.get("model") or "jev-latest"),
        "state": state,
        "questions": questions or rank_questions(),
    }
    headers = {
        "Authorization": "Bearer " + str(prof.get("apiKey") or ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    proxy = str(prof.get("proxy") or "").strip() or httpx_proxy()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=LLM_CONNECT_TIMEOUT),
            trust_env=False,
            proxy=proxy or None,
        ) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise LlmError("TypeSafe 请求失败：" + str(e)[:120]) from e
    if resp.status_code == 451:
        raise LlmError(
            "TypeSafe 当前地区不可用（HTTP 451）。请保持 Clash 开启，或在后台「模型配置 → 意图分析」填写代理，例如 http://127.0.0.1:7897"
        )
    if resp.status_code >= 400:
        raise LlmError("TypeSafe HTTP " + str(resp.status_code) + "：" + (resp.text or "")[:160])
    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise LlmError("TypeSafe 返回不是 JSON") from e
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        raise LlmError("TypeSafe 未返回 answers")
    return answers


async def _systemone(state, *, prof: dict | None = None, questions: dict | None = None) -> dict:
    return answers_to_intent(await _systemone_raw(state, prof=prof, questions=questions))


async def judge_message(message: dict, context: list | None = None) -> dict:
    try:
        slim = _slim(message, context or [])
        return await _systemone(slim)
    except (LlmError, ValueError, TypeError) as e:
        return _blank_intent(error=str(e)[:160])


async def judge_message_scored(
    message: dict,
    context: list | None = None,
    examples: list | None = None,
    extra_intents: list | None = None,
) -> dict:
    """极明显短句走规则；其余问 Jev，保留若干候选，避免收成粗类。"""
    ruled = rule_intent_for_score(message)
    if ruled:
        return ruled
    try:
        slim = _slim(message, context or [])
        slim["rule"] = (
            "Pick the most specific listed intent. "
            "Do not stretch 要求补材料 or 要求修改申报书. "
            "Blank 形式审核 is 形式审核缺项. Salary check is 核对薪资. "
            "确认收到 is only 收到/好的 with no question."
        )
        if examples:
            slim["leaderExamples"] = [
                {
                    "intent": r.get("intent") or r.get("label"),
                    "text": _example_snip(r if isinstance(r, dict) else {}),
                }
                for r in examples[:24]
                if isinstance(r, dict) and _example_snip(r)
            ]
        questions = rank_questions(examples, extra_intents)
        try:
            answers = await _systemone_raw(slim, questions=questions)
            ranked = ranked_intents(answers, 5)
        except (LlmError, ValueError, TypeError):
            one = await _systemone(slim, questions=questions)
            ranked = [{
                "intent": one.get("intent") or "other",
                "intentLabel": one.get("intentLabel") or "其他",
                "score": int(one.get("score") or 0),
                "need": one.get("need") or "",
                "action": one.get("action") or "",
            }]
        if not looks_like_send(message):
            ranked = [r for r in ranked if str(r.get("intent") or "") != "send_form"]
        if not ranked:
            ranked = [{
                "intent": "other",
                "intentLabel": "其他",
                "score": 75,
                "need": "",
                "action": "",
            }]
        top = ranked[0]
        item = {
            "intent": top.get("intent") or "other",
            "intentLabel": top.get("intentLabel") or intent_label(top.get("intent") or "other"),
            "score": int(top.get("score") or 0),
            "need": top.get("need") or "",
            "action": top.get("action") or "",
        }
        out = dict(item)
        out["read"] = True
        out["readLabel"] = "已读"
        out["candidates"] = ranked[:5]
        return out
    except (LlmError, ValueError, TypeError) as e:
        blank = _blank_intent(error=str(e)[:160])
        blank["candidates"] = []
        return blank


async def judge_page(messages: list) -> list:
    """本页消息逐条调用 TypeSafe。空消息不请求。最多 4 路并行。"""
    rows = [m if isinstance(m, dict) else {} for m in (messages or [])][:200]
    out: list = []
    pending: list[int] = []
    for i, message in enumerate(rows):
        if _has_intent_body(message):
            out.append(None)
            pending.append(i)
        else:
            out.append(_blank_intent("这条没有可分析的正文"))
    if not pending:
        return out

    try:
        prof = resolve_intent_llm()
    except ValueError as e:
        err = str(e)[:160]
        for idx in pending:
            out[idx] = _blank_intent(error=err)
        return out

    sem = asyncio.Semaphore(4)

    async def one(idx: int) -> None:
        async with sem:
            try:
                slim = _slim(rows[idx], [])
                out[idx] = await _systemone(slim, prof=prof)
            except Exception as e:
                out[idx] = _blank_intent(error=str(e)[:160])

    await asyncio.gather(*[one(idx) for idx in pending])
    for i, row in enumerate(out):
        if row is None:
            out[i] = _blank_intent(error="这条没有返回意图")
    extra = len(messages or []) - len(out)
    if extra > 0:
        out.extend([_blank_intent(error="本批未覆盖，请再点分析意图") for _ in range(extra)])
    return out
