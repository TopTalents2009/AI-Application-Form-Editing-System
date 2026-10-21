"""Search decoded WeCom chat messages on the server."""
import os
import re
from typing import List

from shared import synced_store

_EDGE_PUNCT_RE = re.compile(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff]+$", re.UNICODE)
_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "id", "in", "is", "name",
    "of", "on", "or", "the", "to", "with",
}


def tokenize(query: str) -> List[str]:
    parts = []
    for item in str(query or "").replace("\u3000", " ").split():
        text = _EDGE_PUNCT_RE.sub("", item).strip().casefold()
        if text:
            parts.append(text)
    parts = parts[:8]
    meaningful = [item for item in parts if item not in _STOPWORDS]
    return meaningful or parts


def haystack_for(item: dict, session_name: str = "") -> str:
    parts = [
        item.get("text") or "",
        item.get("sender") or "",
        item.get("sender_id") or "",
        item.get("attachment_name") or "",
        session_name or "",
    ]
    return "\n".join(parts).casefold()


def match_score(haystack: str, tokens: List[str]) -> int:
    if not tokens:
        return 0
    return sum(1 for token in tokens if token in haystack)


def matches(haystack: str, tokens: List[str]) -> bool:
    if not tokens:
        return False
    return match_score(haystack, tokens) == len(tokens)


def snippet(text: str, tokens: List[str], radius: int = 36) -> str:
    raw = (text or "").replace("\n", " ").strip()
    if not raw:
        return ""
    lowered = raw.casefold()
    idx = -1
    width = 0
    for token in tokens:
        found = lowered.find(token)
        if found >= 0:
            idx = found
            width = len(token)
            break
    if idx < 0:
        return raw[:80]
    start = max(0, idx - radius)
    end = min(len(raw), idx + width + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(raw) else ""
    return prefix + raw[start:end] + suffix


def _hit(source_id: str, source_name: str, session_id: str, session_name: str, item: dict, tokens: List[str]) -> dict:
    text = item.get("text") or ""
    return {
        "source_id": source_id,
        "source_name": source_name,
        "session_id": session_id,
        "session_name": session_name or session_id,
        "time_text": item.get("time_text") or "",
        "sender": item.get("sender") or "",
        "sender_id": item.get("sender_id") or "",
        "text": text,
        "snippet": snippet(text, tokens) or snippet(item.get("attachment_name") or "", tokens),
        "message_id": int(item.get("message_id") or 0),
        "msg_type": int(item.get("msg_type") or 0),
        "attachment_name": item.get("attachment_name") or "",
        "media_url": item.get("media_url") or "",
    }


def _pick(hits: List[dict], tokens: List[str], limit: int, strip: bool = False) -> List[dict]:
    if not hits:
        return []
    needed = len(tokens)
    full = [row for row in hits if int(row.get("score") or 0) >= needed]
    chosen = full or hits
    chosen.sort(
        key=lambda row: (int(row.get("score") or 0), row.get("time_text") or ""),
        reverse=True,
    )
    out = []
    for row in chosen[:limit]:
        item = dict(row)
        if strip:
            item.pop("score", None)
        out.append(item)
    return out


def _consider(item: dict, tokens: List[str], session_name: str, source_id: str,
              source_name: str, session_id: str, bucket: List[dict]) -> None:
    score = match_score(haystack_for(item, session_name), tokens)
    if not score:
        return
    hit = _hit(source_id, source_name, session_id, session_name, item, tokens)
    hit["score"] = score
    bucket.append(hit)


def search_synced(
    source_id: str,
    tokens: List[str],
    session_id: str = "",
    limit: int = 50,
) -> List[dict]:
    source = synced_store.get_source(source_id) or {}
    source_name = source.get("operator_name") or source.get("computer_name") or source_id
    sessions = synced_store.list_sessions(source_id)
    if session_id:
        sessions = [item for item in sessions if str(item.get("username") or "") == session_id]
        if not sessions:
            sessions = [{"username": session_id, "display_name": session_id}]
    hits = []
    for session in sessions:
        sid = str(session.get("username") or "")
        if not sid:
            continue
        name = session.get("display_name") or sid
        path = os.path.join(
            synced_store._source_dir(source_id),
            "messages",
            synced_store._safe_id(sid) + ".json",
        )
        for item in synced_store._read_json(path, []):
            _consider(item, tokens, name, source_id, source_name, sid, hits)
    return _pick(hits, tokens, limit, strip=False)


def search_local(
    platform,
    tokens: List[str],
    session_id: str = "",
    limit: int = 50,
    source_id: str = "local",
    source_name: str = "本机",
) -> List[dict]:
    hits = []
    if session_id:
        name = session_id
        for session in platform.list_sessions(limit=2000):
            if session.username == session_id:
                name = session.display_name or session_id
                break
        for msg in platform.query_messages(session_id, limit=20000):
            _consider(_message_item(msg), tokens, name, source_id, source_name, session_id, hits)
        return _pick(hits, tokens, limit, strip=False)

    for session in platform.list_sessions(limit=2000):
        sid = session.username
        name = session.display_name or sid
        for msg in platform.query_messages(sid, limit=1200):
            _consider(_message_item(msg), tokens, name, source_id, source_name, sid, hits)
    return _pick(hits, tokens, limit, strip=False)


def _message_item(msg) -> dict:
    return {
        "time_text": msg.time.strftime("%Y-%m-%d %H:%M") if msg.time else (msg.time_text or ""),
        "sender": msg.sender,
        "sender_id": msg.sender_id,
        "text": msg.text,
        "message_id": msg.message_id,
        "msg_type": msg.msg_type,
        "attachment_name": msg.attachment_name,
        "media_url": getattr(msg, "media_url", "") or "",
    }


def search(
    query: str,
    source_id: str = "",
    session_id: str = "",
    limit: int = 50,
    local_platform=None,
) -> List[dict]:
    tokens = tokenize(query)
    if not tokens:
        return []
    source_id = str(source_id or "").strip()
    session_id = str(session_id or "").strip()
    limit = max(1, min(int(limit or 50), 200))
    if source_id == "local":
        if local_platform is None:
            return []
        return _pick(search_local(local_platform, tokens, session_id, limit), tokens, limit, strip=True)
    if source_id:
        return _pick(search_synced(source_id, tokens, session_id, limit), tokens, limit, strip=True)

    hits = []
    if local_platform is not None:
        hits.extend(search_local(local_platform, tokens, session_id, min(limit, 80)))
    for src in synced_store.list_sources():
        hits.extend(search_synced(src["id"], tokens, session_id, min(limit, 80)))
    return _pick(hits, tokens, limit, strip=True)
