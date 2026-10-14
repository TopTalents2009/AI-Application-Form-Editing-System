from __future__ import annotations

from collections import Counter, defaultdict

from .agent_extract import filename_of, is_delivery_name, is_system, summary, text_of
from .agent_review import review_map
from .agent_router import route_with_session
from .party import classify, infer_platform

_CACHE: dict[str, dict] = {}
_CACHE_MAX = 8


def clear_cache() -> None:
    _CACHE.clear()


def _key(source_id: str, session_id: str) -> str:
    return f"{source_id or '*'}|{session_id}"


def _slim(message: dict) -> dict:
    return {
        "time": message.get("time_text") or "",
        "sender": message.get("sender") or "",
        "senderId": message.get("sender_id") or "",
        "text": text_of(message)[:500],
        "filename": filename_of(message),
        "msgType": message.get("msg_type_label") or "",
        "messageId": message.get("message_id"),
        "hasAttachment": bool(message.get("has_attachment") or filename_of(message)),
        "mediaUrl": message.get("media_url") or "",
    }


def replay_messages(messages: list[dict], *, session: dict | None = None) -> dict:
    hits: list[dict] = []
    stream: list[dict] = []
    sender_stats: Counter[str] = Counter()
    agent_stats: Counter[str] = Counter()
    party_stats: Counter[str] = Counter()
    context: list[dict] = []
    sess = session or {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        platform = infer_platform(msg, sess)
        party = classify(str(msg.get("sender") or ""), platform)
        party_stats[party["party"]] += 1
        routed = route_with_session(msg, context, sess)
        context.append(msg)
        if len(context) > 12:
            context = context[-12:]
        item = {
            "message": _slim(msg),
            "party": party,
            "ackOnly": bool(routed.get("ackOnly")),
            "skipped": bool(routed.get("skipped")),
            "skipReason": routed.get("skipReason") or "",
            "triggers": routed.get("triggers") or [],
            "agentIds": [t["agentId"] for t in (routed.get("triggers") or [])],
        }
        stream.append(item)
        if party["party"] != "甲方":
            continue
        if is_system(msg) and not routed.get("triggers"):
            continue
        sender_stats[str(msg.get("sender") or "")] += 1
        for trig in routed.get("triggers") or []:
            agent_stats[trig["agentId"]] += 1
        hit = dict(item)
        hit["index"] = len(hits)
        hits.append(hit)

    return {
        "msg_count": len(messages),
        "jiafang_count": len(hits),
        "party_stats": dict(party_stats),
        "sender_stats": sender_stats.most_common(40),
        "agent_stats": agent_stats.most_common(),
        "hits": hits,
        "stream": stream,
    }


async def replay_session(
    session_id: str = "",
    source_id: str = "",
    name_hint: str = "",
    force: bool = False,
) -> dict:
    from . import wecom_client as W

    sid = str(session_id or "").strip()
    if not sid:
        raise KeyError("未找到会话")
    src = str(source_id or "").strip()
    cache_key = _key(src, sid)
    if not force and cache_key in _CACHE:
        return _CACHE[cache_key]

    data = await W.list_merged_messages(sid, source_id=src, full=True)
    messages = [m for m in (data.get("items") or []) if isinstance(m, dict)]
    name = str(data.get("display_name") or name_hint or sid)
    session = {
        "session_id": sid,
        "username": sid,
        "display_name": name,
        "source_id": src or "*",
        "is_delivery": is_delivery_name(name),
        "is_group": True,
    }
    computed = replay_messages(messages, session=session)
    result = {
        "source_id": src or "*",
        "session_id": sid,
        "name": name,
        "is_delivery": is_delivery_name(name),
        **computed,
    }
    _CACHE[cache_key] = result
    if len(_CACHE) > _CACHE_MAX:
        oldest = next(iter(_CACHE))
        if oldest != cache_key:
            _CACHE.pop(oldest, None)
    return result


def suggested_traces(result: dict, n: int = 6) -> list[dict]:
    grouped: dict[str, list] = defaultdict(list)
    for hit in result.get("hits") or []:
        sender = hit["message"]["sender"]
        grouped[sender].append(hit)
    ranked = sorted(grouped.items(), key=lambda kv: -len(kv[1]))
    out = []
    for sender, items in ranked[:n]:
        agents = Counter()
        for hit in items:
            agents.update(hit.get("agentIds") or [])
        first = items[0]["message"]
        out.append({
            "sender": sender,
            "count": len(items),
            "first": first.get("time") or "",
            "last": items[-1]["message"].get("time") or "",
            "agents": agents.most_common(),
            "preview": summary(first.get("text") or "", first.get("filename") or ""),
        })
    return out


def trace_sender(result: dict, sender: str) -> list[dict]:
    return [h for h in (result.get("hits") or []) if h["message"]["sender"] == sender]


def attach_reviews(result: dict, items: list[dict]) -> list[dict]:
    reviews = review_map(str(result.get("source_id") or ""), str(result.get("session_id") or ""))
    for hit in items:
        mid = str((hit.get("message") or {}).get("messageId") or "")
        hit["review"] = reviews.get(mid) or {}
    return items
