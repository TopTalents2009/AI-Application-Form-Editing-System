"""单条会话消息的意图判断。

题目固定、说明用英文、原文保持中文。答题走 TypeSafe System One（Jev），
不再调用 12ai Gemini 自由生成 JSON。
"""
from __future__ import annotations
import asyncio
import json
import re

import httpx

from .config import LLM_CONNECT_TIMEOUT, LLM_TIMEOUT_CLASSIFY, httpx_proxy, resolve_intent_llm
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

def intent_label(code: str) -> str:
    return _INTENTS.get(str(code or "").strip(), "其他")


def parse_intent_json(text: str) -> dict:
    data = extract_json_lenient(text)
    if not isinstance(data, dict):
        raise ValueError("意图结果不是 JSON")
    code = str(data.get("intent") or "other").strip()
    if code not in _INTENTS:
        code = "other"
    return {
        "read": True,
        "readLabel": "已读",
        "intent": code,
        "intentLabel": intent_label(code),
        "need": str(data.get("need") or "").strip()[:80],
        "action": str(data.get("action") or "").strip()[:80],
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
            "sender": str(m.get("sender") or "")[:40],
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
    "request_revision": "They give or change a form field value, e.g. 是否缴纳社保-否, 护照号改为X. A yes/no check like 是否一致/对吗 is not this.",
    "supply_material": "They ask for a missing proof file: passport, diploma, scan, 材料. Asking about salary or form text is not this.",
    "ask": "They ask a question (吗/是否/是不是/对吗/一致吗/确认一下) about salary, a field, or whether the form is correct. Not 收到. Not a rewrite request.",
    "confirm": "ONLY short ack with no question and no new ask: 收到 / 好的 / 知道了 / 嗯 / ok / 1. 确认一下…吗 is NOT this.",
    "chat": "Small talk with no work content. 申报书 / 薪资 / 材料 questions are not chat.",
    "other": "Work-related but none of the above (status, deadline, matching, etc.).",
}

_INTENT_INSTRUCTIONS = (
    "Pick EXACTLY ONE primary intent for the CURRENT message. "
    "A question (吗/是否/对吗/确认一下) is ask, never confirm. "
    "Short 收到/好的 with no question is confirm. "
    "Chat has no 申报书/薪资/材料. "
    "request_revision needs an actual change request, not a yes/no check. "
    "If none of the business intents fit, pick other."
)

_NEED_ACTION = {
    "send_form": ("申报书文件", "核对并配对意见"),
    "request_revision": ("修改申报书内容", "按意见改申报书"),
    "supply_material": ("补充材料", "收集并上传材料"),
    "ask": ("核对申报信息", "答复是否与申报书一致"),
    "confirm": ("", ""),
    "chat": ("", ""),
    "other": ("", ""),
}

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
    """明显句子先走规则，避免 Jev 把填字段/短回复打成发送申报书。"""
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


def _criteria_with_examples(examples: list | None) -> dict:
    grouped: dict[str, list[str]] = {}
    for row in examples or []:
        code = str(row.get("intent") or row.get("label") or "").strip()
        snip = _example_snip(row if isinstance(row, dict) else {})
        if code in _INTENTS and snip:
            grouped.setdefault(code, []).append(snip)
    out = {}
    for code, desc in _INTENT_CRITERIA.items():
        samples = grouped.get(code) or []
        if samples:
            desc = desc + " Leader examples: " + " || ".join(samples[:4])
        out[code] = desc
    return out


def rank_questions(examples: list | None = None) -> dict:
    """一条消息只问一个互斥选择题，避免 7 路独立打分都偏高。"""
    return {
        "intent": {
            "type": "choice",
            "instructions": _INTENT_INSTRUCTIONS,
            "criteria": _criteria_with_examples(examples),
        },
        "score": {
            "type": "choice",
            "instructions": str(_SCORE_QUESTION["score"]["instructions"]),
            "criteria": dict(_SCORE_QUESTION["score"]["criteria"]),
        },
    }


def ranked_intents(answers: dict, limit: int = 3) -> list[dict]:
    """只保留真正沾边的意图，避免每条都铺满同一组高分。"""
    limit = max(1, min(int(limit or 3), 5))
    scored: list[tuple[str, int]] = []
    if isinstance(answers, dict):
        for code in _INTENTS:
            if code not in answers:
                continue
            scored.append((code, score_of(answers.get(code))))
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
    exclusive = top_code in _EXCLUSIVE_INTENTS
    floor = top_sc if exclusive else max(_KEEP_MIN, top_sc - _KEEP_GAP)
    out = []
    for code, sc in scored:
        if sc <= 0:
            continue
        if exclusive and code != top_code:
            continue
        if out and sc < floor:
            continue
        need, action = _NEED_ACTION.get(code, ("", ""))
        out.append({
            "intent": code,
            "intentLabel": intent_label(code),
            "score": int(sc),
            "need": need,
            "action": action,
        })
        if len(out) >= limit:
            break
    return out


def answers_to_intent(answers: dict) -> dict:
    row = answers.get("intent") if isinstance(answers, dict) else None
    code = "other"
    if isinstance(row, dict):
        code = str(row.get("choice") or "other").strip()
    elif isinstance(row, str):
        code = row.strip()
    if code not in _INTENTS:
        code = "other"
    need, action = _NEED_ACTION.get(code, ("", ""))
    score = 0
    if isinstance(answers, dict):
        score = score_of(answers.get("score")) or score_of(row)
    return {
        "read": True,
        "readLabel": "已读",
        "intent": code,
        "intentLabel": intent_label(code),
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
        "questions": questions or _SYSTEMONE_QUESTIONS,
    }
    headers = {
        "Authorization": "Bearer " + str(prof.get("apiKey") or ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=LLM_CONNECT_TIMEOUT),
            trust_env=False,
            proxy=httpx_proxy(),
        ) as client:
            resp = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        raise LlmError("TypeSafe 请求失败：" + str(e)[:120]) from e
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


async def judge_message_scored(message: dict, context: list | None = None, examples: list | None = None) -> dict:
    """明显句子走规则；其余才问 Jev，且只保留一条主意图。"""
    ruled = rule_intent(message)
    if ruled:
        return ruled
    try:
        slim = _slim(message, context or [])
        slim["rule"] = (
            "Pick exactly one primary intent. 确认收到 is only 收到/好的 with no question. "
            "确认一下/是否一致 is 核对提问, not 确认收到."
        )
        if examples:
            slim["leaderExamples"] = [
                {
                    "intent": r.get("intent") or r.get("label"),
                    "text": _example_snip(r if isinstance(r, dict) else {}),
                }
                for r in examples[:16]
                if isinstance(r, dict) and _example_snip(r)
            ]
        try:
            answers = await _systemone_raw(slim, questions=rank_questions(examples))
            ranked = ranked_intents(answers, 1)
        except (LlmError, ValueError, TypeError):
            qs = dict(_SYSTEMONE_QUESTIONS)
            qs.update(_SCORE_QUESTION)
            one = await _systemone(slim, questions=qs)
            ranked = [{
                "intent": one.get("intent") or "other",
                "intentLabel": one.get("intentLabel") or "其他",
                "score": int(one.get("score") or 0),
                "need": one.get("need") or "",
                "action": one.get("action") or "",
            }]
        top = ranked[0] if ranked else _blank_intent()
        if str(top.get("intent") or "") == "send_form" and not looks_like_send(message):
            top = _pack_rule("other", 75)
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
        out["candidates"] = [item]
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
