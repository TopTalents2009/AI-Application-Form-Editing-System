"""AI 解读记录：落盘与列表查询。"""
from __future__ import annotations
import json
import secrets
from datetime import datetime
from pathlib import Path
from .config import DATA_DIR, atomic_replace

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
