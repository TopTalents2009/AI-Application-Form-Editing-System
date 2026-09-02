"""批次相关路由"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
import json, os
from urllib.parse import quote


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _guard_batch(request: Request, b: dict | None) -> dict:
    """非管理员只能访问自己创建的批次；无归属的历史批次仅管理员可见。"""
    if not b:
        raise HTTPException(404, "批次不存在")
    u = _user(request)
    if u.get("role") != "admin" and str(b.get("owner") or "") != str(u.get("username") or ""):
        raise HTTPException(404, "批次不存在")
    return b


def create_router(runner, batches):
    router = APIRouter()

    @router.post("/api/batches")
    async def create_batch(body: dict, request: Request):
        try:
            meta = await batches.create(body, owner=(_user(request).get("username") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": meta["id"]}

    @router.get("/api/batches")
    def list_batches(request: Request):
        u = _user(request)
        items = batches.list_meta()
        if u.get("role") != "admin":
            uname = str(u.get("username") or "")
            items = [x for x in items if str(x.get("owner") or "") == uname]
        return {"batches": items}

    @router.get("/api/batches/{bid}")
    def get_batch(bid: str, request: Request):
        b = _guard_batch(request, batches.get(bid))
        clone = {k: v for k, v in b.items() if k != "dir"}
        ids = b.get("taskIds") or []
        if ids:
            sums = []
            for tid in ids:
                t = runner.get(tid)
                if t:
                    sums.append({"id": tid, "app": t["app"]["name"], "status": t["status"],
                                 "deliverables": [{"name": o["name"], "size": o["size"]} for o in t.get("deliverables", [])]})
                else:
                    sums.append({"id": tid, "status": "missing", "app": "(缺失)", "deliverables": []})
            clone["taskSummaries"] = sums
        return clone

    @router.post("/api/batches/{bid}/start")
    def start_batch(bid: str, body: dict | None = None, request: Request = None):
        _guard_batch(request, batches.get(bid))
        try:
            ids = batches.start(bid, body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"taskIds": ids}

    @router.get("/api/batches/{bid}/files")
    def batch_file(bid: str, request: Request, name: str = ""):
        b = _guard_batch(request, batches.get(bid))
        name = os.path.basename(name)
        fp = os.path.join(b["dir"], "input", name)
        if not os.path.isfile(fp): raise HTTPException(404, "文件不存在")
        return FileResponse(fp, headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})

    return router
