"""FastAPI 入口：路由挂载、静态托管、恢复中断任务"""
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from .config import STATIC_DIR, DATA_DIR
from .runner import TaskStore
from .batch import BatchStore
from .routes import tasks as tasks_routes
from .routes import batches as batches_routes
from .routes import auth as auth_routes
from .routes import admin as admin_routes
from .routes import feedback as feedback_routes
from .routes import wecom_board as wecom_board_routes
from .routes import openapi as openapi_routes
from .routes import api_apply as api_apply_routes
from .routes import talent_files as talent_files_routes

runner = TaskStore()
batches = BatchStore(runner=runner)

app = FastAPI(title="申报书智能修改系统", docs_url=None, redoc_url=None)

def _is_app_shell(p: str) -> bool:
    """首页与任务深链、后台页：未登录也先下发 HTML，由前端跳登录并带回 next。"""
    p = str(p or "").rstrip("/") or "/"
    if p in ("/", "/admin"):
        return True
    if p.startswith("/t/"):
        tid = p[3:]
        return bool(tid) and "/" not in tid
    return False


class NoCacheStatic(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p == "/" or p.startswith("/public") or p.startswith("/t/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

app.add_middleware(NoCacheStatic)

# 无需登录即可访问的路径
PUBLIC_PATHS = (
    "/login", "/register", "/portal",
    "/api/auth/login", "/api/auth/register",
    "/api/auth/portal-config", "/api/auth/portal-login",
    "/client-extract",
)

def _is_public(p: str) -> bool:
    p = str(p or "").rstrip("/") or "/"
    if p in PUBLIC_PATHS or p.startswith("/public"):
        return True
    if p.startswith("/api/auth/portal-"):
        return True
    return False


class AuthGate(BaseHTTPMiddleware):
    """校验会话：cookie 或 Authorization Bearer。未登录 API 返回 401，页面跳 /login。"""

    async def dispatch(self, request: Request, call_next):
        from . import auth
        p = request.url.path
        if p.startswith("/public") or p.startswith("/api/v1"):
            request.state.user = None
            return await call_next(request)
        try:
            request.state.user = auth.user_by_token(auth.session_token_from_request(request))
        except Exception as e:
            return JSONResponse({"detail": "数据库不可用：" + str(e)[:160]}, status_code=503)
        if request.state.user or _is_public(p):
            return await call_next(request)
        if p.startswith("/api"):
            return JSONResponse({"detail": "未登录"}, status_code=401)
        # iframe 里 cookie 常被浏览器丢掉；主页面仍下发 HTML，由前端带 Bearer 续上会话
        if request.method in ("GET", "HEAD") and _is_app_shell(p):
            return await call_next(request)
        return RedirectResponse("/login", status_code=303)

app.add_middleware(AuthGate)
app.add_middleware(openapi_routes.OpenApiGate)

tasks_router = tasks_routes.create_router(runner)
batches_router = batches_routes.create_router(runner, batches)
app.include_router(tasks_router)
app.include_router(batches_router)
app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(feedback_routes.router)
app.include_router(wecom_board_routes.router)
app.include_router(openapi_routes.create_router(runner))
app.include_router(api_apply_routes.router)
app.include_router(talent_files_routes.router)

@app.get("/api/config")
def api_config(request: Request):
    from .config import editor_config, frontend_config
    u = getattr(request.state, "user", None) or {}
    if u.get("role") == "admin":
        return editor_config()
    return frontend_config()

def _require_admin(request: Request):
    u = getattr(request.state, "user", None)
    if not u:
        from fastapi import HTTPException
        raise HTTPException(401, "未登录")
    if u.get("role") != "admin":
        from fastapi import HTTPException
        raise HTTPException(403, "仅管理员可修改配置")

@app.post("/api/config")
def api_config_save(body: dict, request: Request):
    from fastapi import HTTPException
    from .config import save_config
    _require_admin(request)
    try:
        return save_config(body, save_as_default=bool((body or {}).get("saveAsDefault")))
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post("/api/config/restore-default")
def api_config_restore(request: Request):
    from fastapi import HTTPException
    from .config import restore_default_config
    _require_admin(request)
    try:
        return restore_default_config()
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post("/api/config/probe")
async def api_config_probe(request: Request):
    """登录用户可检测已配置模型是否能真正完成一次 chat。不返回密钥。"""
    from fastapi import HTTPException
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    from .llm import probe_models
    from .config import model_family
    mid = body.get("model")
    if (u or {}).get("role") != "admin":
        mid = mid if model_family(str(mid or "")) == "gemini" else "gemini"
    return await probe_models(mid)

@app.get("/api/pool/health")
async def api_pool_health():
    from .pool import health
    return await health()

@app.get("/api/papers/health")
async def api_papers_health():
    from .papers import health
    return await health()

@app.post("/api/project-proof/probe")
async def api_project_proof_probe(request: Request):
    """检测项目证明生成接口是否可达、密钥是否被接受。不返回密钥。"""
    _require_admin(request)
    import httpx
    from .config import load_config
    from .project_proof import generate_headers
    cfg = (load_config().get("projectProof") or {}).get("generate") or {}
    base = str(cfg.get("baseUrl") or "").rstrip("/")
    path = str(cfg.get("path") or "/api/external/documents")
    if not cfg.get("configured") or not base:
        return {"ok": False, "service_ok": False, "auth_ok": False, "error": "生成接口未配置"}
    url = base + (path if path.startswith("/") else "/" + path)
    headers = generate_headers(cfg)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0), trust_env=False) as client:
            r = await client.post(url, headers=headers, json={"letters": []})
    except httpx.HTTPError as e:
        return {"ok": False, "service_ok": False, "auth_ok": False, "error": "无法连接：" + str(e)[:160]}
    if r.status_code == 401:
        return {"ok": False, "service_ok": True, "auth_ok": False, "status": 401, "error": "服务可达，但 API Key 无效"}
    if r.status_code >= 400:
        detail = ""
        try:
            body = r.json()
            if isinstance(body, dict):
                detail = str(body.get("error") or body.get("detail") or "")[:160]
        except Exception:
            detail = (r.text or "")[:160]
        return {"ok": False, "service_ok": True, "auth_ok": True, "status": r.status_code, "error": detail or ("HTTP " + str(r.status_code))}
    return {"ok": True, "service_ok": True, "auth_ok": True, "status": r.status_code}

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

@app.get("/t/{tid}")
def index_task(tid: str):
    """任务深链：与首页同一套 SPA，地址栏显示当前任务 id。"""
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

@app.get("/client-extract")
def client_extract_disabled():
    """年龄改写界面已下线，旧链接重定向到申报书智能修改首页。"""
    return RedirectResponse("/", status_code=302)

from fastapi.staticfiles import StaticFiles
app.mount("/public", StaticFiles(directory=str(STATIC_DIR)), name="public")

import asyncio

@app.on_event("startup")
def _startup():
    from .config import ensure_default_config
    ensure_default_config()
    runner.bind_loop(asyncio.get_running_loop())
    # 初始化 MySQL 用户库（建库建表 + 初始管理员）
    try:
        from .db import init_db
        info = init_db()
        for n in info.get("notes", []):
            print("[auth] " + n, flush=True)
    except Exception as e:
        print("[auth] MySQL 初始化失败，登录功能不可用：" + str(e)[:200], flush=True)
    try:
        from .wecom_watch import start_watch
        start_watch(asyncio.get_running_loop())
        print("[wecom-watch] 值班已启动：聊天记录同步后增量扫描", flush=True)
    except Exception as e:
        print("[wecom-watch] 启动失败：" + str(e)[:200], flush=True)
