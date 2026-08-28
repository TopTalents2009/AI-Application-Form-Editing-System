"""FastAPI 入口：路由挂载、静态托管、恢复中断任务"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from .config import STATIC_DIR, DATA_DIR
from .runner import TaskStore
from .batch import BatchStore
from .routes import tasks as tasks_routes
from .routes import batches as batches_routes

runner = TaskStore()
batches = BatchStore(runner=runner)

app = FastAPI(title="申报书智能修改系统", docs_url=None, redoc_url=None)

class NoCacheStatic(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p == "/" or p.startswith("/public"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

app.add_middleware(NoCacheStatic)

tasks_router = tasks_routes.create_router(runner)
batches_router = batches_routes.create_router(runner, batches)
app.include_router(tasks_router)
app.include_router(batches_router)

@app.get("/api/config")
def api_config():
    from .config import public_config
    return public_config()

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
    runner.bind_loop(asyncio.get_running_loop())
    # 恢复语义已在各 Store 的 load_all 中处理（非终态→failed/中断）
    pass
