"""甲 / 乙方判定。

规则：
- 个人微信里名字含 reaching 的是甲方
- 本公司人员名单及别名是乙方
- 企业微信里未命中乙方名单的人是甲方
- 个人微信里除 reaching 外默认乙方
- 申报组 / 匹配组头衔视为乙方
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .config import DATA_DIR

PARTY_PATH = DATA_DIR / "data" / "party.json"

_TITLE_RE = re.compile(r"申报.?组|匹配.?组|申报部门|申报部-|组长-|组员-|海归就业创业")


def normalize_party(data: dict | None) -> dict:
    src = data if isinstance(data, dict) else {}
    merged: dict[str, dict] = {}
    order: list[str] = []
    for item in src.get("staff") or []:
        if isinstance(item, str):
            item = {"name": item, "aliases": []}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        aliases: list[str] = []
        for raw in item.get("aliases") or []:
            alias = str(raw).strip()
            if alias and alias != name and alias not in aliases:
                aliases.append(alias)
        if key in merged:
            seen = set(merged[key]["aliases"])
            for alias in aliases:
                if alias not in seen:
                    merged[key]["aliases"].append(alias)
                    seen.add(alias)
        else:
            merged[key] = {"name": name, "aliases": aliases}
            order.append(key)
    staff = [merged[k] for k in sorted(order, key=lambda x: merged[x]["name"])]
    reaching: list[str] = []
    for raw in src.get("reachingAliases") or ["reaching"]:
        alias = str(raw).strip()
        if alias and alias not in reaching:
            reaching.append(alias)
    if not reaching:
        reaching = ["reaching"]
    return {
        "staff": staff,
        "reachingAliases": reaching,
        "titleStaffPattern": str(src.get("titleStaffPattern") or "").strip(),
    }


def load_party() -> dict:
    if not PARTY_PATH.exists():
        return {"staff": [], "reachingAliases": ["reaching"], "titleStaffPattern": ""}
    try:
        data = json.loads(PARTY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"staff": [], "reachingAliases": ["reaching"], "titleStaffPattern": ""}
    return normalize_party(data if isinstance(data, dict) else {})


def save_party(data: dict) -> None:
    PARTY_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = normalize_party(data)
    PARTY_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _staff_index.cache_clear()


def _norm(text: str) -> str:
    return re.sub(r"[\s()（）\-—_@·]+", "", (text or "")).lower()


def _title_re(pattern: str) -> re.Pattern | None:
    text = str(pattern or "").strip()
    if not text:
        return _TITLE_RE
    try:
        return re.compile(text)
    except re.error:
        return _TITLE_RE


def _staff_rows(data: dict) -> tuple[list[tuple[str, str, str]], re.Pattern | None]:
    rows: list[tuple[str, str, str]] = []
    for item in data.get("staff") or []:
        if isinstance(item, str):
            item = {"name": item, "aliases": []}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        aliases = [name] + [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()]
        for alias in aliases:
            rows.append((_norm(alias), alias, name))
    rows.sort(key=lambda r: len(r[0]), reverse=True)
    return rows, _title_re(str(data.get("titleStaffPattern") or ""))


@lru_cache(maxsize=1)
def _staff_index() -> tuple[list[tuple[str, str, str]], re.Pattern | None]:
    return _staff_rows(load_party())


def is_reaching(sender: str, data: dict | None = None) -> bool:
    party = data if isinstance(data, dict) else load_party()
    blob = (sender or "").lower()
    for alias in party.get("reachingAliases") or ["reaching"]:
        if str(alias).lower() in blob:
            return True
    return False


def match_staff(sender: str, data: dict | None = None) -> str:
    raw = sender or ""
    key = _norm(raw)
    if not key:
        return ""
    rows, title_re = _staff_rows(data) if isinstance(data, dict) else _staff_index()
    for nkey, alias, name in rows:
        if not nkey:
            continue
        if key == nkey or nkey in key:
            if len(nkey) >= 2:
                return name
        if alias and alias in raw:
            return name
    if title_re and title_re.search(raw):
        return raw
    return ""


def infer_platform(message: dict | None = None, session: dict | None = None) -> str:
    """企业微信默认 wecom；个人微信（wxid_ / 标注个人微信）走 wechat。"""
    for src in (
        (message or {}).get("platform"),
        (session or {}).get("platform"),
    ):
        p = str(src or "").lower()
        if p in ("wechat", "wx", "personal"):
            return "wechat"
        if p in ("wecom", "wework", "qywx"):
            return "wecom"
    for c in (message or {}).get("copies") or []:
        if not isinstance(c, dict):
            continue
        p = str(c.get("platform") or "").lower()
        if p in ("wechat", "wx", "personal"):
            return "wechat"
        kind = str(c.get("kind") or "").lower()
        if kind in ("wechat", "wx"):
            return "wechat"
    blob = " ".join([
        str((session or {}).get("display_name") or ""),
        str((session or {}).get("username") or ""),
        str((message or {}).get("sender") or ""),
    ]).lower()
    if "个人微信" in blob:
        return "wechat"
    uid = str((session or {}).get("username") or (session or {}).get("session_id") or "")
    if uid.startswith("wxid_"):
        return "wechat"
    return "wecom"


def classify(sender: str, platform: str = "wecom", data: dict | None = None) -> dict:
    sender = str(sender or "").strip()
    if is_reaching(sender, data):
        return {
            "party": "甲方",
            "reason": "个人微信 reaching 视为甲方",
            "staffName": "",
            "platform": platform,
        }
    staff = match_staff(sender, data)
    if staff:
        return {
            "party": "乙方",
            "reason": "命中本公司人员名单或组内头衔",
            "staffName": staff,
            "platform": platform,
        }
    if (platform or "wecom").lower() == "wechat":
        return {
            "party": "乙方",
            "reason": "个人微信除 reaching 外视为乙方",
            "staffName": "",
            "platform": platform,
        }
    return {
        "party": "甲方",
        "reason": "企业微信且未在本公司人员名单中",
        "staffName": "",
        "platform": platform,
    }


def party_of(sender: str, platform: str = "wecom", data: dict | None = None) -> str:
    return classify(sender, platform, data)["party"]
