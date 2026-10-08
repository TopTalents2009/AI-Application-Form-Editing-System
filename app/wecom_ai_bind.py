"""人才编号历史版本与修改意见绑定：规则 + 多套 Gemini prompt 对比。"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from .config import LLM_TIMEOUT_CLASSIFY, resolve_gemini
from .llm import LlmError, chat, extract_json_lenient
from . import matcher as M
from . import talent_app_files as T
from .wecom_cases import _fname, _has_file, _msg_kind, is_opinion_text
from .wecom_locate import extract_keys, parse_time

PROMPT_TIME_WINDOW = "time_window"
PROMPT_NAME_ID = "name_id"
PROMPT_SEMANTIC = "semantic"
PROMPT_SEMANTIC_STRICT = "semantic_strict"
DEFAULT_BIND_PROMPTS = [
    PROMPT_TIME_WINDOW,
    PROMPT_NAME_ID,
    PROMPT_SEMANTIC,
    PROMPT_SEMANTIC_STRICT,
]
_UNKNOWN_ATTACH = "未识别编号"
_COMPANY_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9·]+(?:有限责任公司|股份有限公司|有限公司|股份公司))")
_LEAD_NUM = re.compile(r"^(?:\d\s+)?(\d{4,6})(?:[^\d]|$)")
_WECOM_PREFIX = re.compile(r"^「[^\[」]*[:：\[]*(?:文件\])?")


def _msg_time(m: dict) -> str:
    return str(m.get("time_text") or m.get("time") or "").strip()


def _company_of(filename: str) -> str:
    m = _COMPANY_RE.search(str(filename or ""))
    return (m.group(1) or "").strip() if m else ""


def _person_of(app: dict) -> str:
    fname = str(app.get("filename") or "")
    for raw in list(app.get("names") or []) + [fname]:
        s = _WECOM_PREFIX.sub("", str(raw or "")).strip()
        s = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", s)
        s = re.sub(r"[_（(]?(?:美国|英文简历|申报书).*$", "", s)
        full, toks = M.accept_person_name(s)
        if full and toks:
            return str(full)
        lat = re.search(r"\b([A-Z][A-Za-z'.\-]+(?:\s+[A-Z][A-Za-z'.\-]+){1,4})\b", s)
        if lat:
            label, _ = M.accept_person_name(lat.group(1))
            if label:
                return label
    return ""


def _pick_attach_id(nums: list, text: str = "", filename: str = "") -> str:
    fname = str(filename or "")
    lead = _LEAD_NUM.match(_WECOM_PREFIX.sub("", fname).lstrip())
    if lead:
        s = lead.group(1)
        if M.is_book_num(s):
            return s
    for n in nums or []:
        s = str(n or "").strip()
        if re.fullmatch(r"\d{4,6}", s) and M.is_book_num(s):
            return s
    blob = " ".join(x for x in (fname, text) if x)
    for n in M.extract_book_nums(blob):
        if re.fullmatch(r"\d{4,6}", str(n)) and M.is_book_num(str(n)):
            return str(n)
    return _UNKNOWN_ATTACH


def _catalog_item(m: dict, role: str, session_id: str, session_name: str) -> dict:
    fname = _fname(m)
    keys = extract_keys({
        "filename": fname,
        "text": m.get("text") or m.get("snippet") or "",
        "time_text": _msg_time(m),
        "sender": m.get("sender") or "",
        "session_name": session_name,
    })
    attach_id = _pick_attach_id(keys.get("nums") or [], keys.get("text") or "", fname)
    mid = str(m.get("message_id") or "").strip()
    kind = "text" if role == "text" else "file"
    display = fname if kind == "file" else "群聊修改意见.txt"
    item = {
        "role": role,
        "kind": kind,
        "attachId": attach_id,
        "filename": display,
        "time": _msg_time(m),
        "sender": str(m.get("sender") or ""),
        "messageId": mid,
        "message_id": m.get("message_id") or 0,
        "nums": list(keys.get("nums") or [])[:6],
        "names": list(keys.get("names") or [])[:4],
        "text": str(m.get("text") or m.get("snippet") or "")[:400] if kind == "text" else "",
        "sessionId": session_id,
        "sessionName": session_name,
        "company": _company_of(display if kind == "file" else (keys.get("text") or "")),
    }
    if kind == "file":
        item["person"] = _person_of(item)
    return item


def extract_catalog(messages: list, *, session_id: str = "", session_name: str = "") -> dict:
    """从消息抽出申报书与意见目录。"""
    apps: list[dict] = []
    opinions: list[dict] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        kind = _msg_kind(m)
        if kind == "app" and _has_file(m):
            apps.append(_catalog_item(m, "app", session_id, session_name))
        elif kind == "opinion" and _has_file(m):
            opinions.append(_catalog_item(m, "opinion", session_id, session_name))
        elif kind == "text" or (not _has_file(m) and is_opinion_text(m.get("text") or m.get("snippet") or "")):
            opinions.append(_catalog_item(m, "text", session_id, session_name))
    apps.sort(key=lambda x: (parse_time(x.get("time")) or datetime.min, x.get("messageId") or ""))
    opinions.sort(key=lambda x: (parse_time(x.get("time")) or datetime.min, x.get("messageId") or ""))
    return {"apps": apps, "opinions": opinions}


def _version_window_end(v_dt: datetime, next_dt: datetime | None, window_hours: int) -> datetime:
    if next_dt and next_dt > v_dt:
        return next_dt
    try:
        hours = int(window_hours)
    except (TypeError, ValueError):
        hours = 48
    if hours <= 0:
        return datetime.max
    return v_dt + timedelta(hours=max(1, hours))


def bind_opinions_time_window(
    versions: list[dict],
    opinions: list[dict],
    *,
    window_hours: int = 48,
) -> tuple[list[dict], list[dict]]:
    """纯函数：意见落在某版申报书之后、下一版之前（末版用时间窗）才绑定。"""
    vers = [dict(v) for v in versions or []]
    for v in vers:
        v["opinions"] = []
    unbound: list[dict] = []
    for op in sorted(opinions or [], key=lambda x: (parse_time(x.get("time")) or datetime.min, x.get("messageId") or "")):
        op_dt = parse_time(op.get("time"))
        if not op_dt:
            unbound.append(op)
            continue
        hit = False
        for i, ver in enumerate(vers):
            v_dt = parse_time(ver.get("time"))
            if not v_dt or op_dt < v_dt:
                continue
            next_dt = parse_time(vers[i + 1].get("time")) if i + 1 < len(vers) else None
            end = _version_window_end(v_dt, next_dt, window_hours)
            if op_dt >= end:
                continue
            ver["opinions"].append(_slim_opinion(op))
            hit = True
            break
        if not hit:
            unbound.append(op)
    return vers, unbound


def _item_blob(item: dict) -> str:
    return " ".join(
        str(x) for x in (
            item.get("filename") or "",
            item.get("text") or "",
            " ".join(str(n) for n in (item.get("nums") or [])),
        ) if x
    )


def _id_match(attach_id: str, item: dict) -> bool:
    return _talent_item_match({"attachId": attach_id, "label": attach_id}, item)


def _talent_item_match(talent: dict, item: dict) -> bool:
    """编号优先；同号再看公司；无编号则公司或人名。"""
    aid = str(talent.get("attachId") or "").strip()
    company = str(talent.get("company") or "").strip()
    person = str(talent.get("person") or "").strip()
    blob = _item_blob(item)
    item_co = str(item.get("company") or "") or _company_of(item.get("filename") or "")
    if aid and aid != _UNKNOWN_ATTACH:
        nums = {str(x) for x in (item.get("nums") or [])}
        id_hit = aid in nums or aid in blob or aid in set(M.extract_book_nums(blob))
        if not id_hit:
            return False
        if company and item_co and item_co != company:
            return False
        return True
    if company:
        return bool(item_co and item_co == company) or (company in blob)
    if person:
        return person.lower() in blob.lower()
    return False


def bind_opinions_name_id(
    versions: list[dict],
    opinions: list[dict],
    *,
    attach_id: str,
    talent: dict | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """文件名或正文编号/公司一致才绑；时间只用于排序；冲突写入 conflicts。"""
    vers = [dict(v) for v in versions or []]
    for v in vers:
        v["opinions"] = []
    meta = talent or {"attachId": attach_id, "label": attach_id}
    pool = [op for op in opinions or [] if _talent_item_match(meta, op)]
    unbound = [op for op in opinions or [] if op not in pool]
    conflicts: list[dict] = []
    for op in sorted(pool, key=lambda x: (parse_time(x.get("time")) or datetime.min, x.get("messageId") or "")):
        cands = []
        for ver in vers:
            if _talent_item_match(meta, ver) or (not meta.get("attachId") and not meta.get("company")):
                cands.append(ver)
        if not cands:
            cands = list(vers)
        if not cands:
            unbound.append(op)
            continue
        if len(cands) == 1:
            cands[0]["opinions"].append(_slim_opinion(op))
            continue
        # 多版候选：按时间就近挂到不晚于意见的最新申报书
        op_dt = parse_time(op.get("time")) or datetime.min
        best = None
        best_dt = None
        for ver in cands:
            v_dt = parse_time(ver.get("time")) or datetime.min
            if v_dt <= op_dt and (best_dt is None or v_dt > best_dt):
                best = ver
                best_dt = v_dt
        if best:
            best["opinions"].append(_slim_opinion(op))
        else:
            conflicts.append({
                "messageId": op.get("messageId") or "",
                "reason": "编号匹配但找不到不晚于意见时间的申报书版本",
                "filename": op.get("filename") or "",
            })
            unbound.append(op)
    return vers, unbound, conflicts


def _opinion_has_id(op: dict) -> bool:
    nums = [str(x) for x in (op.get("nums") or []) if M.is_book_num(str(x))]
    if nums:
        return True
    blob = _item_blob(op)
    return bool(M.extract_book_nums(blob))


def bind_orphan_opinions(
    talents: list[dict],
    opinions: list[dict],
    *,
    window_hours: int = 48,
    cluster_minutes: int = 15,
) -> list[dict]:
    """无编号意见挂到时间上紧挨着的上一份申报书；同一分钟发出的多份一起挂。"""
    bound_ids: set[str] = set()
    rows = []
    for t in talents or []:
        for v in t.get("versions") or []:
            for op in v.get("opinions") or []:
                mid = str(op.get("messageId") or "")
                if mid:
                    bound_ids.add(mid)
            rows.append((t, v, parse_time(v.get("time")) or datetime.min))
    rows.sort(key=lambda x: (x[2], str(x[1].get("messageId") or "")))
    leftover = []
    try:
        hours = int(window_hours)
    except (TypeError, ValueError):
        hours = 48
    span = timedelta(minutes=max(1, int(cluster_minutes)))
    for op in opinions or []:
        mid = str(op.get("messageId") or "")
        if mid in bound_ids:
            continue
        if _opinion_has_id(op) or op.get("company"):
            leftover.append(op)
            continue
        op_dt = parse_time(op.get("time"))
        if not op_dt:
            leftover.append(op)
            continue
        cands = [r for r in rows if r[2] <= op_dt]
        if not cands:
            leftover.append(op)
            continue
        latest = cands[-1][2]
        if hours > 0 and op_dt >= latest + timedelta(hours=max(1, hours)):
            leftover.append(op)
            continue
        cluster = [r for r in cands if latest - r[2] <= span]
        slim = _slim_opinion(op)
        for _t, ver, _dt in cluster:
            ops = list(ver.get("opinions") or [])
            if any(str(x.get("messageId") or "") == mid for x in ops):
                continue
            ops.append(slim)
            ver["opinions"] = ops
        bound_ids.add(mid)
    return leftover


def _slim_opinion(op: dict) -> dict:
    return {
        "messageId": str(op.get("messageId") or op.get("message_id") or ""),
        "kind": op.get("kind") or "file",
        "filename": op.get("filename") or "",
        "time": op.get("time") or "",
        "sender": op.get("sender") or "",
    }


def _public_library(row: dict | None) -> dict | None:
    if not row:
        return None
    pub = T.public_row(row, with_url=False)
    pub["source"] = "talent_app_files"
    return pub


def _match_library(chat_time: str, rows: list[dict], can_read: Callable[[dict], bool] | None) -> tuple[dict | None, bool]:
    cdt = parse_time(chat_time)
    if not cdt or not rows:
        return None, False
    best = None
    best_delta = None
    hidden = False
    for row in rows:
        ldt = parse_time(row.get("created_at") or row.get("createdAt"))
        if not ldt:
            continue
        delta = abs((ldt - cdt).total_seconds())
        if best is None or delta < best_delta:
            best = row
            best_delta = delta
    if not best:
        return None, False
    if can_read and not can_read(best):
        return None, True
    return _public_library(best), False


def _group_key(app: dict) -> tuple[str, str, str]:
    aid = str(app.get("attachId") or "").strip()
    if aid == _UNKNOWN_ATTACH:
        aid = ""
    company = str(app.get("company") or "") or _company_of(app.get("filename") or "")
    person = str(app.get("person") or "") or (_person_of(app) if not aid and not company else "")
    if not aid and not company and not person:
        return ("", "", _UNKNOWN_ATTACH)
    return (aid, company, person)


def _talent_label(aid: str, company: str, person: str) -> str:
    if aid and company:
        return aid + " " + company
    if aid:
        return aid
    if company:
        return company
    if person:
        return person
    return _UNKNOWN_ATTACH


def build_talent_versions(
    apps: list[dict],
    *,
    list_by_attach: Callable[[str], list[dict]] | None = None,
    can_read: Callable[[dict], bool] | None = None,
) -> list[dict]:
    """按编号+公司（或人名）分组，申报书按时间 chat-1、chat-2…"""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for app in apps or []:
        groups.setdefault(_group_key(app), []).append(app)
    talents = []
    keys = sorted(groups.keys(), key=lambda x: (x[2] == _UNKNOWN_ATTACH, x[0] == "", x[0], x[1], x[2]))
    for aid, company, person in keys:
        vers_apps = sorted(groups[(aid, company, person)], key=lambda x: (parse_time(x.get("time")) or datetime.min, x.get("messageId") or ""))
        lib_rows = list_by_attach(aid) if list_by_attach and aid else []
        versions = []
        for i, app in enumerate(vers_apps):
            lib, hidden = _match_library(app.get("time") or "", lib_rows, can_read)
            row = {
                "chatVersion": "chat-" + str(i + 1),
                "messageId": str(app.get("messageId") or ""),
                "filename": app.get("filename") or "",
                "time": app.get("time") or "",
                "sender": app.get("sender") or "",
                "opinions": [],
                "libraryVersion": lib,
                "libraryHidden": bool(hidden),
                "company": company or str(app.get("company") or "") or _company_of(app.get("filename") or ""),
            }
            versions.append(row)
        talents.append({
            "attachId": aid,
            "label": _talent_label(aid, company, person),
            "company": company,
            "person": person if person != _UNKNOWN_ATTACH else "",
            "versions": versions,
        })
    return talents


def collect_valid_message_ids(catalog: dict) -> set[str]:
    ids: set[str] = set()
    for key in ("apps", "opinions"):
        for item in catalog.get(key) or []:
            mid = str(item.get("messageId") or item.get("message_id") or "").strip()
            if mid:
                ids.add(mid)
    return ids


def sanitize_bind_payload(data: dict, valid_ids: set[str]) -> dict:
    """丢弃模型编造的 messageId。"""
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    out["talents"] = _sanitize_talents(data.get("talents") or [], valid_ids)
    out["unboundOpinions"] = _sanitize_opinions(data.get("unboundOpinions") or [], valid_ids)
    out["conflicts"] = _sanitize_conflicts(data.get("conflicts") or [], valid_ids)
    return out


def _sanitize_talents(talents: list, valid_ids: set[str]) -> list:
    rows = []
    for t in talents or []:
        if not isinstance(t, dict):
            continue
        vers = []
        for v in t.get("versions") or []:
            if not isinstance(v, dict):
                continue
            mid = str(v.get("messageId") or "").strip()
            if mid and mid not in valid_ids:
                continue
            nv = dict(v)
            nv["opinions"] = _sanitize_opinions(v.get("opinions") or [], valid_ids)
            vers.append(nv)
        if vers:
            rows.append({**t, "versions": vers})
    return rows


def _sanitize_opinions(ops: list, valid_ids: set[str]) -> list:
    out = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        mid = str(op.get("messageId") or "").strip()
        if mid and mid not in valid_ids:
            continue
        out.append({
            "messageId": mid,
            "kind": op.get("kind") or "file",
            "filename": op.get("filename") or "",
            "time": op.get("time") or "",
            "sender": op.get("sender") or "",
        })
    return out


def _sanitize_conflicts(rows: list, valid_ids: set[str]) -> list:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        mid = str(row.get("messageId") or "").strip()
        if mid and mid not in valid_ids:
            continue
        out.append(row)
    return out


def _bind_result_for_prompt(
    prompt_id: str,
    talents: list[dict],
    opinions: list[dict],
    *,
    window_hours: int = 48,
) -> dict:
    bound_ops: set[str] = set()
    out_talents = []
    all_unbound: list[dict] = []
    conflicts: list[dict] = []
    for t in talents:
        aid = str(t.get("attachId") or t.get("label") or "")
        vers = [dict(v) for v in t.get("versions") or []]
        pool = [op for op in opinions if _talent_item_match(t, op)]
        if prompt_id == PROMPT_TIME_WINDOW:
            # 有编号的仍按本人才时间窗；无编号的后面用簇填充
            vers, unbound = bind_opinions_time_window(vers, pool, window_hours=window_hours)
        elif prompt_id == PROMPT_NAME_ID:
            vers, unbound, extra = bind_opinions_name_id(vers, pool, attach_id=aid, talent=t)
            conflicts.extend(extra)
        else:
            unbound = pool
        for v in vers:
            for op in v.get("opinions") or []:
                bound_ops.add(str(op.get("messageId") or ""))
        out_talents.append({**t, "versions": vers})
        all_unbound.extend(unbound)
    if prompt_id in (PROMPT_TIME_WINDOW, PROMPT_NAME_ID):
        leftover = bind_orphan_opinions(out_talents, opinions, window_hours=window_hours)
        bound_ops = set()
        for t in out_talents:
            for v in t.get("versions") or []:
                for op in v.get("opinions") or []:
                    bound_ops.add(str(op.get("messageId") or ""))
        ub_seen = set()
        unbound_out = []
        for op in leftover + all_unbound:
            mid = str(op.get("messageId") or op.get("message_id") or "")
            if not mid or mid in ub_seen or mid in bound_ops:
                continue
            ub_seen.add(mid)
            unbound_out.append(_slim_opinion(op))
    else:
        unbound_out = [_slim_opinion(op) for op in opinions if str(op.get("messageId") or "") not in bound_ops]
    return {
        "id": prompt_id,
        "talents": out_talents,
        "unboundOpinions": unbound_out,
        "conflicts": conflicts,
    }


_SEMANTIC_PROMPT = """你是企业微信群聊「申报书版本 ↔ 修改意见」绑定助手。只依据输入消息与目录，为每个人才编号下的申报书版本（chat-1、chat-2…）挂接修改意见。

只输出一个 JSON 对象：
{
  "talents": [
    {
      "attachId": "编号或空",
      "label": "显示名",
      "versions": [
        {
          "chatVersion": "chat-1",
          "messageId": "申报书消息ID（必须来自输入）",
          "opinions": [
            {"messageId": "意见消息ID", "kind": "file|text", "filename": "", "time": "", "sender": ""}
          ]
        }
      ]
    }
  ],
  "unboundOpinions": [],
  "conflicts": [{"messageId": "", "reason": ""}]
}

要求：
- messageId 只能使用输入里出现的 ID，禁止编造
- 允许意见时间早于申报书，但须在 conflicts 或版本说明中体现原因
- 禁止仅靠人名绑定；编号或文件名/正文一致优先
- 没有把握的意见放进 unboundOpinions

输入：
"""

_SEMANTIC_STRICT_PROMPT = """你是企业微信群聊「申报书版本 ↔ 修改意见」严格绑定助手。只依据输入消息与目录，为每个人才编号下的申报书版本（chat-1、chat-2…）挂接修改意见。宁可少绑，不可误绑。

只输出一个 JSON 对象：
{
  "talents": [
    {
      "attachId": "编号或空",
      "label": "显示名",
      "versions": [
        {
          "chatVersion": "chat-1",
          "messageId": "申报书消息ID（必须来自输入）",
          "opinions": [
            {"messageId": "意见消息ID", "kind": "file|text", "filename": "", "time": "", "sender": ""}
          ]
        }
      ]
    }
  ],
  "unboundOpinions": [],
  "conflicts": [{"messageId": "", "reason": ""}]
}

严格要求：
- messageId 只能使用输入里出现的 ID，禁止编造
- 必须编号一致，或文件名/正文明确指向同一人才编号，才允许绑定
- 优先落在申报书时间之后、下一版之前的时间窗内（末版参考 windowHours）；窗外仅当编号与文件名证据极强时可绑，否则进 unboundOpinions
- 禁止用人名、发送者、模糊语义猜测绑定
- 一意见多版候选时：取时间上不晚于意见且最近的一版；无法判定写入 conflicts，意见进 unboundOpinions
- 没有十足把握一律放进 unboundOpinions

输入：
"""

_SEMANTIC_PROMPT_TEXTS = {
    PROMPT_SEMANTIC: _SEMANTIC_PROMPT,
    PROMPT_SEMANTIC_STRICT: _SEMANTIC_STRICT_PROMPT,
}


async def _bind_semantic(
    catalog: dict,
    talents: list[dict],
    *,
    window_hours: int = 48,
    prompt_id: str = PROMPT_SEMANTIC,
    prompt_text: str | None = None,
) -> dict:
    pid = prompt_id if prompt_id in _SEMANTIC_PROMPT_TEXTS else PROMPT_SEMANTIC
    text = prompt_text or _SEMANTIC_PROMPT_TEXTS.get(pid) or _SEMANTIC_PROMPT
    valid = collect_valid_message_ids(catalog)
    payload = {
        "windowHours": window_hours,
        "catalog": {
            "apps": catalog.get("apps") or [],
            "opinions": catalog.get("opinions") or [],
        },
        "talents": talents,
    }
    try:
        prof = resolve_gemini()
    except ValueError as e:
        return {
            "id": pid,
            "error": str(e),
            "talents": talents,
            "unboundOpinions": [_slim_opinion(op) for op in catalog.get("opinions") or []],
            "conflicts": [],
        }
    try:
        resp = await chat(
            [{"role": "user", "content": text + json.dumps(payload, ensure_ascii=False)}],
            json_mode=True,
            timeout_s=min(float(prof.get("timeoutSec") or 120), LLM_TIMEOUT_CLASSIFY * 2),
            profile=prof,
            retries=1,
            apply_profile_timeout=False,
        )
        data = extract_json_lenient(resp.get("content") or "")
        if not isinstance(data, dict):
            raise ValueError("语义绑定不是 JSON 对象")
        cleaned = sanitize_bind_payload(data, valid)
        cleaned["id"] = pid
        # 保留库版本字段
        cleaned["talents"] = _merge_library_fields(cleaned.get("talents") or [], talents)
        leftover = bind_orphan_opinions(
            cleaned.get("talents") or [],
            catalog.get("opinions") or [],
            window_hours=window_hours,
        )
        bound = {
            str(op.get("messageId") or "")
            for t in (cleaned.get("talents") or [])
            for v in (t.get("versions") or [])
            for op in (v.get("opinions") or [])
        }
        cleaned["unboundOpinions"] = [
            _slim_opinion(op) for op in leftover
            if str(op.get("messageId") or "") not in bound
        ]
        return cleaned
    except (LlmError, ValueError, TypeError, json.JSONDecodeError) as e:
        return {
            "id": pid,
            "error": str(e)[:200],
            "talents": talents,
            "unboundOpinions": [_slim_opinion(op) for op in catalog.get("opinions") or []],
            "conflicts": [],
        }


def _merge_library_fields(new_talents: list, base_talents: list) -> list:
    lib_map = {}
    for t in base_talents or []:
        for v in t.get("versions") or []:
            lib_map[str(v.get("messageId") or "")] = {
                "libraryVersion": v.get("libraryVersion"),
                "libraryHidden": v.get("libraryHidden"),
                "filename": v.get("filename"),
                "time": v.get("time"),
                "sender": v.get("sender"),
                "chatVersion": v.get("chatVersion"),
            }
    out = []
    for t in new_talents or []:
        vers = []
        for v in t.get("versions") or []:
            mid = str(v.get("messageId") or "")
            base = lib_map.get(mid) or {}
            vers.append({
                **base,
                **v,
                "chatVersion": v.get("chatVersion") or base.get("chatVersion") or "",
                "libraryVersion": base.get("libraryVersion"),
                "libraryHidden": base.get("libraryHidden", False),
                "opinions": _sanitize_opinions(v.get("opinions") or [], set(lib_map.keys()) | {mid}),
            })
        out.append({**t, "versions": vers})
    return out or base_talents


def count_bindings(bind_prompts: dict | None) -> int:
    n = 0
    if not isinstance(bind_prompts, dict):
        return 0
    for _pid, block in bind_prompts.items():
        if not isinstance(block, dict):
            continue
        for t in block.get("talents") or []:
            for v in t.get("versions") or []:
                n += len(v.get("opinions") or [])
    return n


async def run_version_bind(
    messages: list,
    *,
    session_id: str = "",
    session_name: str = "",
    window_hours: int = 48,
    bind_prompts: list[str] | None = None,
    user: dict | None = None,
    list_by_attach: Callable[[str], list[dict]] | None = None,
) -> dict:
    """执行版本绑定，返回 bindPrompts 字典。"""
    prompts = [p for p in (bind_prompts or DEFAULT_BIND_PROMPTS) if p in DEFAULT_BIND_PROMPTS]
    if not prompts:
        prompts = DEFAULT_BIND_PROMPTS[:]
    catalog = extract_catalog(messages, session_id=session_id, session_name=session_name)
    valid = collect_valid_message_ids(catalog)

    def _can_read(row: dict) -> bool:
        if not user:
            return True
        try:
            from .routes.talent_files import _can_read
            return _can_read(user, row)
        except Exception:
            return False

    lib_fn = list_by_attach or T.list_by_attach
    talents = build_talent_versions(
        catalog.get("apps") or [],
        list_by_attach=lib_fn,
        can_read=_can_read,
    )
    opinions = catalog.get("opinions") or []
    out: dict[str, Any] = {}

    async def _one(pid: str):
        if pid in _SEMANTIC_PROMPT_TEXTS:
            return pid, await _bind_semantic(
                catalog,
                talents,
                window_hours=window_hours,
                prompt_id=pid,
                prompt_text=_SEMANTIC_PROMPT_TEXTS[pid],
            )
        result = _bind_result_for_prompt(pid, talents, opinions, window_hours=window_hours)
        return pid, sanitize_bind_payload(result, valid)

    tasks = [_one(pid) for pid in prompts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in results:
        if isinstance(item, Exception):
            continue
        pid, block = item
        out[pid] = block
    return out
