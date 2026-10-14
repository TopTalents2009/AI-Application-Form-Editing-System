"""会话汇总扫到的新文件：按人才编号匹配后上传到人才库附件。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .attachments import KIND_POOL_ID, KINDS
from .config import DATA_DIR, atomic_replace
from . import matcher as M
from . import pool as POOL
from . import wecom_client as W
from .wecom_ai_bind import _UNKNOWN_ATTACH, _pick_attach_id
from .wecom_locate import parse_time

_STORE_PATH = DATA_DIR / "data" / "wecom_talent_uploads.json"
_MAX_PER_SCAN = 10
_WAIT_S = 6.0
SKIP_TYPES = {"app", "opinion", "other", ""}
UPLOAD_TYPES = {str(k.get("id") or "") for k in KINDS}
TYPE_LABEL = {str(k.get("id") or ""): str(k.get("label") or "") for k in KINDS}


def upload_configured() -> bool:
    return bool(POOL.talent_upload_target().get("ok"))


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def _load_state() -> dict:
    if not _STORE_PATH.is_file():
        return {"items": {}}
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"items": {}}
    items = data.get("items") if isinstance(data, dict) else None
    return {"items": items if isinstance(items, dict) else {}}


def _save_state(state: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    items = state.get("items") if isinstance(state, dict) else {}
    if not isinstance(items, dict):
        items = {}
    if len(items) > 2000:
        keys = list(items.keys())[-1800:]
        items = {k: items[k] for k in keys}
    tmp = _STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, _STORE_PATH)


def file_key(session_id, message_id, filename: str) -> str:
    return "|".join([
        str(session_id or "").strip(),
        str(message_id or "").strip(),
        str(filename or "").strip().lower(),
    ])


def already_uploaded(state: dict, key: str) -> dict | None:
    row = (state.get("items") or {}).get(key)
    if isinstance(row, dict) and row.get("ok"):
        return row
    return None


def remember(state: dict, key: str, *, attach_id: str, kind: str, filename: str, ok: bool, detail: str = "") -> None:
    items = state.setdefault("items", {})
    items[key] = {
        "attachId": str(attach_id or ""),
        "kind": str(kind or ""),
        "filename": str(filename or ""),
        "ok": bool(ok),
        "detail": str(detail or "")[:200],
        "at": _now(),
    }


def attach_id_of(*, filename: str = "", text: str = "", nums=None) -> str:
    aid = _pick_attach_id(list(nums or []), text, filename)
    if not aid or aid == _UNKNOWN_ATTACH:
        return ""
    if not M.is_book_num(aid):
        return ""
    return aid


def uploadable_kind(kind: str) -> bool:
    k = str(kind or "").strip()
    if k in SKIP_TYPES:
        return False
    return k in UPLOAD_TYPES


def pool_kind_of(kind: str) -> str:
    return str(KIND_POOL_ID.get(str(kind or "").strip()) or kind or "")


def fill_attach_ids(files: list) -> list:
    """文件名/正文编号优先；没有编号则挂到时间上最近的申报书编号。"""
    rows = [dict(f) for f in (files or []) if isinstance(f, dict)]
    apps = []
    for f in rows:
        fname = str(f.get("filename") or "")
        text = str(f.get("caption") or f.get("reason") or f.get("text") or "")
        aid = str(f.get("attachId") or "").strip()
        if not aid or aid == _UNKNOWN_ATTACH:
            aid = attach_id_of(filename=fname, text=text, nums=f.get("nums"))
        if aid:
            f["attachId"] = aid
        kind = str(f.get("type") or f.get("kind") or "")
        if kind == "app" and aid:
            apps.append((parse_time(f.get("time")) or datetime.min, aid))
    apps.sort(key=lambda x: x[0])
    pre = timedelta(hours=2)
    for f in rows:
        if str(f.get("attachId") or "").strip():
            continue
        if not uploadable_kind(str(f.get("type") or f.get("kind") or "")):
            continue
        dt = parse_time(f.get("time")) or datetime.min
        cands = [a for a in apps if a[0] <= dt + pre]
        if not cands:
            continue
        f["attachId"] = cands[-1][1]
        f["attachIdFrom"] = "nearby_app"
    return rows


def _copies_of(f: dict) -> list:
    raw = f.get("copies") if isinstance(f.get("copies"), list) else []
    out = []
    for c in raw:
        if isinstance(c, dict) and c.get("source_id") and c.get("message_id"):
            out.append(c)
    if out:
        return out
    sid = str(f.get("source_id") or "")
    try:
        mid = int(f.get("messageId") or f.get("message_id") or 0)
    except (TypeError, ValueError):
        mid = 0
    if sid and mid:
        return [{"source_id": sid, "message_id": mid, "session_id": str(f.get("sessionId") or "")}]
    return []


def annotate_files(files: list, state: dict | None = None) -> list:
    state = state or _load_state()
    rows = fill_attach_ids(files)
    for f in rows:
        kind = str(f.get("type") or f.get("kind") or "")
        aid = str(f.get("attachId") or "").strip()
        key = file_key(f.get("sessionId") or f.get("session_id"), f.get("messageId") or f.get("message_id"), f.get("filename") or "")
        prev = already_uploaded(state, key)
        if prev:
            f["uploadStatus"] = "uploaded"
            f["uploadNote"] = "已在人才库 " + str(prev.get("attachId") or aid or "")
            f["attachId"] = str(prev.get("attachId") or aid)
            continue
        if not uploadable_kind(kind):
            f["uploadStatus"] = "not_kind"
            f["uploadNote"] = "申报书/意见/未分类材料不自动入库"
            continue
        if not aid:
            f["uploadStatus"] = "no_id"
            f["uploadNote"] = "未识别人才编号"
            continue
        f["uploadStatus"] = "pending"
        f["uploadNote"] = "待上传到 " + aid
    return rows


def _summary_of(rows: list, *, configured: bool) -> dict:
    uploaded = sum(1 for f in rows if f.get("uploadStatus") == "uploaded")
    failed = sum(1 for f in rows if f.get("uploadStatus") == "error")
    pending = sum(1 for f in rows if f.get("uploadStatus") == "pending")
    no_id = sum(1 for f in rows if f.get("uploadStatus") == "no_id")
    note = ""
    if not configured:
        note = "未配置人才库，跳过自动入库"
    elif uploaded:
        note = "已自动入库 " + str(uploaded) + " 个"
        if failed:
            note += "，失败 " + str(failed) + " 个"
        if no_id:
            note += "，" + str(no_id) + " 个无编号"
    elif pending:
        note = "有新文件待入库"
    elif no_id:
        note = str(no_id) + " 个材料未识别人才编号，未入库"
    else:
        note = "没有需要自动入库的新材料"
    return {
        "ok": True,
        "configured": configured,
        "uploaded": uploaded,
        "failed": failed,
        "pending": pending,
        "noId": no_id,
        "note": note,
    }


async def _fetch_bytes(f: dict) -> tuple[bytes, str, str]:
    copies = _copies_of(f)
    got = await W.fetch_attachment_any(copies, wait_s=_WAIT_S)
    kind = str(got.get("kind") or "")
    if kind == "pending":
        raise W.WecomError("NOT_READY", str(got.get("detail") or "文件未缓存"), 409)
    if kind != "file" or not got.get("content"):
        raise W.WecomError("MISSING", str(got.get("detail") or "文件不在缓存"), 404)
    name = str(f.get("filename") or got.get("filename") or "file")
    ctype = str(got.get("content_type") or "application/octet-stream")
    return got["content"], name, ctype


async def upload_one(f: dict, *, state: dict | None = None, force: bool = False) -> dict:
    """上传一份已分类文件。返回带 uploadStatus 的副本。"""
    state = state if isinstance(state, dict) else _load_state()
    row = dict(f)
    rows = fill_attach_ids([row])
    row = rows[0] if rows else row
    kind = str(row.get("type") or row.get("kind") or "")
    aid = str(row.get("attachId") or "").strip()
    fname = str(row.get("filename") or "")
    key = file_key(row.get("sessionId") or row.get("session_id"), row.get("messageId") or row.get("message_id"), fname)
    prev = already_uploaded(state, key)
    if prev:
        row["uploadStatus"] = "uploaded"
        row["uploadNote"] = "已在人才库 " + str(prev.get("attachId") or aid)
        row["attachId"] = str(prev.get("attachId") or aid)
        return row
    if not uploadable_kind(kind) and not force:
        row["uploadStatus"] = "not_kind"
        row["uploadNote"] = "该类文件不自动入库"
        return row
    if not aid:
        row["uploadStatus"] = "no_id"
        row["uploadNote"] = "未识别人才编号"
        return row
    try:
        content, name, ctype = await _fetch_bytes(row)
        got = await POOL.upload_talent_attachment(
            attach_id=aid,
            filename=name,
            content=content,
            content_type=ctype,
            kind=pool_kind_of(kind),
            label=TYPE_LABEL.get(kind) or str(row.get("label") or ""),
        )
        remember(state, key, attach_id=aid, kind=kind, filename=fname, ok=True, detail=str(got.get("detail") or ""))
        _save_state(state)
        row["uploadStatus"] = "uploaded"
        row["uploadNote"] = ("库内已有 " if got.get("already") else "已上传到 ") + aid
        row["attachId"] = aid
        return row
    except POOL.PoolError as e:
        row["uploadStatus"] = "error"
        row["uploadNote"] = e.message[:160]
        return row
    except W.WecomError as e:
        row["uploadStatus"] = "missing" if e.code in ("MISSING",) or e.status == 404 else "error"
        row["uploadNote"] = e.message[:160]
        return row
    except Exception as e:
        row["uploadStatus"] = "error"
        row["uploadNote"] = str(e)[:160]
        return row


async def auto_upload_files(files: list, *, session_id: str = "", max_n: int = _MAX_PER_SCAN) -> tuple[list, dict]:
    """扫描结果里尚未入库、且能匹配人才编号的材料自动上传。"""
    configured = upload_configured()
    state = _load_state()
    rows = annotate_files(files, state)
    if session_id:
        for f in rows:
            if not f.get("sessionId"):
                f["sessionId"] = session_id
    if not configured:
        return rows, _summary_of(rows, configured=False)
    cap = max(1, min(int(max_n or _MAX_PER_SCAN), 20))
    n = 0
    for i, f in enumerate(rows):
        if f.get("uploadStatus") != "pending":
            continue
        if n >= cap:
            f["uploadNote"] = "本轮已达上限，下次扫描继续"
            continue
        n += 1
        rows[i] = await upload_one(f, state=state)
    return rows, _summary_of(rows, configured=True)


def apply_status_to_groups(groups: list, rows: list) -> None:
    by = {}
    for f in rows or []:
        key = file_key(f.get("sessionId"), f.get("messageId"), f.get("filename") or "")
        by[key] = f
    for g in groups or []:
        for f in g.get("files") or []:
            if not isinstance(f, dict):
                continue
            hit = by.get(file_key(f.get("sessionId"), f.get("messageId"), f.get("filename") or ""))
            if not hit:
                continue
            f["attachId"] = hit.get("attachId") or ""
            f["uploadStatus"] = hit.get("uploadStatus") or ""
            f["uploadNote"] = hit.get("uploadNote") or ""
            if hit.get("attachIdFrom"):
                f["attachIdFrom"] = hit.get("attachIdFrom")


def select_fresh_files(entries: list, fresh_ids) -> list:
    """只留下水位之后新消息里的文件。"""
    want = {str(x) for x in (fresh_ids or []) if str(x) not in ("", "0", "None")}
    out = []
    for it in entries or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("messageId") or it.get("message_id") or "") in want:
            out.append(dict(it))
    return out


def flatten_groups(groups: list, *, session_id: str = "") -> list:
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        kind = str(g.get("id") or "")
        label = str(g.get("label") or "")
        for f in g.get("files") or []:
            if not isinstance(f, dict):
                continue
            row = dict(f)
            row["type"] = kind or str(row.get("type") or "")
            row["kind"] = row["type"]
            row["label"] = label or str(row.get("label") or "")
            if session_id and not row.get("sessionId"):
                row["sessionId"] = session_id
            out.append(row)
    return out
