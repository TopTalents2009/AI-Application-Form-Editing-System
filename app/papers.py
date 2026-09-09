"""科研成果附件系统导出 API（只读 GET，见 论文api文档.md）"""
from __future__ import annotations
import json, re, asyncio
from urllib.parse import urlparse
import httpx
from .config import load_config, httpx_trust_env

PREFIX = "/api/v1"
_lock = asyncio.Lock()
_session_lock = asyncio.Lock()
_session_client: httpx.AsyncClient | None = None


class PapersError(Exception):
    def __init__(self, code: str, message: str, status: int = 0, body: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.body = body or {}


def norm_attach_id(val) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    s = re.sub(r"^(HJ)[_-]?", "", s, flags=re.I)
    m = re.search(r"\d{4,6}", s)
    return m.group(0) if m else ""


def abs_url(base: str, path: str) -> str:
    p = str(path or "").strip()
    if not p:
        return ""
    if re.match(r"https?://", p, re.I):
        return p
    b = str(base or "").rstrip("/")
    return b + (p if p.startswith("/") else "/" + p)


def _client_timeout(read: float = 30.0):
    return httpx.Timeout(read, connect=8.0)


def _trust_env(url: str) -> bool:
    return httpx_trust_env()


def _err_from_body(status: int, text: str) -> PapersError:
    code, msg, body = "HTTP_" + str(status), (text or "")[:240], {}
    try:
        d = json.loads(text or "{}")
        if isinstance(d, dict):
            body = d
            det = d.get("detail")
            if isinstance(det, str):
                msg = det
            elif isinstance(det, dict):
                msg = str(det.get("message") or det.get("detail") or msg)
    except Exception:
        pass
    if status == 401:
        code = "AUTH"
    elif status == 403:
        code = "FORBIDDEN"
    elif status == 404:
        code = "NOT_FOUND"
    elif status == 409:
        code = "NOT_READY"
    elif status == 503:
        code = "UNCONFIGURED"
    return PapersError(code, msg, status, body)


def _uses_api_key(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("papersApiKey"))


def _uses_session(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("papersUsername") and cfg.get("papersPassword"))


async def _reset_session():
    global _session_client
    async with _session_lock:
        if _session_client is not None:
            await _session_client.aclose()
        _session_client = None


async def _session_client_get() -> httpx.AsyncClient:
    global _session_client
    cfg = load_config()
    if not _uses_session(cfg):
        raise PapersError("NOT_CONFIGURED", "未配置论文系统账号 papers.username / papers.password")
    base = str(cfg.get("papersBaseUrl") or "").rstrip("/")
    async with _session_lock:
        if _session_client is None:
            _session_client = httpx.AsyncClient(
                timeout=_client_timeout(60.0),
                trust_env=_trust_env(base),
                follow_redirects=True,
            )
            r = await _session_client.post(
                base + "/api/login",
                data={"username": cfg["papersUsername"], "password": cfg["papersPassword"]},
            )
            if r.status_code >= 400:
                await _reset_session()
                raise _err_from_body(r.status_code, r.text)
            try:
                data = r.json()
            except Exception:
                data = {}
            if not (isinstance(data, dict) and data.get("ok")):
                await _reset_session()
                raise PapersError("AUTH", "论文系统登录失败")
        return _session_client


async def _session_json(path: str, *, params: dict | None = None, timeout: float = 30.0) -> dict:
    cfg = load_config()
    base = str(cfg.get("papersBaseUrl") or "").rstrip("/")
    url = abs_url(base, path)
    for attempt in range(2):
        client = await _session_client_get()
        try:
            r = await client.get(url, params=params or None, timeout=_client_timeout(timeout))
        except httpx.HTTPError as e:
            raise PapersError("NETWORK", str(e)[:200])
        if r.status_code == 401 and attempt == 0:
            await _reset_session()
            continue
        if r.status_code >= 400:
            raise _err_from_body(r.status_code, r.text)
        data = r.json()
        if not isinstance(data, dict):
            raise PapersError("BAD_RESPONSE", "响应不是 JSON 对象")
        return data
    raise PapersError("AUTH", "论文系统会话失效")


def _library_download_path(file_id) -> str:
    return "/api/library/files/" + str(file_id) + "/download"


def _build_talent_from_session(aid: str, talent: dict, library: dict) -> dict:
    files = [f for f in (library.get("files") or []) if isinstance(f, dict)]
    attachment = {}
    for f in files:
        if str(f.get("kind") or "") == "attachment" and str(f.get("mime") or "").startswith("application/pdf"):
            fid = f.get("file_id")
            attachment = {
                "ready": True,
                "filename": str(f.get("filename") or (aid + ".pdf")),
                "file_id": fid,
                "size": f.get("size"),
                "url": _library_download_path(fid),
            }
            break
    by_paper = {}
    for f in files:
        pid = str(f.get("paper_id") or "").strip()
        if not pid:
            continue
        if str(f.get("kind") or "") in ("annotated_pdf", "source_pdf"):
            by_paper[pid] = f
    papers_out = []
    for p in talent.get("papers") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or p.get("paper_id") or "").strip()
        row = dict(p)
        row["paper_id"] = pid
        hit = by_paper.get(pid)
        if hit and hit.get("file_id") not in (None, ""):
            row["file_id"] = hit.get("file_id")
            row["pdf_url"] = _library_download_path(hit.get("file_id"))
        papers_out.append(row)
    files_out = []
    for f in files:
        fid = f.get("file_id")
        if fid in (None, ""):
            continue
        kind = str(f.get("kind") or "")
        if kind not in ("attachment", "annotated_pdf", "source_pdf"):
            continue
        files_out.append({
            "file_id": fid,
            "kind": kind,
            "filename": str(f.get("filename") or "file.pdf"),
            "size": f.get("size"),
            "paper_id": str(f.get("paper_id") or ""),
            "doi": str(f.get("doi") or ""),
            "url": _library_download_path(fid),
        })
    meta = talent.get("meta") if isinstance(talent.get("meta"), dict) else {}
    return {
        "attach_id": aid,
        "name": str(meta.get("name") or talent.get("name") or ""),
        "attachment": attachment,
        "papers": papers_out,
        "files": files_out,
    }


async def _get_talent_session(aid: str) -> dict:
    talent = await _session_json("/api/talent/" + aid, timeout=30.0)
    if str(talent.get("detail") or "").strip():
        raise PapersError("NOT_FOUND", str(talent.get("detail") or ("没有该人才档案: " + aid)))
    library = await _session_json("/api/library/files", params={"attach_id": aid}, timeout=30.0)
    return _build_talent_from_session(aid, talent, library)


async def fetch_file(url: str) -> tuple[bytes, str, str, int]:
    """按论文系统配置下载文件（API Key 或网页会话）。"""
    cfg = load_config()
    if not cfg.get("papersConfigured"):
        raise PapersError("NOT_CONFIGURED", "未配置论文系统")
    full = abs_url(cfg.get("papersBaseUrl") or "", url)
    if _uses_api_key(cfg):
        headers = {"X-Api-Key": cfg["papersApiKey"]}
        try:
            async with httpx.AsyncClient(timeout=_client_timeout(120.0), trust_env=_trust_env(full), follow_redirects=True) as client:
                r = await client.get(full, headers=headers)
        except httpx.HTTPError as e:
            raise PapersError("NETWORK", str(e)[:200])
    else:
        for attempt in range(2):
            client = await _session_client_get()
            try:
                r = await client.get(full, timeout=_client_timeout(120.0))
            except httpx.HTTPError as e:
                raise PapersError("NETWORK", str(e)[:200])
            if r.status_code == 401 and attempt == 0:
                await _reset_session()
                continue
            break
    if r.status_code == 409:
        raise PapersError("NOT_READY", "附件尚未生成", 409)
    if r.status_code >= 400:
        raise _err_from_body(r.status_code, r.text)
    fn = "file.pdf"
    cd = r.headers.get("content-disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?", cd, re.I)
    if m:
        fn = m.group(1) or m.group(2) or fn
    ctype = r.headers.get("content-type") or "application/octet-stream"
    return r.content, fn, ctype, r.status_code


async def _request(path: str, *, timeout: float = 30.0, as_json: bool = True):
    cfg = load_config()
    if not _uses_api_key(cfg):
        raise PapersError("NOT_CONFIGURED", "未配置论文系统 papers.apiKey")
    url = abs_url(cfg["papersBaseUrl"], PREFIX + path if path.startswith("/") else PREFIX + "/" + path)
    headers = {"X-Api-Key": cfg["papersApiKey"], "Accept": "application/json"}
    async with _lock:
        try:
            async with httpx.AsyncClient(timeout=_client_timeout(timeout), trust_env=_trust_env(url)) as client:
                r = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            raise PapersError("NETWORK", str(e)[:200])
    if r.status_code >= 400:
        raise _err_from_body(r.status_code, r.text)
    if not as_json:
        return r
    data = r.json()
    if not isinstance(data, dict):
        raise PapersError("BAD_RESPONSE", "响应不是 JSON 对象")
    return data


async def health() -> dict:
    """探活：先 GET /api/v1/health（免密钥），再在有 apiKey 时试拉人才列表验证鉴权。"""
    cfg = load_config()
    base = str(cfg.get("papersBaseUrl") or "").rstrip("/")
    if not base:
        return {"configured": False, "service_ok": False, "ok": False, "error": "未配置 papers.baseUrl"}
    url = abs_url(base, PREFIX + "/health")
    try:
        async with httpx.AsyncClient(timeout=_client_timeout(10.0), trust_env=_trust_env(url)) as client:
            r = await client.get(url)
    except httpx.HTTPError as e:
        return {"configured": bool(cfg.get("papersConfigured")), "service_ok": False, "ok": False, "error": "NETWORK: " + str(e)[:160]}
    if r.status_code >= 400:
        return {"configured": bool(cfg.get("papersConfigured")), "service_ok": False, "ok": False, "error": "HTTP " + str(r.status_code)}
    try:
        data = r.json()
    except Exception:
        data = {}
    service_ok = bool(isinstance(data, dict) and data.get("ok"))
    out = {
        "configured": bool(cfg.get("papersConfigured")),
        "service_ok": service_ok,
        "api": data.get("api") if isinstance(data, dict) else "",
        "mode": data.get("mode") if isinstance(data, dict) else "",
        "auth_ok": False,
        "ok": False,
        "error": None,
    }
    if not service_ok:
        out["error"] = "论文导出服务未就绪"
        return out
    if not cfg.get("papersConfigured"):
        out["error"] = "未配置 papers.apiKey 或 papers.username/password（服务可达，鉴权未测）"
        return out
    try:
        if _uses_api_key(cfg):
            await _request("/talents", timeout=15.0)
            out["auth_mode"] = "apiKey"
        else:
            me = await _session_json("/api/me", timeout=15.0)
            if not me.get("username"):
                raise PapersError("AUTH", "论文系统登录后未返回用户")
            out["auth_mode"] = "session"
            out["username"] = me.get("username")
        out["auth_ok"] = True
        out["ok"] = True
        out["configured"] = True
        return out
    except PapersError as e:
        out["error"] = e.code + ": " + e.message
        return out


async def get_talent(attach_id: str) -> dict:
    """拉取人才论文详情（优先 export API Key，否则网页会话）。"""
    aid = norm_attach_id(attach_id)
    if not aid:
        raise PapersError("BAD_ID", "人才 ID 无效")
    cfg = load_config()
    if not cfg.get("papersConfigured"):
        raise PapersError("NOT_CONFIGURED", "未配置论文系统")
    if _uses_api_key(cfg):
        return await _request("/talents/" + aid, timeout=30.0)
    return await _get_talent_session(aid)


def public_files(data: dict, base: str) -> list:
    """从人才详情抽出可下载项（装订 PDF + 单篇 PDF）。"""
    out = []
    if not isinstance(data, dict):
        return out
    aid = str(data.get("attach_id") or "")
    att = data.get("attachment") if isinstance(data.get("attachment"), dict) else {}
    if att.get("ready") and (att.get("url") or att.get("file_id") is not None):
        url = att.get("url") or ("/api/v1/files/" + str(att.get("file_id")))
        out.append({
            "kind": "论文装订附件",
            "filename": str(att.get("filename") or (aid + ".pdf")),
            "title": "装订附件 " + (aid + ".pdf" if aid else ""),
            "url": url,
            "file_id": att.get("file_id"),
            "size": att.get("size"),
            "paper_id": "",
        })
    seen = {str(x.get("url") or "") for x in out}
    for p in data.get("papers") or []:
        if not isinstance(p, dict):
            continue
        fid = p.get("file_id")
        pdf = str(p.get("pdf_url") or "")
        if not pdf and fid not in (None, ""):
            pdf = "/api/v1/files/" + str(fid)
        if not pdf:
            continue
        title = str(p.get("title_zh") or p.get("title") or "").strip()
        fn = (str(p.get("paper_id") or "paper") + ".pdf")
        item = {
            "kind": "论文全文",
            "filename": fn,
            "title": title or fn,
            "url": pdf,
            "file_id": fid,
            "size": None,
            "paper_id": str(p.get("paper_id") or ""),
            "doi": str(p.get("doi") or ""),
            "year": str(p.get("year") or ""),
            "journal": str(p.get("journal") or ""),
        }
        if pdf not in seen:
            seen.add(pdf)
            out.append(item)
    for f in data.get("files") or []:
        if not isinstance(f, dict):
            continue
        url = str(f.get("url") or "")
        if not url and f.get("file_id") not in (None, ""):
            url = "/api/v1/files/" + str(f.get("file_id"))
        if not url or url in seen:
            continue
        k = str(f.get("kind") or "")
        label = "论文全文"
        if k == "attachment":
            label = "论文装订附件"
        elif k == "annotated_pdf":
            label = "论文全文"
        elif k == "source_pdf":
            label = "论文原文"
        seen.add(url)
        out.append({
            "kind": label,
            "filename": str(f.get("filename") or "file.pdf"),
            "title": str(f.get("filename") or ""),
            "url": url,
            "file_id": f.get("file_id"),
            "size": f.get("size"),
            "paper_id": str(f.get("paper_id") or ""),
            "doi": str(f.get("doi") or ""),
        })
    for it in out:
        it["abs_url"] = abs_url(base, it.get("url") or "")
    return out
