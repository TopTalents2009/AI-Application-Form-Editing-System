"""告警规则3：群聊重要问题关键词。"""
from __future__ import annotations
import re

DEFAULT_ISSUE_KEYWORDS = [
    "低级错误",
    "怎么回事",
    "什么时候",
    "？",
    "到底",
    "尽快",
    "急",
    "人呢？",
    "今天能交/给我吗",
    "涉嫌造假",
    "重复",
    "效率差",
]

_URGENT_NEG = re.compile(r"急诊|急于|急救|急性")
_QMARK_ONLY = re.compile(r"^[?？\s]+$")
_QMARK_RUN = re.compile(r"[?？]{2,}")


def parse_issue_keywords(val) -> list:
    if isinstance(val, list):
        parts = val
    else:
        parts = re.split(r"[\n]+", str(val or ""))
    out = []
    for x in parts:
        s = str(x or "").strip()
        if not s:
            continue
        if s not in out:
            out.append(s[:40])
        if len(out) >= 80:
            break
    return out


def expand_keyword(kw: str) -> list:
    s = str(kw or "").strip()
    if not s:
        return []
    if s in ("?", "？"):
        return ["？"]
    if "/" not in s:
        return [s]
    parts = [p.strip() for p in s.split("/") if p.strip()]
    if len(parts) == 2 and parts[0].startswith("今天") and not parts[1].startswith("今天"):
        return [parts[0], "今天" + parts[1]]
    out = []
    for p in parts:
        if p not in out:
            out.append(p)
    return out


def _hit_token(text: str, token: str) -> bool:
    if not token:
        return False
    if token in ("?", "？"):
        return bool(_QMARK_ONLY.match(text) or _QMARK_RUN.search(text))
    if token == "急":
        return "急" in _URGENT_NEG.sub("", text)
    if token.endswith("？") or token.endswith("?"):
        stem = token.rstrip("？?")
        return stem in text if stem else _hit_token(text, "？")
    return token in text


def match_keywords(text: str, keywords: list | None = None) -> list:
    raw = str(text or "").strip()
    if not raw:
        return []
    kws = parse_issue_keywords(keywords if keywords is not None else DEFAULT_ISSUE_KEYWORDS)
    if not kws:
        kws = list(DEFAULT_ISSUE_KEYWORDS)
    hits = []
    for kw in kws:
        for tok in expand_keyword(kw):
            if _hit_token(raw, tok):
                if kw not in hits:
                    hits.append(kw)
                break
    return hits


def scan_messages(messages: list, keywords: list | None = None) -> list:
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        text = str(m.get("text") or m.get("snippet") or "").strip()
        if not text:
            continue
        kws = match_keywords(text, keywords)
        if not kws:
            continue
        out.append({
            "messageId": m.get("message_id") or 0,
            "sender": str(m.get("sender") or ""),
            "time": str(m.get("time_text") or m.get("time") or ""),
            "text": text,
            "keywords": kws,
        })
    return out
