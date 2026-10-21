"""看板直读企业微信解析服务已同步的 JSON，避免整文件经 HTTP 往返。"""
from __future__ import annotations
import json
import re
import threading
import time
from collections import Counter
from pathlib import Path

from .party import sender_of

_LOCK = threading.Lock()
_CACHE: dict[str, tuple] = {}
_CACHE_MAX = 64
_NAME_CACHE: dict[str, str] = {}
_SELF_CACHE: dict[str, set] = {}
_ID_NAME = re.compile(r"^[MRS]:", re.I)
_DIGITS_ID = re.compile(r"^\d{8,}$")
_JSON_NAME = re.compile(r'"name"\s*:\s*"([^"]{1,40})"')
_SKIP_JSON_NAME = {"user", "avatar", "name", "vid", "userid", "username"}
_JOIN_SHARE = re.compile(r"你通过(.+?)分享的链接加入了此(智能)?(表格|文档)")
_CREATED_DOC = re.compile(r"你创建了(智能)?(表格|文档)")
_EDITED_DOC = re.compile(r"编辑了(智能)?(表格|文档)")
_GENERIC_DOC = {"智能表格", "智能文档"}


def looks_like_session_id(value: str) -> bool:
    """企业微信内部会话号（M:/R:/S: 或纯数字），不能当展示名。"""
    s = str(value or "").strip()
    if not s:
        return True
    return bool(_ID_NAME.match(s) or _DIGITS_ID.match(s))


def better_session_name(cur: str, cand: str) -> str:
    c = str(cand or "").strip()
    if not c or looks_like_session_id(c):
        return str(cur or "").strip()
    k = str(cur or "").strip()
    if not k or looks_like_session_id(k):
        return c
    return c if len(c) > len(k) else k


def _is_self_name(value: str, skip: set | None = None) -> bool:
    n = str(value or "").strip()
    if not n:
        return True
    for s in skip or ():
        t = str(s or "").strip()
        if len(t) < 2:
            continue
        if n == t or t in n or (len(n) >= 2 and n in t):
            return True
    return False


def _human_name(value: str, skip: set | None = None) -> str:
    from .party import clean_sender
    s = clean_sender(value)
    if not s or looks_like_session_id(s):
        return ""
    if _is_self_name(s, skip):
        return ""
    if s.lower() in _SKIP_JSON_NAME:
        return ""
    return s


def _short_who(who: str) -> str:
    s = str(who or "").strip()
    s = re.split(r"[（(]", s, 1)[0].strip()
    return s[:30]


def doc_title_from_text(text: str) -> str:
    """企业微信智能表格/文档会话的系统提示，不是联系人备注。"""
    t = str(text or "").replace("\n", " ")
    if "已导入此文档" in t:
        return "智能文档"
    m = _JOIN_SHARE.search(t)
    if m:
        who = _short_who(m.group(1))
        kind = m.group(3) or "表格"
        if who and not looks_like_session_id(who):
            return who + "的" + kind
        return "智能文档" if kind == "文档" else "智能表格"
    m = _CREATED_DOC.search(t)
    if m:
        kind = m.group(2) or "表格"
        return "智能文档" if kind == "文档" else "智能表格"
    if _EDITED_DOC.search(t) or "加入了此表格" in t or "加入了此智能表格" in t:
        return "智能表格"
    if "智能文档" in t and ("创建" in t or "文档" in t):
        return "智能文档"
    return ""


def _pick_doc_title(votes: Counter) -> str:
    if not votes:
        return ""
    specific = [(n, c) for n, c in votes.items() if n not in _GENERIC_DOC]
    if specific:
        specific.sort(key=lambda kv: (-kv[1], -len(kv[0])))
        return specific[0][0]
    return votes.most_common(1)[0][0]


def _meta_aliases(src: dict | None) -> set:
    aliases = set()
    for key in ("operator_name", "username", "computer_name"):
        v = str((src or {}).get(key) or "").strip()
        if v and not looks_like_session_id(v):
            aliases.add(v)
    return aliases


def _source_self_aliases(source_id: str, src: dict | None = None) -> set:
    """本机操作员在消息里的自称（常比 meta.operator_name 更长）。"""
    sid = str(source_id or "")
    cached = _SELF_CACHE.get(sid)
    if cached is not None:
        return set(cached)
    aliases = _meta_aliases(src)
    counts: Counter = Counter()
    scanned = 0
    try:
        rows = list_sessions(sid, 120)
    except Exception:
        rows = []
    for it in rows or []:
        uid = str((it or {}).get("username") or "")
        if not uid.startswith("M:"):
            continue
        path = _msg_path(sid, uid)
        if not path:
            continue
        msgs, _trunc = _read_tail(path, 6)
        seen = set()
        for m in msgs or []:
            if not isinstance(m, dict):
                continue
            who = _human_name(m.get("sender") or "", None)
            if who and who.lower() not in _SKIP_JSON_NAME:
                seen.add(who)
            for n in _JSON_NAME.findall(str(m.get("text") or "")):
                hn = str(n or "").strip()
                if hn and not looks_like_session_id(hn) and hn.lower() not in _SKIP_JSON_NAME:
                    seen.add(hn)
        for x in seen:
            counts[x] += 1
        scanned += 1
        if scanned >= 20:
            break
    thresh = max(3, scanned // 4) if scanned else 99
    for name, n in counts.items():
        if n >= thresh:
            aliases.add(name)
    _SELF_CACHE[sid] = aliases
    return set(aliases)


def infer_session_display_name(session_id: str, replicas: list | None = None, sources: list | None = None) -> str:
    """单聊通讯录没解析出名时，用消息发出人 / 卡片 JSON 里的 name 补。"""
    cid = str(session_id or "").strip()
    if not cid:
        return ""
    hit = _NAME_CACHE.get(cid)
    if hit is not None:
        return hit
    src_by = {}
    for s in sources or list_sources():
        if isinstance(s, dict) and s.get("id"):
            src_by[str(s.get("id") or "")] = s
    ids = []
    for r in replicas or []:
        sid = str((r or {}).get("source_id") or (r or {}).get("id") or "")
        if sid and sid not in ids:
            ids.append(sid)
    if not ids:
        _NAME_CACHE[cid] = ""
        return ""
    counts: Counter = Counter()
    docs: Counter = Counter()
    for sid in ids:
        path = _msg_path(sid, cid)
        if not path:
            continue
        skip = _source_self_aliases(sid, src_by.get(sid) or {})
        msgs, _trunc = _read_tail(path, 40)
        for m in msgs or []:
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or "")
            title = doc_title_from_text(text)
            if title:
                docs[title] += 3 if title not in _GENERIC_DOC else 1
            who = _human_name(m.get("sender") or "", skip)
            if who:
                counts[who] += 2
            for n in _JSON_NAME.findall(text):
                hn = _human_name(n, skip)
                if hn:
                    counts[hn] += 1
    name = _pick_doc_title(docs) or (counts.most_common(1)[0][0] if counts else "")
    _NAME_CACHE[cid] = name
    if len(_NAME_CACHE) > 4000:
        for old in list(_NAME_CACHE.keys())[:800]:
            _NAME_CACHE.pop(old, None)
    return name


def fill_session_names(items: list) -> list:
    srcs = list_sources()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        uid = str(it.get("username") or it.get("session_id") or "")
        if not uid.startswith("M:"):
            continue
        name = str(it.get("display_name") or "").strip()
        if name and not looks_like_session_id(name) and name != uid:
            continue
        reps = list(it.get("replicas") or [])
        if not reps:
            sid = str(it.get("source_id") or "")
            if sid:
                reps = [{"source_id": sid}]
        guessed = infer_session_display_name(uid, reps, srcs)
        if guessed:
            it["display_name"] = guessed
    return items


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
    bundled = here.parents[1] / "parser" / "export" / "synced"
    if _looks_synced(bundled):
        return bundled
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


def source_has_session(source_id: str, session_id: str) -> bool:
    """这台电脑的缓存里有没有该会话。"""
    cid = str(session_id or "").strip()
    if not cid:
        return False
    if _msg_path(source_id, cid):
        return True
    for it in list_sessions(source_id, 0):
        uid = str((it or {}).get("username") or (it or {}).get("session_id") or "").strip()
        if uid == cid:
            return True
    return False


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
    lim_in = int(limit or 0)
    if lim_in <= 0:
        return rows
    lim = max(1, min(lim_in, 5000))
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
    if int(need or 0) <= 0:
        data = _read_json(path, [])
        rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
        if start_date or end_date:
            return _filter_dates(rows, start_date, end_date), False
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
    try:
        lim_in = int(limit or 0)
    except (TypeError, ValueError):
        lim_in = 80
    unlimited = lim_in <= 0
    lim = 0 if unlimited else max(1, min(lim_in, 10000))
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
                sender_of(m),
                str(m.get("attachment_name") or ""),
            ]).lower()
            if qn in blob:
                hits.append(m)
                if not unlimited and len(hits) >= lim:
                    return hits
    return hits
