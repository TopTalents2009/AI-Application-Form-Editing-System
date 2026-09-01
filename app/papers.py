"""科研成果附件系统导出 API（只读 GET，见 论文api文档.md）"""
from __future__ import annotations
import json, re, asyncio
from urllib.parse import urlparse
import httpx
from .config import load_config

PREFIX = "/api/v1"
_lock = asyncio.Lock()


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
    host = urlparse(url).hostname or ""
    return host not in ("127.0.0.1", "localhost", "::1")


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


async def _request(path: str, *, timeout: float = 30.0, as_json: bool = True):
    cfg = load_config()
    if not cfg.get("papersConfigured"):
        raise PapersError("NOT_CONFIGURED", "未配置论文系统 papers.baseUrl / papers.apiKey")
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


async def get_talent(attach_id: str) -> dict:
    """GET /api/v1/talents/{id}。404 不重试。"""
    aid = norm_attach_id(attach_id)
    if not aid:
        raise PapersError("BAD_ID", "人才 ID 无效")
    return await _request("/talents/" + aid, timeout=30.0)


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
