"""批次相关路由"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import json, os
from urllib.parse import quote

def create_router(runner, batches):
    router = APIRouter()

    @router.post("/api/batches")
    async def create_batch(body: dict):
        try:
            meta = await batches.create(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": meta["id"]}

    @router.get("/api/batches")
    def list_batches():
        return {"batches": batches.list_meta()}

    @router.get("/api/batches/{bid}")
    def get_batch(bid: str):
        b = batches.get(bid)
        if not b: raise HTTPException(404, "批次不存在")
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
    def start_batch(bid: str, body: dict | None = None):
        try:
            ids = batches.start(bid, body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"taskIds": ids}

    @router.get("/api/batches/{bid}/files")
    def batch_file(bid: str, name: str = ""):
        b = batches.get(bid)
        if not b: raise HTTPException(404, "批次不存在")
        name = os.path.basename(name)
        fp = os.path.join(b["dir"], "input", name)
        if not os.path.isfile(fp): raise HTTPException(404, "文件不存在")
        return FileResponse(fp, headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})

    return router
