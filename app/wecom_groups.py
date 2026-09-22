"""各群记录：关注群（值班白名单）读写。"""
from __future__ import annotations
from .config import CONFIG_PATH, _read_config_file, _write_json, load_config
from .wecom_client import match_allowlist


def list_watch_groups() -> list:
    return list(load_config().get("wecomChatGroups") or [])


def group_is_watched(display_name: str, session_id: str = "") -> bool:
    groups = list_watch_groups()
    if not groups:
        return False
    return match_allowlist({
        "username": str(session_id or ""),
        "display_name": str(display_name or ""),
    }, groups)


def _clean_groups(groups: list) -> list:
    out = []
    for x in groups or []:
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def save_watch_groups(groups: list) -> dict:
    cleaned = _clean_groups(groups)
    raw, err = _read_config_file()
    if err and CONFIG_PATH.exists():
        raise ValueError(err)
    if not isinstance(raw, dict):
        raw = {}
    blob = raw.get("wecomChat") if isinstance(raw.get("wecomChat"), dict) else {}
    blob["groups"] = cleaned
    raw["wecomChat"] = blob
    _write_json(CONFIG_PATH, raw)
    return {"ok": True, "groups": cleaned, "count": len(cleaned)}


def toggle_watch_group(display_name: str, session_id: str = "", *, watch: bool) -> dict:
    name = str(display_name or "").strip()
    sid = str(session_id or "").strip()
    if not name and not sid:
        raise ValueError("缺少群名或 session_id")
    groups = list_watch_groups()
    if watch:
        for token in (name, sid):
            if token and token not in groups:
                groups.append(token)
    else:
        drop = {name, sid}
        groups = [g for g in groups if g not in drop]
    return save_watch_groups(groups)
