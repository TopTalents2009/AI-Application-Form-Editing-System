"""值班判断：Gemini 按 Jev 式固定题目识别修改意见。配申报书仍走原来的 Gemini 拆解。"""
from __future__ import annotations
import json
from .config import LLM_TIMEOUT_CLASSIFY, resolve_watch_llm
from .llm import LlmError, chat, extract_json_lenient
from .wecom_cases import _CHATTER, _msg_kind, is_opinion_text
from .wecom_jev import judgment_prompt
from .wecom_split_llm import _allow_opinion, _as_int, _catalog


def rule_opinion_ids(messages: list) -> list:
    """规则层认为像修改意见的消息（文档或文本）。"""
    hits = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        kind = _msg_kind(m)
        if kind in ("opinion", "text"):
            hits.append(m)
            continue
        text = str(m.get("text") or m.get("snippet") or "").strip()
        if text and len(text) >= 8 and not _CHATTER.match(text) and is_opinion_text(text):
            hits.append(m)
    return hits


def parse_watch_json(text: str) -> dict:
    data = extract_json_lenient(text)
    if not isinstance(data, dict):
        raise ValueError("值班模型返回无法解析")
    raw_ops = data.get("opinions") if isinstance(data.get("opinions"), list) else []
    opinions = []
    for row in raw_ops:
        if isinstance(row, dict):
            nid = _as_int(row.get("id"))
            conf = str(row.get("confidence") or "medium").strip().lower()
        else:
            nid = _as_int(row)
            conf = "medium"
        if nid is None:
            continue
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        opinions.append({"id": nid, "confidence": conf})
    has = bool(data.get("hasOpinion")) or bool(opinions)
    return {
        "hasOpinion": has,
        "opinions": opinions,
        "reason": str(data.get("reason") or "").strip()[:200],
        "pairReady": bool(data.get("pairReady") or data.get("pair_ready")),
    }


async def detect_opinions(messages: list) -> dict:
    """看小窗口消息，返回是否有修改意见及对应原消息。"""
    cat = _catalog(messages)
    empty = {
        "hasOpinion": False,
        "opinions": [],
        "reason": "",
        "note": "",
        "error": "",
        "fallback": False,
        "engine": "",
        "pairReady": False,
    }
    if not cat:
        return empty
    slim = [{
        "id": r["id"],
        "time": r["time"],
        "sender": r["sender"],
        "filename": r["filename"],
        "text": r["text"],
        "rule": r["rule"],
    } for r in cat]
    try:
        prof = resolve_watch_llm()
    except ValueError as e:
        return {**empty, "error": str(e)}
    try:
        resp = await chat(
            [{"role": "user", "content": judgment_prompt(slim)}],
            json_mode=True,
            timeout_s=min(float(prof.get("timeoutSec") or 60), LLM_TIMEOUT_CLASSIFY),
            profile=prof,
            retries=2,
            apply_profile_timeout=False,
        )
        parsed = parse_watch_json(resp.get("content") or "")
    except (LlmError, ValueError, TypeError, json.JSONDecodeError) as e:
        hits = rule_opinion_ids(messages)
        if hits:
            return {
                "hasOpinion": True,
                "opinions": [{"message": m, "confidence": "medium"} for m in hits[:12]],
                "reason": "值班模型失败，按规则识别到修改意见",
                "note": str(e)[:160],
                "error": "",
                "fallback": True,
                "engine": "rules",
            }
        return {**empty, "error": "值班模型失败：" + str(e)[:160]}

    by_id = {int(r["id"]): r for r in cat}
    out = []
    for row in parsed.get("opinions") or []:
        hit = by_id.get(row["id"])
        if not hit or not _allow_opinion(hit["m"]):
            continue
        out.append({"message": hit["m"], "confidence": row["confidence"], "id": row["id"]})
    has = bool(parsed.get("hasOpinion") and out)
    return {
        "hasOpinion": has,
        "opinions": out,
        "reason": parsed.get("reason") or "",
        "note": "",
        "error": "",
        "fallback": False,
        "engine": "gemini",
        "pairReady": bool(parsed.get("pairReady")),
    }


async def probe_watch() -> dict:
    """发一条极短 JSON，检测值班用的 Gemini。"""
    import time
    t0 = time.monotonic()
    try:
        prof = resolve_watch_llm()
    except ValueError as e:
        return {"ok": False, "error": str(e), "ms": 0, "model": ""}
    try:
        r = await chat(
            [{"role": "user", "content": '只输出 JSON：{"ok":true}'}],
            json_mode=True,
            timeout_s=25,
            profile=prof,
            retries=1,
            apply_profile_timeout=False,
        )
        text = (r.get("content") or "").strip()
        return {
            "ok": True,
            "ms": int((time.monotonic() - t0) * 1000),
            "model": prof.get("id") or "",
            "detail": ("已连通，回复「" + text[:40] + "」") if text else "已连通",
        }
    except Exception as e:
        return {
            "ok": False,
            "ms": int((time.monotonic() - t0) * 1000),
            "model": prof.get("id") or "",
            "error": str(e)[:240],
        }
