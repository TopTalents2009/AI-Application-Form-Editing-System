"""ChatInsight：静态 SPA + 管理员反代解析服务 /api。"""
from __future__ import annotations

from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse

from ..config import STATIC_DIR, httpx_trust_env

CHATINSIGHT_DIR = STATIC_DIR / "chatinsight"
PARSER_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
ASSET_EXT = {
    "js", "css", "map", "svg", "png", "jpg", "jpeg", "gif", "webp", "ico",
    "woff", "woff2", "ttf", "eot", "json", "txt", "wasm",
}
HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}
REQ_SKIP = HOP | {"cookie", "authorization", "content-length"}
RESP_SKIP = HOP | {"content-encoding", "content-length"}

router = APIRouter()


def _user(request: Request) -> dict | None:
    u = getattr(request.state, "user", None)
    return u if isinstance(u, dict) else None


def _require_admin(request: Request) -> dict:
    u = _user(request)
    if not u:
        raise HTTPException(401, "未登录")
    if u.get("role") != "admin":
        raise HTTPException(403, "仅管理员可打开解析控制台")
    return u


def safe_rel_path(rel: str) -> str | None:
    """拒绝跳出 static/chatinsight 的相对路径；空或目录则视为 SPA。"""
    rel = str(rel or "").replace("\\", "/").lstrip("/")
    if not rel or rel.endswith("/"):
        return ""
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


def looks_like_asset(rel: str) -> bool:
    name = str(rel or "").rsplit("/", 1)[-1]
    if "." not in name:
        return False
    return name.rsplit(".", 1)[-1].lower() in ASSET_EXT


def parser_upstream_url(path: str, query: str = "") -> str:
    from ..wecom_client import _root
    rel = str(path or "").replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(400, "非法路径")
    joined = "/".join(parts)
    url = _root().rstrip("/") + "/api"
    if joined:
        url += "/" + joined
    q = str(query or "").lstrip("?")
    if q:
        url += "?" + q
    return url


def _chatinsight_file(rel: str):
    if not rel:
        return None
    root = CHATINSIGHT_DIR.resolve()
    target = (CHATINSIGHT_DIR / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if target.is_file():
        return target
    return None


@router.api_route("/chatinsight", methods=["GET", "HEAD"])
@router.api_route("/chatinsight/{full_path:path}", methods=["GET", "HEAD"])
def chatinsight_spa(request: Request, full_path: str = ""):
    u = _user(request)
    if not u:
        nxt = request.url.path or "/chatinsight/"
        if not nxt.endswith("/") and not looks_like_asset(full_path):
            nxt = nxt.rstrip("/") + "/"
        return RedirectResponse("/login?next=" + quote(nxt, safe="/"), status_code=303)
    if u.get("role") != "admin":
        raise HTTPException(403, "仅管理员可打开解析控制台")
    rel = safe_rel_path(full_path)
    if rel is None:
        raise HTTPException(400, "非法路径")
    hit = _chatinsight_file(rel)
    if hit is not None:
        return FileResponse(hit)
    if looks_like_asset(rel):
        raise HTTPException(404, "资源不存在")
    index = CHATINSIGHT_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(503, "解析控制台尚未构建，请运行 python scripts/build_chatinsight.py")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _forward_request_headers(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in request.headers.items():
        if k.lower() in REQ_SKIP:
            continue
        out[k] = v
    from ..wecom_client import _key
    key = _key()
    if key:
        out["X-API-Key"] = key
    if "accept" not in {x.lower() for x in out}:
        out["Accept"] = "*/*"
    return out


def _forward_response_headers(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in RESP_SKIP:
            continue
        out[k] = v
    return out


@router.api_route("/parser-api", methods=list(PARSER_METHODS))
@router.api_route("/parser-api/{path:path}", methods=list(PARSER_METHODS))
async def parser_proxy(request: Request, path: str = ""):
    _require_admin(request)
    url = parser_upstream_url(path, str(request.url.query or ""))
    body = await request.body()
    timeout = httpx.Timeout(120.0, connect=5.0)
    client = httpx.AsyncClient(timeout=timeout, trust_env=httpx_trust_env(), follow_redirects=True)
    try:
        req = client.build_request(
            request.method,
            url,
            headers=_forward_request_headers(request),
            content=body if body else None,
        )
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        return JSONResponse({"detail": "解析服务不可达：" + str(e)[:160]}, status_code=502)

    headers = _forward_response_headers(resp.headers)

    async def _iter():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(_iter(), status_code=resp.status_code, headers=headers)
