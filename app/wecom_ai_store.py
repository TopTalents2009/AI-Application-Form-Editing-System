"""AI 解读记录：落盘与列表查询。"""
from __future__ import annotations
import json
import re
import secrets
from datetime import datetime
from pathlib import Path
from .config import DATA_DIR, atomic_replace
from .wecom_ai_bind import count_bindings

_STORE_DIR = DATA_DIR / "data" / "wecom_ai_reads"
_INDEX_PATH = _STORE_DIR / "index.json"
_MAX_INDEX = 200


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def _load_index() -> list:
    if not _INDEX_PATH.exists():
        return []
    try:
        rows = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _save_index(rows: list):
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _INDEX_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, _INDEX_PATH)


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3)


def save_ai_read(payload: dict, *, user: str = "") -> dict:
    if not isinstance(payload, dict):
        raise ValueError("记录内容无效")
    rid = _new_id()
    op = payload.get("opinion") if isinstance(payload.get("opinion"), dict) else {}
    sumo = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    scope = str(payload.get("scope") or "session")
    has_op = bool(payload.get("hasOpinion")) if "hasOpinion" in payload else bool(op.get("hasOpinion"))
    if scope == "watch":
        sess_name = "关注群×" + str(payload.get("groupCount") or 0)
    else:
        sess_name = str(payload.get("session_name") or "")
    meta = {
        "id": rid,
        "at": _now(),
        "user": str(user or "").strip(),
        "scope": scope,
        "session_id": str(payload.get("session_id") or ""),
        "session_name": sess_name,
        "start_date": str(payload.get("start_date") or ""),
        "end_date": str(payload.get("end_date") or ""),
        "messageCount": int(payload.get("messageCount") or 0),
        "groupCount": int(payload.get("groupCount") or 0),
        "hasOpinion": has_op,
        "readyCount": int(payload.get("readyCount") or 0),
        "model": str(payload.get("model") or ""),
        "summaryText": str(sumo.get("summary") or "")[:160],
        "bindCount": count_bindings(payload.get("bindPrompts") if isinstance(payload.get("bindPrompts"), dict) else {}),
    }
    full = dict(payload)
    full["id"] = rid
    full["at"] = meta["at"]
    full["user"] = meta["user"]
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _STORE_DIR / (rid + ".json")
    path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = _load_index()
    rows.insert(0, meta)
    _save_index(rows[:_MAX_INDEX])
    return meta


def list_ai_reads(*, session_id: str = "", limit: int = 40) -> dict:
    lim = max(1, min(int(limit or 40), 100))
    sid = str(session_id or "").strip()
    rows = _load_index()
    if sid:
        rows = [r for r in rows if isinstance(r, dict) and str(r.get("session_id") or "") == sid]
    return {"items": rows[:lim], "count": len(rows[:lim]), "total": len(rows)}


def latest_ai_read(session_id: str = "") -> dict | None:
    sid = str(session_id or "").strip()
    rows = _load_index()
    for r in rows:
        if not isinstance(r, dict):
            continue
        if sid and str(r.get("session_id") or "") != sid:
            continue
        got = get_ai_read(str(r.get("id") or ""))
        if got:
            return got
    return None


def _file_base(name: str) -> str:
    s = str(name or "").strip().lower()
    s = re.sub(r"^\[.*?\]\s*", "", s)
    s = re.sub(r"^【.*?】\s*", "", s)
    s = s.replace("\\", "/")
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    return s


def _item_mids(it: dict) -> set[str]:
    out: set[str] = set()
    mid = str(it.get("messageId") or it.get("message_id") or "").strip()
    if mid:
        out.add(mid)
    for c in it.get("copies") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("message_id") or c.get("messageId") or "").strip()
        if cid:
            out.add(cid)
    return out


def ai_read_file_items(payload: dict | None) -> list[dict]:
    out: list[dict] = []
    data = payload if isinstance(payload, dict) else {}
    ft = data.get("fileTypes") if isinstance(data.get("fileTypes"), dict) else {}
    for it in ft.get("items") or []:
        if isinstance(it, dict):
            out.append(it)
    for g in data.get("groups") or []:
        if not isinstance(g, dict):
            continue
        gft = g.get("fileTypes") if isinstance(g.get("fileTypes"), dict) else {}
        for it in gft.get("items") or []:
            if isinstance(it, dict):
                row = dict(it)
                if not row.get("session_name"):
                    row["session_name"] = g.get("session_name") or ""
                out.append(row)
    for c in data.get("cases") or []:
        if not isinstance(c, dict):
            continue
        app = c.get("app") if isinstance(c.get("app"), dict) else c
        if app:
            out.append(app)
        for op in c.get("opinions") or []:
            if isinstance(op, dict):
                out.append(op)
    binds = data.get("bindPrompts") if isinstance(data.get("bindPrompts"), dict) else {}
    for block in binds.values():
        if not isinstance(block, dict):
            continue
        for t in block.get("talents") or []:
            if not isinstance(t, dict):
                continue
            for v in t.get("versions") or []:
                if isinstance(v, dict):
                    out.append(v)
                    for op in v.get("opinions") or []:
                        if isinstance(op, dict):
                            out.append(op)
        for op in block.get("unboundOpinions") or []:
            if isinstance(op, dict):
                out.append(op)
    return out


def match_ai_read_file(payload: dict | None, *, message_id: str = "", filename: str = "") -> dict | None:
    mid = str(message_id or "").strip()
    name = _file_base(filename)
    items = ai_read_file_items(payload)
    if mid:
        for it in items:
            if mid in _item_mids(it):
                return it
    if name:
        for it in items:
            if _file_base(str(it.get("filename") or "")) == name:
                return it
    return None


def get_ai_read(record_id: str) -> dict | None:
    rid = str(record_id or "").strip()
    if not rid or "/" in rid or "\\" in rid:
        return None
    path = _STORE_DIR / (rid + ".json")
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None
