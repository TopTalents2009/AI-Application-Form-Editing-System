"""值班扫描日志：每轮扫描落盘，供管理后台查看。"""
from __future__ import annotations
import json
import secrets
from datetime import datetime
from pathlib import Path
from .config import DATA_DIR, atomic_replace

_STORE_DIR = DATA_DIR / "data" / "wecom_watch_logs"
_INDEX_PATH = _STORE_DIR / "index.json"
_MAX_INDEX = 300


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3)


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


def save_scan_run(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("扫描日志无效")
    rid = _new_id()
    meta = {
        "id": rid,
        "at": str(payload.get("at") or _now()),
        "trigger": str(payload.get("trigger") or "auto"),
        "ok": bool(payload.get("ok", True)),
        "groupCount": int(payload.get("groupCount") or 0),
        "scannedCount": int(payload.get("scannedCount") or 0),
        "createdCount": int(payload.get("createdCount") or 0),
        "pendingCount": int(payload.get("pendingCount") or 0),
        "skippedCount": int(payload.get("skippedCount") or 0),
        "durationMs": int(payload.get("durationMs") or 0),
        "summary": str(payload.get("summary") or "")[:200],
        "error": str(payload.get("error") or "")[:200],
    }
    full = dict(payload)
    full["id"] = rid
    full["at"] = meta["at"]
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _STORE_DIR / (rid + ".json")
    path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = _load_index()
    rows.insert(0, meta)
    _save_index(rows[:_MAX_INDEX])
    return meta


def list_scan_runs(*, limit: int = 40) -> dict:
    lim = max(1, min(int(limit or 40), 100))
    rows = _load_index()
    return {"items": rows[:lim], "count": len(rows[:lim]), "total": len(rows)}


def get_scan_run(record_id: str) -> dict | None:
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
