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


_JOB_WAIT = {"running", "pending", "queued"}
_JOB_OK = {"done", "succeeded", "success", "completed"}
_JOB_FAIL = {"interrupted", "failed", "error", "cancelled"}


async def _v1_http(method: str, path: str, *, json_body=None, data=None, files=None, timeout: float = 60.0) -> dict:
    """对接 /api/v1 写接口与任务查询（不占用长时间全局锁）。"""
    cfg = load_config()
    if not _uses_api_key(cfg):
        raise PapersError("NOT_CONFIGURED", "未配置论文系统 papers.apiKey")
    rel = str(path or "")
    if not rel.startswith("/"):
        rel = "/" + rel
    if not rel.startswith("/api/"):
        rel = PREFIX + rel
    url = abs_url(cfg["papersBaseUrl"], rel)
    headers = {"X-Api-Key": cfg["papersApiKey"], "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_client_timeout(timeout), trust_env=_trust_env(url), follow_redirects=True) as client:
            r = await client.request(method, url, headers=headers, json=json_body, data=data, files=files)
    except httpx.HTTPError as e:
        raise PapersError("NETWORK", str(e)[:200])
    if r.status_code == 201:
        raise PapersError("BAD_RESPONSE", "论文系统返回 201（对接约定视为失败）", 201)
    if r.status_code >= 400:
        raise _err_from_body(r.status_code, r.text)
    try:
        payload = r.json()
    except Exception:
        raise PapersError("BAD_RESPONSE", "论文系统响应不是合法 JSON")
    if not isinstance(payload, dict):
        raise PapersError("BAD_RESPONSE", "论文系统响应不是 JSON 对象")
    return payload


def _job_id_of(data: dict) -> str:
    return str((data or {}).get("job_id") or (data or {}).get("id") or "").strip()


def _clean_author_name(raw: str) -> str:
    s = re.sub(r"\(\s*\)", "", str(raw or ""))
    s = re.sub(r"\s+", " ", s).strip(" /")
    return s


def papers_json_from_search(data: dict) -> list:
    out, seen = [], set()
    for p in (data or {}).get("papers") or []:
        if not isinstance(p, dict):
            continue
        title = str(p.get("title") or p.get("title_zh") or "").strip()
        if len(title) < 6:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        item = {"title": title}
        zh = str(p.get("title_zh") or "").strip()
        if zh:
            item["title_zh"] = zh
        for k in ("doi", "year", "journal", "authors"):
            v = str(p.get(k) or "").strip()
            if v:
                item[k] = v
        out.append(item)
    return out


async def search_author(author: str, *, per_page: int = 50) -> dict:
    """POST /api/search：OpenAlex 作者检索，返回 papers[]。"""
    name = _clean_author_name(author)
    if len(name) < 3:
        raise PapersError("BAD_ID", "检索姓名为空")
    return await _v1_http(
        "POST", "/api/search",
        data={"author": name, "per_page": str(max(5, min(int(per_page or 50), 100)))},
        timeout=90.0,
    )


async def search_authors(names: list) -> tuple[list, str]:
    tried = []
    for raw in names or []:
        nm = _clean_author_name(raw)
        if len(nm) < 3 or nm in tried:
            continue
        tried.append(nm)
        try:
            data = await search_author(nm)
        except PapersError as e:
            if e.code in ("AUTH", "FORBIDDEN", "NOT_CONFIGURED"):
                raise
            continue
        rows = papers_json_from_search(data)
        if rows:
            return rows, nm
    return [], ""


async def start_papers_job(attach_id: str, *, refresh: bool = True, papers_json=None, name: str = "") -> dict:
    aid = norm_attach_id(attach_id)
    if not aid:
        raise PapersError("BAD_ID", "人才 ID 无效")
    body = {"attach_id": aid, "refresh": bool(refresh)}
    if str(name or "").strip():
        body["name"] = str(name).strip()
    if papers_json:
        body["papers_json"] = papers_json
    data = await _v1_http("POST", "/papers/jobs", json_body=body, timeout=60.0)
    jid = _job_id_of(data)
    if not jid:
        raise PapersError("BAD_RESPONSE", "论文构建任务未返回 job_id")
    data["job_id"] = jid
    return data


async def get_job(job_id: str) -> dict:
    jid = str(job_id or "").strip()
    if not jid:
        raise PapersError("BAD_ID", "任务 ID 无效")
    return await _v1_http("GET", "/jobs/" + jid, timeout=30.0)


async def wait_job(job_id: str, *, timeout_sec: int = 600) -> dict:
    import time
    t0 = time.monotonic()
    last: dict = {}
    while True:
        last = await get_job(job_id)
        st = str(last.get("status") or "").strip().lower()
        if st in _JOB_OK:
            return last
        if st in _JOB_FAIL:
            err = str(last.get("error") or last.get("message") or last.get("detail") or "")
            raise PapersError("JOB_FAILED", err or ("论文任务失败：" + (st or "unknown")))
        if time.monotonic() - t0 > max(30, int(timeout_sec or 600)):
            raise PapersError("TIMEOUT", "论文任务超时（" + str(timeout_sec) + "s）status=" + (st or ""))
        await asyncio.sleep(2.0)


def talent_has_files(data: dict) -> bool:
    cfg = load_config()
    files = public_files(data, cfg.get("papersBaseUrl") or "")
    att = data.get("attachment") if isinstance((data or {}).get("attachment"), dict) else {}
    return bool(files) or bool(att.get("ready"))


async def ensure_talent(
    attach_id: str,
    *,
    papers_json=None,
    name: str = "",
    names: list | None = None,
    timeout_sec: int = 600,
) -> tuple[dict, str]:
    """先 GET 档案；没有 PDF 时作者检索 + POST /api/v1/papers/jobs 再拉取。"""
    aid = norm_attach_id(attach_id)
    if not aid:
        raise PapersError("BAD_ID", "人才 ID 无效")
    cfg = load_config()
    if not cfg.get("papersConfigured"):
        raise PapersError("NOT_CONFIGURED", "未配置论文系统")
    if not _uses_api_key(cfg):
        return await get_talent(aid), "论文系统（会话模式）仅查询档案 attach_id=" + aid
    try:
        data = await get_talent(aid)
        if talent_has_files(data):
            return data, "论文系统已有可下载档案 attach_id=" + aid
        had_archive = True
    except PapersError as e:
        if e.code != "NOT_FOUND":
            raise
        had_archive = False

    extra = [x for x in (papers_json or []) if isinstance(x, dict)]
    name_list = []
    for raw in [name] + list(names or []):
        nm = _clean_author_name(raw)
        if nm and nm not in name_list:
            name_list.append(nm)
    search_note = ""
    if not extra and name_list:
        extra, used = await search_authors(name_list)
        if extra:
            search_note = "作者检索「" + used + "」命中 " + str(len(extra)) + " 篇"
        else:
            search_note = "作者检索未命中（" + " / ".join(name_list[:3]) + "）"

    bits = ["论文系统有档案但无 PDF" if had_archive else "论文系统无档案"]
    if search_note:
        bits.append(search_note)
    bits.append("已提交构建任务 attach_id=" + aid)
    job = await start_papers_job(
        aid,
        refresh=not bool(extra),
        papers_json=extra or None,
        name=name_list[0] if name_list else name,
    )
    note = "；".join(bits) + " job_id=" + job["job_id"]
    waited = await wait_job(job["job_id"], timeout_sec=timeout_sec)
    st = str(waited.get("status") or "")
    last_miss = None
    for _ in range(6):
        try:
            data = await get_talent(aid)
            return data, note + " 状态=" + st
        except PapersError as e:
            if e.code != "NOT_FOUND":
                raise
            last_miss = e
            await asyncio.sleep(2.0)
    raise last_miss or PapersError("NOT_FOUND", "论文系统没有该人才档案 attach_id=" + aid)


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
    for p in _papers_rows(data):
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


def _papers_rows(data: dict) -> list:
    if not isinstance(data, dict):
        return []
    rows = data.get("papers")
    if isinstance(rows, list):
        return rows
    inner = data.get("data")
    if isinstance(inner, dict) and isinstance(inner.get("papers"), list):
        return inner.get("papers") or []
    return []


def public_catalog(data: dict) -> list:
    """题录（供申报书代表性论文栏补写，不要求已有 PDF）。"""
    out, seen = [], set()
    if not isinstance(data, dict):
        return out
    for p in _papers_rows(data):
        if not isinstance(p, dict):
            continue
        title = str(p.get("title") or p.get("title_zh") or "").strip()
        if len(title) < 6:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": str(p.get("title") or title).strip(),
            "titleZh": str(p.get("title_zh") or "").strip(),
            "year": str(p.get("year") or "").strip(),
            "journal": str(p.get("journal") or "").strip(),
            "doi": str(p.get("doi") or "").strip(),
            "authors": str(p.get("authors") or "").strip(),
            "paperId": str(p.get("paper_id") or p.get("id") or "").strip(),
        })
    return out[:12]
