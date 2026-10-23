"""p 图工单（PS 系统）外部 API：计划生成后预创建工单并上传系统改不了的原件。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import load_config, httpx_trust_env

PREFIX = "/api/external/v1"
DEFAULT_BASE = "http://192.168.2.50:18427" + PREFIX
MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = {
    ".jpg", ".jpeg", ".png", ".webp",
    ".pdf", ".txt",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
}
STATUS_LABEL = {
    "pending": "待处理",
    "in_progress": "处理中",
    "on_hold": "挂起",
    "reviewing": "待审核",
    "returned": "已退回",
    "completed": "已完成",
    "cancelled": "已取消",
}
PUBLIC_KEYS = (
    "ok", "id", "status", "statusLabel", "talentId", "talentName",
    "note", "actorName", "url", "assets", "uploaded", "skipped", "error", "at",
)
EXT_FID_RE = re.compile(r"/ext-files/([A-Za-z0-9]+)")


class PsError(Exception):
    def __init__(self, code: str, message: str, status: int = 0, body: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.body = body or {}


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def normalize_base(url: str) -> str:
    u = str(url or "").strip().rstrip("/")
    if not u:
        return ""
    if u.endswith(PREFIX):
        return u
    if u.endswith("/api/external"):
        return u + "/v1"
    parsed = urlparse(u)
    if parsed.scheme and parsed.netloc and "/api/" not in (parsed.path or ""):
        return u + PREFIX
    return u


def cfg_block() -> dict:
    cfg = load_config()
    block = cfg.get("ps") if isinstance(cfg.get("ps"), dict) else {}
    return block if isinstance(block, dict) else {}


def configured() -> bool:
    b = cfg_block()
    return bool(b.get("configured") or (b.get("baseUrl") and b.get("apiKey")))


def ticket_web_url(ticket_id) -> str:
    if ticket_id in (None, ""):
        return ""
    b = cfg_block()
    web = str(b.get("webUrl") or "").strip().rstrip("/")
    if not web:
        base = normalize_base(str(b.get("baseUrl") or cfg_block().get("baseUrl") or ""))
        web = re.sub(r"/api/external/v1/?$", "", base).rstrip("/")
    if not web:
        return ""
    return web + "/tickets/" + str(ticket_id)


def _asset_filename(a: dict) -> str:
    if not isinstance(a, dict):
        return ""
    for k in ("filename", "name", "original_name", "originalName", "file_name", "fileName", "title"):
        v = str(a.get(k) or "").strip()
        if v:
            return v
    return ""


def public_ticket(row: dict | None) -> dict:
    if not isinstance(row, dict):
        return {}
    if not row.get("id") and not row.get("error") and not row.get("ok") and not row.get("status"):
        return {}
    out = {k: row.get(k) for k in PUBLIC_KEYS}
    if out.get("status") and not out.get("statusLabel"):
        out["statusLabel"] = STATUS_LABEL.get(str(out.get("status") or ""), str(out.get("status") or ""))
    assets = []
    for a in row.get("assets") or []:
        if not isinstance(a, dict):
            continue
        assets.append({
            "id": a.get("id") or a.get("asset_id") or "",
            "filename": _asset_filename(a),
            "kind": a.get("kind") or a.get("type") or "original",
        })
    out["assets"] = assets
    out["ok"] = bool(row.get("ok"))
    return out


_PS_NOTE_NOISE = re.compile(
    r"无法通过系统改|无法写入申报书|请按审核意见|请按意见更正|请按意见更新|"
    r"请按意见准备|请按意见补|请按意见另行|另行上传|另行准备|另行补传|"
    r"请下载核对|已在本机|本地「附件」|人才库|聊天记录均|"
    r"需申报人自行|在申报系统或材料包|对应待修改附件|缺失附件已检索|"
    r"未检索到可下载|需要补充材料|【缺附件|下载链接|ext-files|/api/tasks/"
)
_PS_EDIT_HINT = re.compile(
    r"翻译|标亮|改为|改成|改至|修改为|更正|不清晰|清晰|时间|日期|职位|职务|"
    r"姓名|国籍|出生|证件号码|护照号码|有效期"
)
_PS_BARE_SUPPLEMENT = re.compile(r"^(?:请)?(?:补充|补传|补交|缺少|缺失|需补充)")


def _ps_keep_line(s: str) -> bool:
    t = str(s or "").strip()
    if not t or _PS_NOTE_NOISE.search(t):
        return False
    if t in ("未检索到可下载文件", "需要补充材料", "需另行提供"):
        return False
    if _PS_BARE_SUPPLEMENT.search(t) and not _PS_EDIT_HINT.search(t):
        return False
    return True


def _ps_is_paper_noise(it: dict) -> bool:
    if str(it.get("id") or "") != "paper":
        return False
    blob = "\n".join(str(x) for x in (it.get("locations") or [])) + "\n" + str(it.get("snippet") or "")
    if re.search(r"主持项目|科研成果证明", blob) and not re.search(r"论文.{0,8}(扫描|不清晰|模糊)", blob):
        return True
    if not re.search(r"论文.{0,16}(扫描|不清晰|模糊|清晰|翻译|标亮)", blob):
        return True
    return False


def _ps_change_lines(it: dict) -> list:
    """只取已有扫描件上要改的点，不要补材料、检索失败说明。"""
    if not isinstance(it, dict):
        return []
    if not (it.get("downloads") or []):
        return []
    if _ps_is_paper_noise(it):
        return []
    locs = [str(x).strip() for x in (it.get("locations") or []) if str(x).strip()]
    bits = locs
    if not bits:
        snip = str(it.get("snippet") or "").strip()
        if snip:
            bits = [x.strip() for x in re.split(r"[；;]\s*", snip) if x.strip()]
    return [b for b in bits if _ps_keep_line(b)]


def slim_ps_note(note: str) -> str:
    """清洗已生成的备注，去掉申报书抬头、附件名、办理说明。"""
    keep = []
    seen = set()
    for raw in str(note or "").splitlines():
        t = re.sub(r"^[\s\-•·]+", "", str(raw or "")).strip()
        if not t:
            continue
        if t.startswith("任务提交人："):
            continue
        if t.startswith("申报书 ") or t.startswith("需要修改的位置"):
            continue
        if t.startswith("（无具体"):
            continue
        if not _ps_keep_line(t):
            continue
        if re.match(r"^\d+\.\s+", t) and (
            re.search(r"[（(][^）)]+\.[A-Za-z0-9]{2,4}[）)]", t)
            or (len(t) < 24 and not re.search(r"改|换|补|职位|时间|职务|不清晰", t))
        ):
            continue
        key = re.sub(r"\s+", "", t)
        if not key or key in seen:
            continue
        seen.add(key)
        keep.append(t)
    return "\n".join(keep).strip()


def build_note(*, task_id: str = "", app_name: str = "", talent_id: str = "", talent_name: str = "", items: list | None = None, actor_name: str = "") -> str:
    """给工单备注预填扫描件上要改的点。人才编号、申报书名、提交人走独立字段，不写入备注。"""
    _ = (task_id, app_name, talent_id, talent_name)
    rows = [x for x in (items or []) if isinstance(x, dict)]
    groups = []
    for it in rows:
        changes = _ps_change_lines(it)
        if changes:
            groups.append((str(it.get("title") or "").strip(), changes))
    out, seen = [], set()
    multi = len(groups) > 1
    for title, changes in groups:
        for loc in changes:
            line = loc
            if multi and title and not loc.startswith(title):
                line = title + "：" + loc
            key = re.sub(r"\s+", "", line)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(line)
    text = slim_ps_note("\n".join(out))
    if str(actor_name or "").strip():
        text = compose_note(text, actor_name)
    return text[:2000]


def compose_note(note: str, actor_name: str) -> str:
    name = str(actor_name or "").strip()
    body = str(note or "").strip()
    body = re.sub(r"^任务提交人：.*(?:\n|$)", "", body).strip()
    if name:
        return ("任务提交人：" + name + ("\n" + body if body else "")).strip()[:2000]
    return body[:2000]


def draft_for_task(t: dict | None, items: list | None, *, actor_name: str = "", plan: dict | None = None) -> dict:
    rows = [x for x in (items or []) if isinstance(x, dict)]
    if not rows:
        return {"configured": configured(), "note": "", "actorName": str(actor_name or "").strip(), "fileCount": 0}
    plan = plan if isinstance(plan, dict) else {}
    from .attachments import attach_item_locations
    from .wecom_locate import identity_of
    rows = attach_item_locations(
        rows,
        clauses=plan.get("clauses") or [],
        leftovers=plan.get("leftovers") or [],
        edits=plan.get("edits") or [],
    )
    ident = identity_of(t or {})
    app = (t or {}).get("app") if isinstance((t or {}).get("app"), dict) else {}
    ps_rows = [x for x in rows if _ps_change_lines(x)]
    refs = collect_download_refs(ps_rows)
    return {
        "configured": configured(),
        "note": build_note(
            task_id=str((t or {}).get("id") or ""),
            app_name=str(app.get("name") or ""),
            talent_id=str(ident.get("attachId") or app.get("attachId") or app.get("no") or ""),
            talent_name=str(ident.get("personName") or app.get("personName") or ""),
            items=ps_rows,
        ),
        "actorName": str(actor_name or "").strip(),
        "fileCount": len(refs),
        "files": [{"filename": r.get("filename") or "", "title": r.get("title") or ""} for r in refs],
    }


def allowed_filename(name: str) -> bool:
    suf = Path(str(name or "")).suffix.lower()
    return suf in ALLOWED_EXT


def _ctype_of(name: str) -> str:
    suf = Path(str(name or "")).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(suf, "application/octet-stream")


def _err_from_body(status: int, text: str) -> PsError:
    code, msg, body = "HTTP_" + str(status), (text or "")[:240], {}
    try:
        d = json.loads(text or "{}")
        if isinstance(d, dict):
            body = d
            msg = str(d.get("message") or d.get("detail") or msg)
            if isinstance(d.get("detail"), dict):
                msg = str(d["detail"].get("message") or d["detail"].get("detail") or msg)
            code = str(d.get("code") or code)
    except Exception:
        pass
    if status == 401:
        code = "AUTH"
    elif status == 404:
        code = "NOT_FOUND"
    elif status == 409:
        code = "CONFLICT"
    elif status == 422:
        code = "INVALID"
    return PsError(code, msg, status, body)


def _ticket_id(body) -> str:
    if not isinstance(body, dict):
        return ""
    for k in ("id", "ticket_id", "ticketId"):
        v = body.get(k)
        if v not in (None, ""):
            return str(v)
    for nest in (body.get("ticket"), body.get("item"), body.get("data")):
        got = _ticket_id(nest) if isinstance(nest, dict) else ""
        if got:
            return got
    return ""


def _headers(key: str) -> dict:
    k = str(key or "").strip()
    return {"X-API-KEY": k, "X-Api-Key": k}


def _client_timeout(read: float = 30.0):
    return httpx.Timeout(read, connect=8.0)


async def _request(method: str, path: str, *, json_body=None, data=None, files=None, timeout: float = 30.0) -> dict:
    b = cfg_block()
    base = normalize_base(str(b.get("baseUrl") or ""))
    key = str(b.get("apiKey") or "").strip()
    if not base or not key:
        raise PsError("NOT_CONFIGURED", "未配置 PS 系统 ps.baseUrl / ps.apiKey")
    url = base.rstrip("/") + (path if str(path).startswith("/") else "/" + str(path))
    headers = _headers(key)
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    try:
        async with httpx.AsyncClient(timeout=_client_timeout(timeout), trust_env=httpx_trust_env()) as client:
            r = await client.request(method, url, headers=headers, json=json_body, data=data, files=files)
    except httpx.HTTPError as e:
        raise PsError("NETWORK", str(e)[:200])
    if r.status_code >= 400:
        raise _err_from_body(r.status_code, r.text)
    if not r.content:
        return {}
    try:
        body = r.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def health() -> dict:
    b = cfg_block()
    base = normalize_base(str(b.get("baseUrl") or ""))
    key = str(b.get("apiKey") or "").strip()
    out = {
        "configured": bool(base and key),
        "baseUrl": base,
        "ok": False,
        "service_ok": False,
        "error": "",
    }
    if not base:
        out["error"] = "未配置 ps.baseUrl"
        return out
    url = base.rstrip("/") + "/health"
    headers = _headers(key) if key else {}
    try:
        async with httpx.AsyncClient(timeout=_client_timeout(10.0), trust_env=httpx_trust_env()) as client:
            r = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        out["error"] = "NETWORK: " + str(e)[:160]
        return out
    if r.status_code >= 400:
        out["error"] = "HTTP " + str(r.status_code)
        try:
            d = r.json()
            if isinstance(d, dict) and (d.get("message") or d.get("detail")):
                out["error"] = str(d.get("message") or d.get("detail"))[:200]
        except Exception:
            pass
        return out
    out["service_ok"] = True
    if r.status_code == 401 or (not key):
        out["error"] = "服务可达；鉴权未通过" if r.status_code == 401 else "未配置 ps.apiKey（服务可达）"
        return out
    out["ok"] = True
    try:
        data = r.json()
        if isinstance(data, dict):
            out["detail"] = str(data.get("message") or data.get("status") or "已连通")
    except Exception:
        out["detail"] = "已连通"
    return out


async def create_ticket(*, talent_id: str, talent_name: str, note: str) -> dict:
    body = {
        "talent_id": str(talent_id or "").strip(),
        "talent_name": str(talent_name or "").strip(),
        "note": str(note or "").strip(),
    }
    data = await _request("POST", "/tickets", json_body=body, timeout=30.0)
    tid = _ticket_id(data)
    if not tid:
        raise PsError("BAD_RESPONSE", "创建工单未返回 id")
    data = dict(data)
    data["id"] = tid
    return data


async def get_ticket(ticket_id) -> dict:
    tid = str(ticket_id or "").strip()
    if not tid:
        raise PsError("BAD_REQUEST", "缺少工单 id")
    data = await _request("GET", "/tickets/" + tid, timeout=20.0)
    got = _ticket_id(data) or tid
    data = dict(data)
    data["id"] = got
    return data


async def upload_originals(ticket_id, files: list) -> dict:
    """files: [{filename, content, content_type}]。一次可传多份 original。"""
    tid = str(ticket_id or "").strip()
    rows = [x for x in (files or []) if isinstance(x, dict) and x.get("content")]
    if not tid:
        raise PsError("BAD_REQUEST", "缺少工单 id")
    if not rows:
        return {"ok": True, "uploaded": 0}
    payload = []
    for it in rows:
        name = str(it.get("filename") or "file").strip() or "file"
        payload.append(("files", (name, it["content"], str(it.get("content_type") or _ctype_of(name)))))
    data = await _request(
        "POST",
        "/tickets/" + tid + "/assets",
        data={"kind": "original"},
        files=payload,
        timeout=90.0,
    )
    return data if isinstance(data, dict) else {"ok": True}


def _fid_of(download: str) -> str:
    m = EXT_FID_RE.search(str(download or ""))
    return m.group(1) if m else ""


def collect_download_refs(items: list) -> list:
    """从任务清单抽出待上传文件引用（不去读内容）。"""
    out, seen = [], set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("id") or "附件")
        for d in it.get("downloads") or []:
            if not isinstance(d, dict):
                continue
            fn = str(d.get("filename") or d.get("title") or "").strip()
            loc = str(d.get("localPath") or "").strip()
            fid = _fid_of(str(d.get("download") or ""))
            key = (loc.lower() if loc else "") or (fid or fn.lower())
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({
                "filename": fn or Path(loc).name or "file",
                "localPath": loc,
                "fid": fid,
                "title": title,
                "source": str(d.get("source") or ""),
            })
    return out


async def load_file_bytes(task_dir, ref: dict) -> tuple[bytes, str, str]:
    """返回 (content, filename, content_type)。"""
    from .attachments import load_private, fetch_upstream, _safe_local_file
    fn = str((ref or {}).get("filename") or "file")
    loc = str((ref or {}).get("localPath") or "").strip()
    if loc:
        try:
            p = _safe_local_file(loc)
            return p.read_bytes(), str(p.name or fn), _ctype_of(p.name or fn)
        except Exception:
            pass
    fid = str((ref or {}).get("fid") or "")
    if fid:
        priv = load_private(task_dir, fid)
        if priv:
            content, name, ctype, _st = await fetch_upstream(priv)
            return content, str(name or fn), str(ctype or _ctype_of(name or fn))
    raise PsError("BAD_FILE", "找不到可上传文件 " + fn)


def _from_remote(ticket: dict, *, talent_id: str = "", talent_name: str = "", note: str = "", actor_name: str = "", skipped=None, uploaded: int = 0, file_names=None) -> dict:
    tid = _ticket_id(ticket) or str(ticket.get("id") or "")
    st = str(ticket.get("status") or "pending")
    assets = ticket.get("assets") or ticket.get("files") or []
    if not isinstance(assets, list):
        assets = []
    names = [str(x).strip() for x in (file_names or []) if str(x).strip()]
    if not assets and names:
        assets = [{"filename": n, "kind": "original"} for n in names]
    else:
        filled = []
        for i, a in enumerate(assets):
            row = dict(a) if isinstance(a, dict) else {}
            if not _asset_filename(row) and i < len(names):
                row["filename"] = names[i]
            filled.append(row)
        assets = filled
    n_up = uploaded
    if not n_up:
        n_up = len([a for a in assets if isinstance(a, dict)])
    return public_ticket({
        "ok": True,
        "id": tid,
        "status": st,
        "statusLabel": STATUS_LABEL.get(st, st),
        "talentId": str(ticket.get("talent_id") or ticket.get("talentId") or talent_id or ""),
        "talentName": str(ticket.get("talent_name") or ticket.get("talentName") or talent_name or ""),
        "note": str(ticket.get("note") or note or ""),
        "actorName": str(actor_name or ticket.get("actorName") or ticket.get("actor_name") or ""),
        "url": ticket_web_url(tid),
        "assets": assets,
        "uploaded": n_up,
        "skipped": list(skipped or []),
        "error": "",
        "at": _now(),
    })


async def preupload_task_list(t: dict, task_list: list, *, note: str = "", actor_name: str = "") -> dict:
    """人工确认并填写备注、提交人后，才创建工单并上传原件。"""
    items = [x for x in (task_list or []) if isinstance(x, dict)]
    if not items:
        return public_ticket(t.get("psTicket") if isinstance(t.get("psTicket"), dict) else {}) or {}
    if not configured():
        return public_ticket({"ok": False, "error": "未配置 PS 系统"})
    actor_name = str(actor_name or "").strip()
    note = str(note or "").strip()
    if not note:
        raise PsError("INVALID", "请填写需要修改的备注")
    if not actor_name:
        raise PsError("INVALID", "请填写任务提交人姓名")
    from .wecom_locate import identity_of
    ident = identity_of(t) if t else {}
    app = (t or {}).get("app") if isinstance((t or {}).get("app"), dict) else {}
    talent_id = str(ident.get("attachId") or app.get("attachId") or app.get("no") or "").strip()
    talent_name = str(ident.get("personName") or app.get("personName") or "").strip()
    note = compose_note(note, actor_name)
    prev = t.get("psTicket") if isinstance(t.get("psTicket"), dict) else {}
    skipped = []
    packed = []
    seen_name = set()
    ps_items = [x for x in items if _ps_change_lines(x)]
    refs = collect_download_refs(ps_items)
    task_dir = str((t or {}).get("dir") or "")
    for ref in refs:
        fn = str(ref.get("filename") or "file")
        if not allowed_filename(fn):
            skipped.append(fn + "（格式不支持，需 JPEG/PNG/WEBP/PDF/Office/TXT）")
            continue
        try:
            content, name, ctype = await load_file_bytes(task_dir, ref)
        except Exception as e:
            skipped.append(fn + "（读取失败：" + str(e)[:80] + "）")
            continue
        if not content:
            skipped.append(fn + "（空文件）")
            continue
        if len(content) > MAX_FILE_BYTES:
            skipped.append(fn + "（超过 10MB）")
            continue
        key = name.lower()
        if key in seen_name:
            stem = Path(name).stem
            suf = Path(name).suffix
            name = stem + "-" + str(ref.get("title") or "file") + suf
            key = name.lower()
        seen_name.add(key)
        packed.append({"filename": name, "content": content, "content_type": ctype})
    try:
        ticket_id = str(prev.get("id") or "").strip()
        remote = None
        if ticket_id and str(prev.get("status") or "") != "cancelled":
            try:
                remote = await get_ticket(ticket_id)
            except PsError as e:
                if e.code == "NETWORK" or e.status in (500, 502, 503):
                    remote = {"id": ticket_id, "status": prev.get("status") or "pending"}
                else:
                    remote = None
                    ticket_id = ""
        if not ticket_id:
            created = await create_ticket(talent_id=talent_id, talent_name=talent_name, note=note)
            ticket_id = _ticket_id(created)
            remote = created
        same = ticket_id == str(prev.get("id") or "")
        uploaded_n = int(prev.get("uploaded") or 0) if same else 0
        already = {str(_asset_filename(a) or "").lower() for a in (prev.get("assets") or []) if isinstance(a, dict)}
        already.discard("")
        if same and uploaded_n >= len(packed) and packed:
            to_send = []
        else:
            to_send = [x for x in packed if str(x.get("filename") or "").lower() not in already]
        if to_send:
            try:
                await upload_originals(ticket_id, to_send)
                uploaded_n += len(to_send)
            except PsError as e:
                skipped.append("上传失败：" + e.message)
        try:
            remote = await get_ticket(ticket_id)
        except PsError:
            remote = remote or {"id": ticket_id, "status": prev.get("status") or "pending"}
        return _from_remote(
            remote,
            talent_id=talent_id,
            talent_name=talent_name,
            note=note,
            actor_name=actor_name,
            skipped=skipped,
            uploaded=uploaded_n,
            file_names=[x.get("filename") for x in packed],
        )
    except PsError as e:
        return public_ticket({
            "ok": False,
            "id": str(prev.get("id") or ""),
            "talentId": talent_id,
            "talentName": talent_name,
            "note": note,
            "actorName": actor_name,
            "uploaded": 0,
            "skipped": skipped,
            "error": e.message,
            "at": _now(),
        })
    except Exception as e:
        return public_ticket({
            "ok": False,
            "id": str(prev.get("id") or ""),
            "talentId": talent_id,
            "talentName": talent_name,
            "note": note,
            "actorName": actor_name,
            "uploaded": 0,
            "skipped": skipped,
            "error": str(e)[:200],
            "at": _now(),
        })
