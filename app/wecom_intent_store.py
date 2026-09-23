"""已分析的聊天意图，按会话落盘，刷新后仍能对上同一条消息。"""
from __future__ import annotations
import json
import re
import threading
from datetime import datetime

from .config import DATA_DIR, atomic_replace

_DIR = DATA_DIR / "data" / "wecom_intents"
_LOCK = threading.Lock()
_FIELDS = ("read", "readLabel", "intent", "intentLabel", "need", "action", "error")


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def _safe_session(session_id: str) -> str:
    raw = str(session_id or "").strip()
    if not raw:
        return ""
    name = re.sub(r'[<>:"/\\|?*\s]+', "_", raw).strip("._")
    return name[:160]


def _path(session_id: str):
    name = _safe_session(session_id)
    if not name:
        return None
    return _DIR / (name + ".json")


def _load(session_id: str) -> dict:
    path = _path(session_id)
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _pack(row: dict) -> dict:
    src = row if isinstance(row, dict) else {}
    out = {k: src.get(k) for k in _FIELDS}
    out["read"] = True
    out["readLabel"] = str(out.get("readLabel") or "已读")[:20]
    out["intent"] = str(out.get("intent") or "other")[:40]
    out["intentLabel"] = str(out.get("intentLabel") or "其他")[:40]
    out["need"] = str(out.get("need") or "")[:80]
    out["action"] = str(out.get("action") or "")[:80]
    out["error"] = str(out.get("error") or "")[:160]
    out["at"] = _now()
    return out


def save_many(session_id: str, pairs: list) -> int:
    sid = str(session_id or "").strip()
    path = _path(sid)
    if not path:
        return 0
    with _LOCK:
        blob = _load(sid)
        n = 0
        for key, row in pairs or []:
            k = str(key or "").strip()
            if not k or not isinstance(row, dict):
                continue
            blob[k[:240]] = _pack(row)
            n += 1
        if not n:
            return 0
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        atomic_replace(tmp, path)
        return n


def load_many(session_id: str, keys: list) -> dict:
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    with _LOCK:
        blob = _load(sid)
    out = {}
    for key in keys or []:
        k = str(key or "").strip()
        row = blob.get(k[:240]) if k else None
        if isinstance(row, dict):
            out[k] = row
    return out
