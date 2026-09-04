"""FastAPI 入口：路由挂载、静态托管、恢复中断任务"""
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from .config import STATIC_DIR, DATA_DIR
from .runner import TaskStore
from .batch import BatchStore
from .routes import tasks as tasks_routes
from .routes import batches as batches_routes
from .routes import client_extract as client_extract_routes
from .routes import auth as auth_routes
from .routes import admin as admin_routes
from .routes import feedback as feedback_routes

runner = TaskStore()
batches = BatchStore(runner=runner)

app = FastAPI(title="申报书智能修改系统", docs_url=None, redoc_url=None)

class NoCacheStatic(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p == "/" or p.startswith("/public") or p.startswith("/client-extract"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

app.add_middleware(NoCacheStatic)

# 无需登录即可访问的路径
PUBLIC_PATHS = ("/login", "/register", "/api/auth/login", "/api/auth/register")

class AuthGate(BaseHTTPMiddleware):
    """校验会话 cookie：未登录页面跳 /login，API 返回 401。"""

    async def dispatch(self, request: Request, call_next):
        from . import auth
        p = request.url.path
        if p.startswith("/public"):
            request.state.user = None
            return await call_next(request)
        try:
            request.state.user = auth.user_by_token(request.cookies.get(auth.COOKIE) or "")
        except Exception as e:
            return JSONResponse({"detail": "数据库不可用：" + str(e)[:160]}, status_code=503)
        if request.state.user or p in PUBLIC_PATHS:
            return await call_next(request)
        if p.startswith("/api"):
            return JSONResponse({"detail": "未登录"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

app.add_middleware(AuthGate)

tasks_router = tasks_routes.create_router(runner)
batches_router = batches_routes.create_router(runner, batches)
client_extract_router = client_extract_routes.create_router()
app.include_router(tasks_router)
app.include_router(batches_router)
app.include_router(client_extract_router)
app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(feedback_routes.router)

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

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

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
