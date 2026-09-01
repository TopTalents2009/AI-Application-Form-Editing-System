"""FastAPI 入口：路由挂载、静态托管、恢复中断任务"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from .config import STATIC_DIR, DATA_DIR
from .runner import TaskStore
from .batch import BatchStore
from .routes import tasks as tasks_routes
from .routes import batches as batches_routes
from .routes import client_extract as client_extract_routes

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

tasks_router = tasks_routes.create_router(runner)
batches_router = batches_routes.create_router(runner, batches)
client_extract_router = client_extract_routes.create_router()
app.include_router(tasks_router)
app.include_router(batches_router)
app.include_router(client_extract_router)

@app.get("/api/config")
def api_config():
    from .config import editor_config
    return editor_config()

@app.post("/api/config")
def api_config_save(body: dict):
    from fastapi import HTTPException
    from .config import save_config
    try:
        return save_config(body, save_as_default=bool((body or {}).get("saveAsDefault")))
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post("/api/config/restore-default")
def api_config_restore():
    from fastapi import HTTPException
    from .config import restore_default_config
    try:
        return restore_default_config()
    except ValueError as e:
        raise HTTPException(400, str(e))

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
    # 恢复语义已在各 Store 的 load_all 中处理（非终态→failed/中断）
    pass
