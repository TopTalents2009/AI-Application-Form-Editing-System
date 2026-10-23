"""本机维护的使用人姓名：按 IP / 电脑名覆盖助手上报的 Windows 用户名。"""
from __future__ import annotations

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIAS_FILE = os.path.join(PROJECT_ROOT, "export", "operator_aliases.json")


def _empty() -> dict:
    return {"by_host": {}, "by_computer": {}}


def load_aliases() -> dict:
    data = _empty()
    if not os.path.isfile(ALIAS_FILE):
        return data
    try:
        with open(ALIAS_FILE, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError, TypeError):
        return data
    if not isinstance(raw, dict):
        return data
    for key in ("by_host", "by_computer"):
        blob = raw.get(key)
        if isinstance(blob, dict):
            data[key] = {
                str(k).strip(): str(v).strip()
                for k, v in blob.items()
                if str(k).strip() and str(v).strip()
            }
    return data


def save_aliases(data: dict) -> None:
    payload = _empty()
    if isinstance(data, dict):
        for key in ("by_host", "by_computer"):
            blob = data.get(key)
            if isinstance(blob, dict):
                payload[key] = {
                    str(k).strip(): str(v).strip()
                    for k, v in blob.items()
                    if str(k).strip() and str(v).strip()
                }
    folder = os.path.dirname(ALIAS_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = ALIAS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, ALIAS_FILE)


def set_alias(*, host: str = "", computer: str = "", name: str = "") -> dict:
    name = str(name or "").strip()
    host = str(host or "").strip()
    computer = str(computer or "").strip()
    data = load_aliases()
    if name and host:
        data["by_host"][host] = name
    if name and computer:
        data["by_computer"][computer] = name
    save_aliases(data)
    return data


def resolve_operator_name(host: str = "", computer: str = "", current: str = "") -> str:
    host = str(host or "").strip()
    computer = str(computer or "").strip()
    current = str(current or "").strip()
    data = load_aliases()
    if host and data["by_host"].get(host):
        return data["by_host"][host]
    if computer and data["by_computer"].get(computer):
        return data["by_computer"][computer]
    return current
