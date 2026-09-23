"""单条会话消息的意图判断。

做法对齐 Jev 聊天助手：题目固定、说明用英文、原文保持中文、一次只回一个 JSON。
题目按申报书群聊改写，不使用人际冲突那套打分。
"""
from __future__ import annotations
import asyncio
import json

from .config import LLM_TIMEOUT_CLASSIFY, resolve_gemini
from .llm import LlmError, chat, extract_json_lenient

_INTENTS = {
    "send_form": "发送申报书",
    "request_revision": "要求修改申报书",
    "supply_material": "要求补材料",
    "confirm": "确认收到",
    "chat": "闲聊",
    "other": "其他",
}

_PROMPT = """You judge one enterprise WeChat message in a Chinese talent-application (申报书) chat.
Instructions are English. Keep the message text unchanged. Do not invent facts.

Questions:
- read (yes/no): Have you read this message? Always yes after reading it.
- intent: one of send_form, request_revision, supply_material, confirm, chat, other.
  send_form = they sent or will send an application form.
  request_revision = they ask to change form text, fields, or wording.
  supply_material = they ask for a proof, scan, or missing attachment.
  confirm = 收到 / 好的 / 知道了 with no new ask.
  chat = small talk.
  other = anything else.
- need: one short Chinese phrase of what they want now. Empty if nothing.
- action: one short Chinese phrase of what the operator should do next. Empty if nothing.

Output one JSON object and nothing else:
{"read":true,"intent":"confirm","need":"","action":""}

Message:
"""


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
    }


def parse_intent_list(text: str, n: int) -> list:
    """按输入条数对齐的意图数组。缺条或坏条记为其他。"""
    s = str(text or "")
    a = s.find("[")
    if a < 0:
        raise ValueError("意图结果不是数组")
    data, _ = json.JSONDecoder().raw_decode(s, a)
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


async def judge_message(message: dict, context: list | None = None) -> dict:
    prof = resolve_gemini()
    body = _PROMPT + json.dumps(_slim(message, context or []), ensure_ascii=False)
    try:
        resp = await chat(
            [{"role": "user", "content": body}],
            json_mode=True,
            timeout_s=min(float(prof.get("timeoutSec") or 60), LLM_TIMEOUT_CLASSIFY),
            profile=prof,
            retries=0,
            apply_profile_timeout=False,
        )
        return parse_intent_json(resp.get("content") or "")
    except (LlmError, ValueError, TypeError, json.JSONDecodeError) as e:
        return {
            "read": True,
            "readLabel": "已读",
            "intent": "other",
            "intentLabel": "其他",
            "need": "",
            "action": "",
            "error": str(e)[:160],
        }


_BATCH_PROMPT = """You judge several enterprise WeChat messages in a Chinese talent-application (申报书) chat.
Instructions are English. Keep each message text unchanged. Do not invent facts.
Return a JSON array with exactly one object per input message, in the same order.
Each object:
{"read":true,"intent":"confirm","need":"","action":""}
intent is one of send_form, request_revision, supply_material, confirm, chat, other.
send_form = they sent or will send an application form.
request_revision = they ask to change form text, fields, or wording.
supply_material = they ask for a proof, scan, or missing attachment.
confirm = 收到 / 好的 / 知道了 with no new ask.
chat = small talk.
other = anything else.
need and action are one short Chinese phrase each. Use empty strings when there is nothing to do.

Messages:
"""


async def judge_page(messages: list) -> list:
    """本页消息批量判断。空消息不调用模型。每 8 条一组，最多两组同时进行。"""
    rows = [m if isinstance(m, dict) else {} for m in (messages or [])][:80]
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

    prof = resolve_gemini()
    timeout = min(float(prof.get("timeoutSec") or 90), LLM_TIMEOUT_CLASSIFY)
    sem = asyncio.Semaphore(2)

    async def one(ids: list[int]) -> None:
        payload = []
        for n, idx in enumerate(ids, 1):
            item = dict((_slim(rows[idx], []) or {}).get("message") or {})
            item["n"] = n
            payload.append(item)
        async with sem:
            try:
                resp = await chat(
                    [{"role": "user", "content": _BATCH_PROMPT + json.dumps(payload, ensure_ascii=False)}],
                    json_mode=True,
                    timeout_s=timeout,
                    profile=prof,
                    retries=0,
                    apply_profile_timeout=False,
                )
                parsed = parse_intent_list(resp.get("content") or "", len(ids))
            except (LlmError, ValueError, TypeError, json.JSONDecodeError) as e:
                err = str(e)[:160]
                parsed = [_blank_intent(error=err) for _ in ids]
        for idx, got in zip(ids, parsed):
            out[idx] = got

    await asyncio.gather(*[one(pending[i:i + 8]) for i in range(0, len(pending), 8)])
    for i, row in enumerate(out):
        if row is None:
            out[i] = _blank_intent(error="这条没有返回意图")
    return out
