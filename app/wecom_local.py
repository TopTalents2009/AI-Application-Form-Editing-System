"""看板直读企业微信解析服务已同步的 JSON，避免整文件经 HTTP 往返。"""
from __future__ import annotations
import json
import re
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()
_CACHE: dict[str, tuple] = {}
_CACHE_MAX = 24


def _safe_id(value: str) -> str:
    text = re.sub(r"[^\w.\-]+", "_", str(value or "").strip())
    return (text or "pc")[:80]


def _cfg() -> dict:
    from .config import load_config
    raw = load_config().get("wecomChat")
    return raw if isinstance(raw, dict) else {}


def _looks_synced(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if child.is_dir() and (child / "meta.json").is_file():
                return True
    except OSError:
        return False
    return False


def data_root() -> Path | None:
    cfg = _cfg()
    mode = str(cfg.get("readMode") or "auto").strip().lower()
    if mode == "http":
        return None
    explicit = str(cfg.get("dataRoot") or "").strip()
    if explicit:
        p = Path(explicit)
        if _looks_synced(p) or (mode == "local" and p.is_dir()):
            return p
        if mode == "local":
            return None
    here = Path(__file__).resolve()
    work = here.parents[3] if len(here.parents) > 3 else None
    if work and work.is_dir():
        try:
            for child in work.iterdir():
                cand = child / "export" / "synced"
                if _looks_synced(cand):
                    return cand
        except OSError:
            return None
    return None


def available() -> bool:
    return data_root() is not None


def status() -> dict:
    root = data_root()
    if not root:
        return {"ok": False, "root": "", "sources": 0, "readMode": "http"}
    items = list_sources()
    return {
        "ok": bool(items),
        "root": str(root),
        "sources": len(items),
        "readMode": "local",
    }


def has_source(source_id: str) -> bool:
    root = data_root()
    sid = _safe_id(source_id)
    return bool(root and sid and (root / sid / "meta.json").is_file())


def _read_json(path: Path, default):
    try:
        st = path.stat()
    except OSError:
        return default
    key = str(path)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    with _LOCK:
        _CACHE[key] = (st.st_mtime_ns, st.st_size, data, now)
        _evict()
    return data


def _evict():
    if len(_CACHE) <= _CACHE_MAX:
        return
    ranked = sorted(_CACHE.items(), key=lambda kv: kv[1][3] if len(kv[1]) > 3 else 0)
    for key, _row in ranked[: max(1, len(_CACHE) - _CACHE_MAX)]:
        _CACHE.pop(key, None)


def list_sources() -> list:
    root = data_root()
    if not root:
        return []
    out = []
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    for name in names:
        meta_path = root / name / "meta.json"
        if not meta_path.is_file():
            continue
        meta = _read_json(meta_path, {})
        if not isinstance(meta, dict):
            meta = {}
        sessions = _read_json(root / name / "sessions.json", [])
        if not isinstance(sessions, list):
            sessions = []
        out.append({
            "id": name,
            "kind": "remote",
            "computer_name": meta.get("computer_name") or name,
            "operator_name": meta.get("operator_name") or "",
            "username": meta.get("username") or "",
            "account_id": meta.get("account_id") or "",
            "host": meta.get("host") or "",
            "last_sync": meta.get("last_sync") or "",
            "session_count": len(sessions),
            "platform": "wecom",
        })
    out.sort(key=lambda s: str(s.get("last_sync") or ""), reverse=True)
    return out


def list_sessions(source_id: str, limit: int = 1000) -> list:
    root = data_root()
    if not root:
        return []
    rows = _read_json(root / _safe_id(source_id) / "sessions.json", [])
    if not isinstance(rows, list):
        return []
    rows = [x for x in rows if isinstance(x, dict)]
    rows.sort(key=lambda s: (str(s.get("last_time") or ""), str(s.get("synced_at") or "")), reverse=True)
    lim = max(1, min(int(limit or 1000), 5000))
    return rows[:lim]


def _msg_path(source_id: str, session_id: str) -> Path | None:
    root = data_root()
    if not root:
        return None
    path = root / _safe_id(source_id) / "messages" / (_safe_id(session_id) + ".json")
    return path if path.is_file() else None


def _filter_dates(messages: list, start_date: str, end_date: str) -> list:
    if not start_date and not end_date:
        return list(messages or [])
    kept = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        day = str(item.get("time_text") or "")[:10]
        if len(day) != 10 or day[4] != "-":
            continue
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        kept.append(item)
    return kept


def _parse_suffix(path: Path, window: int) -> list | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    try:
        with path.open("rb") as f:
            if window < size:
                f.seek(size - window)
            raw = f.read()
    except OSError:
        return None
    if window >= size:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else None
    text = None
    for skip in range(0, 6):
        try:
            text = raw[skip:].decode("utf-8")
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return None
    cut = text.find("\n")
    if cut >= 0:
        text = text[cut + 1 :]
    i = text.find("{")
    if i < 0:
        return None
    body = text[i:].strip()
    if body.endswith("]"):
        body = body[:-1].rstrip()
    if body.endswith(","):
        body = body[:-1].rstrip()
    try:
        arr = json.loads("[" + body + "]")
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list):
        return None
    return [x for x in arr if isinstance(x, dict)]


def _read_tail(path: Path, need: int) -> tuple[list, bool]:
    """只解析文件尾部，返回最后 need 条。第二条表示前面还有更早的消息。"""
    need = max(1, int(need or 1))
    try:
        size = path.stat().st_size
    except OSError:
        return [], False
    if size <= 160_000:
        data = _read_json(path, [])
        if not isinstance(data, list):
            return [], False
        rows = [x for x in data if isinstance(x, dict)]
        if len(rows) > need:
            return rows[-need:], True
        return rows, False
    window = min(size, max(48_000, need * 2800))
    while True:
        if window >= size:
            data = _read_json(path, [])
            rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
            if len(rows) > need:
                return rows[-need:], True
            return rows, False
        arr = _parse_suffix(path, window)
        if arr is None or len(arr) < need:
            window = min(size, max(window * 2, window + 200_000))
            continue
        return arr[-need:], True


def read_window(
    source_id: str,
    session_id: str,
    *,
    need: int = 80,
    start_date: str = "",
    end_date: str = "",
) -> tuple[list, bool]:
    """need<=0 表示整段会话。返回 (messages, has_older)。"""
    path = _msg_path(source_id, session_id)
    if not path:
        return [], False
    start_date = str(start_date or "")[:10]
    end_date = str(end_date or "")[:10]
    if int(need or 0) <= 0 and not start_date and not end_date:
        data = _read_json(path, [])
        rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
        return rows, False
    target = max(1, int(need or 80))
    fetch = target
    while True:
        rows, truncated = _read_tail(path, fetch)
        if start_date or end_date:
            oldest = ""
            for item in rows:
                day = str(item.get("time_text") or "")[:10]
                if len(day) == 10:
                    oldest = day
                    break
            filtered = _filter_dates(rows, start_date, end_date)
            short = len(filtered) < target and truncated and fetch < 20000
            too_new = bool(truncated and oldest and (
                (start_date and oldest > start_date) or (not start_date and end_date and oldest > end_date)
            ))
            if (short or too_new) and fetch < 20000:
                fetch = min(20000, max(fetch * 2, target))
                continue
            if len(filtered) > target:
                return filtered[-target:], True
            return filtered, bool(truncated)
        return rows, truncated


def search_session(q: str, session_id: str, *, source_id: str = "", limit: int = 80) -> list:
    qn = str(q or "").strip().lower()
    if not qn or not session_id:
        return []
    lim = max(1, min(int(limit or 80), 200))
    want = str(source_id or "").strip()
    hits = []
    for src in list_sources():
        sid = str(src.get("id") or "")
        if want and want not in ("*", "all") and sid != want:
            continue
        rows, _older = read_window(sid, session_id, need=0)
        for m in rows:
            blob = " ".join([
                str(m.get("text") or ""),
                str(m.get("sender") or ""),
                str(m.get("attachment_name") or ""),
            ]).lower()
            if qn in blob:
                hits.append(m)
                if len(hits) >= lim:
                    return hits
    return hits
