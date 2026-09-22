"""拆解任务：用现有 Gemini chat 协助定位申报书与修改意见。不改 llm 调用参数。"""
from __future__ import annotations
import json
from .config import LLM_TIMEOUT_CLASSIFY, resolve_gemini
from .llm import LlmError, chat, extract_json_lenient
from .pdf_app import ALLOWED_APP_EXT, ext_of
from .opinion_extract import ALLOWED_OPINION_EXT
from .wecom_cases import (
    _APP_NAME,
    _CHATTER,
    _DONE_NAME,
    _NOT_FORM,
    _OPINION_NAME,
    _attach,
    _file_ref,
    _fname,
    _has_file,
    _msg_kind,
    _names_of,
    _refresh_case,
    _text_ref,
    classify_file,
    cluster_messages,
)

_PROMPT = """你在拆企业微信群聊天记录，找出「已填写的申报书」和对应的「修改意见」。

申报书：文件名含申报书/申请表，或几乎只有人名的 PDF/Word。下面这些不是申报书：资料清单、填表须知、聘用意向书、意向协议、合同/协议、模板/模版、唯一申报承诺、护照、学历、简历。
修改意见：审核/辅导意见文档，或聊天里具体要求改哪些字段、补哪些材料的文字。「收到」「好的」不是意见。
把意见配到对应申报书（同一人、相近时间、同一份材料）。

输入是 messages 数组。id 必须原样引用，禁止编造。
rule 是规则初判：app=申报书，opinion=意见文档，text=意见文本，ignore=忽略。

只输出一个 JSON 对象，不要 Markdown：
{"apps":[1],"ignore":[2],"links":[{"from":3,"to":1}]}
- apps：确认为申报书的 id
- ignore：不是申报书也不是修改意见的 id
- links：from 是意见（文档或文本）id，to 是申报书 id
找不到则 {"apps":[],"ignore":[],"links":[]}

消息列表：
"""


def _catalog(messages: list, *, max_files: int = 80, max_texts: int = 80) -> list:
    files, texts = [], []
    cap_f = max(20, int(max_files or 80))
    cap_t = max(20, int(max_texts or 80))
    cap_all = cap_f + cap_t
    for i, m in enumerate(messages or []):
        if not isinstance(m, dict):
            continue
        fn = _fname(m)
        text = str(m.get("text") or m.get("snippet") or "").strip()
        if fn and _has_file(m):
            files.append((i, m, fn, text))
            continue
        if text and len(text) >= 8 and not _CHATTER.match(text):
            texts.append((i, m, fn, text))
    picked = files[:cap_f] + texts[:cap_t]
    out = []
    for n, (_i, m, fn, text) in enumerate(picked[:cap_all], 1):
        out.append({
            "id": n,
            "m": m,
            "filename": fn,
            "text": text[:280],
            "time": str(m.get("time_text") or m.get("time") or ""),
            "sender": str(m.get("sender") or ""),
            "rule": _msg_kind(m),
        })
    return out


def _allow_app(m: dict) -> bool:
    return classify_file(*_names_of(m), message_id=(m or {}).get("message_id")) == "app"


def _allow_opinion(m: dict) -> bool:
    if not _has_file(m):
        text = str(m.get("text") or m.get("snippet") or "").strip()
        return bool(text and len(text) >= 8 and not _CHATTER.match(text))
    n = _fname(m)
    if not n or _DONE_NAME.search(n):
        return False
    if _NOT_FORM.search(n) and not _OPINION_NAME.search(n):
        return False
    if _APP_NAME.search(n):
        return False
    ext = ext_of(n)
    return ext in ALLOWED_OPINION_EXT or ext in ALLOWED_APP_EXT


def _allow_ignore_app(m: dict) -> bool:
    return not _APP_NAME.search(_fname(m) or "")


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def assist_split_with_gemini(
    messages: list,
    cases: list,
    *,
    session_id: str = "",
    session_name: str = "",
    window_hours: int = 48,
    max_files: int = 80,
    max_texts: int = 80,
) -> tuple[list, str]:
    """规则拆解之后调用 Gemini 纠偏。失败则原样返回 cases。"""
    cat = _catalog(messages, max_files=max_files, max_texts=max_texts)
    if not cat:
        return cases, ""
    try:
        prof = resolve_gemini()
    except ValueError as e:
        return cases, str(e)
    slim = [{
        "id": r["id"],
        "time": r["time"],
        "sender": r["sender"],
        "filename": r["filename"],
        "text": r["text"],
        "rule": r["rule"],
    } for r in cat]
    try:
        resp = await chat(
            [{"role": "user", "content": _PROMPT + json.dumps(slim, ensure_ascii=False)}],
            json_mode=True,
            timeout_s=LLM_TIMEOUT_CLASSIFY,
            model=prof.get("id"),
        )
        data = extract_json_lenient(resp.get("content") or "")
    except (LlmError, ValueError, TypeError, json.JSONDecodeError) as e:
        return cases, "Gemini 协助失败：" + str(e)[:160]
    if not isinstance(data, dict):
        return cases, "Gemini 返回无法解析"
    by_id = {int(r["id"]): r for r in cat}
    overrides = {}

    for raw in data.get("apps") or []:
        nid = _as_int(raw.get("id") if isinstance(raw, dict) else raw)
        row = by_id.get(nid) if nid is not None else None
        if not row or not _allow_app(row["m"]):
            continue
        overrides[id(row["m"])] = "app"
    for raw in data.get("ignore") or []:
        nid = _as_int(raw.get("id") if isinstance(raw, dict) else raw)
        row = by_id.get(nid) if nid is not None else None
        if not row:
            continue
        if row["rule"] == "app" and not _allow_ignore_app(row["m"]):
            continue
        overrides[id(row["m"])] = "ignore"
    for link in data.get("links") or []:
        if not isinstance(link, dict):
            continue
        src = by_id.get(_as_int(link.get("from")))
        if not src or not _allow_opinion(src["m"]):
            continue
        overrides[id(src["m"])] = "opinion" if _has_file(src["m"]) else "text"

    if overrides:
        cases = cluster_messages(
            messages,
            session_id=session_id,
            session_name=session_name,
            window_hours=window_hours,
            kind_of=lambda m: overrides.get(id(m)) or _msg_kind(m),
        )

    by_app = {}
    for c in cases:
        mid = (c.get("app") or {}).get("message_id")
        if mid not in (None, "", 0):
            by_app[str(mid)] = c
    linked = 0
    for link in data.get("links") or []:
        if not isinstance(link, dict):
            continue
        src = by_id.get(_as_int(link.get("from")))
        dst = by_id.get(_as_int(link.get("to")))
        if not src or not dst or not _allow_opinion(src["m"]):
            continue
        case = by_app.get(str(dst["m"].get("message_id") or ""))
        if not case:
            continue
        ref = _file_ref(src["m"], "opinion") if _has_file(src["m"]) else _text_ref(src["m"])
        before = len(case.get("opinions") or [])
        _attach(case, ref)
        if len(case.get("opinions") or []) > before:
            linked += 1
            warns = list(case.get("warnings") or [])
            if "Gemini 已挂上对应修改意见" not in warns:
                warns.append("Gemini 已挂上对应修改意见")
            case["warnings"] = warns
    for c in cases:
        _refresh_case(c)
    n = len(overrides) + linked
    if not n:
        return cases, "Gemini 已核对，未改规则结果"
    return cases, "Gemini 已协助定位（调整 " + str(n) + " 处）"
