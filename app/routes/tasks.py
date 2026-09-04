"""任务相关路由（两阶段：计划 → 人工确认 → 应用）"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
import os


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _guard_task(request: Request, t: dict | None) -> dict:
    """非管理员只能访问自己创建的任务；无归属的历史任务仅管理员可见。"""
    if not t:
        raise HTTPException(404, "任务不存在")
    u = _user(request)
    if u.get("role") != "admin" and str(t.get("owner") or "") != str(u.get("username") or ""):
        raise HTTPException(404, "任务不存在")
    return t


def create_router(runner):
    router = APIRouter()

    @router.post("/api/tasks")
    async def create_task(body: dict, request: Request):
        try:
            t = runner.create(body, owner=(_user(request).get("username") or ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        runner.enqueue(t["id"])
        return {"id": t["id"]}

    @router.get("/api/tasks")
    def list_tasks(request: Request):
        u = _user(request)
        items = runner.list_meta()
        if u.get("role") != "admin":
            uname = str(u.get("username") or "")
            items = [x for x in items if str(x.get("owner") or "") == uname]
        return {"tasks": items}

    @router.get("/api/tasks/{tid}")
    def get_task(tid: str, request: Request):
        t = _guard_task(request, runner.get(tid))
        return {k: v for k, v in t.items() if k != "dir"}

    @router.get("/api/tasks/{tid}/plan")
    def get_plan(tid: str, request: Request):
        t = _guard_task(request, runner.get(tid))
        plan = runner.load_plan(t)
        if plan is None: raise HTTPException(404, "尚未生成编辑计划")
        return plan

    @router.post("/api/tasks/{tid}/apply")
    async def apply_plan(tid: str, body: dict, request: Request):
        t = _guard_task(request, runner.get(tid))
        if t["status"] not in ("planned", "failed"):
            raise HTTPException(400, "当前状态 " + t["status"] + " 不能应用（仅待人工确认/失败重试可）")
        raw = body.get("edits") or []
        edits = []
        for e2 in raw:
            if not isinstance(e2, dict): continue
            find = str(e2.get("find", "")).strip()
            if not find: continue
            edits.append({
                "find": find,
                "replace": str(e2.get("replace", "")),
                "clause": str(e2.get("clause", "")),
                "opinion": str(e2.get("opinion") or e2.get("clause") or ""),
                "opinionGrok": str(e2.get("opinionGrok") or ""),
                "opinionGemini": str(e2.get("opinionGemini") or ""),
                "opinionDoubao": str(e2.get("opinionDoubao") or ""),
                "opName": str(e2.get("opName") or ""),
                "clauseId": str(e2.get("clauseId") or ""),
                "appNo": str(e2.get("appNo") or ""),
                "_sec": str(e2.get("_sec") or e2.get("section") or "其他"),
                "section": str(e2.get("_sec") or e2.get("section") or "其他"),
            })
        leftovers = [str(x) for x in (body.get("leftovers") or []) if str(x).strip()]
        if not edits and not leftovers:
            raise HTTPException(400, "没有可应用的编辑（请至少保留一条有效行）")
        await runner.apply_confirmed(t, edits, leftovers)
        return {"id": tid, "status": t["status"],
                "deliverables": [{"name": o["name"], "size": o["size"]} for o in t.get("deliverables", [])]}

    @router.post("/api/tasks/{tid}/replan")
    def replan(tid: str, request: Request):
        _guard_task(request, runner.get(tid))
        try:
            return {"id": runner.replan(tid)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/api/tasks/{tid}/retry")
    def retry_task(tid: str, request: Request):
        _guard_task(request, runner.get(tid))
        try:
            return {"id": runner.replan(tid)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/api/tasks/{tid}/ext-files/{fid}")
    async def task_ext_file(tid: str, fid: str, request: Request):
        t = _guard_task(request, runner.get(tid))
        from ..attachments import load_private, fetch_upstream
        from ..papers import PapersError
        from fastapi.responses import Response
        from urllib.parse import quote
        priv = load_private(t["dir"], fid)
        if not priv:
            raise HTTPException(404, "附件不存在或计划已过期")
        try:
            content, filename, ctype, _st = await fetch_upstream(priv)
        except PapersError as e:
            raise HTTPException(e.status or 502, e.message)
        except Exception as e:
            raise HTTPException(502, str(e)[:200])
        return Response(
            content=content,
            media_type=ctype or "application/octet-stream",
            headers={
                "Cache-Control": "no-cache",
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename or "file"),
            },
        )

    @router.get("/api/tasks/{tid}/files")
    def task_file(tid: str, request: Request, dir: str = "output", name: str = ""):
        t = _guard_task(request, runner.get(tid))
        if dir not in ("input", "output"): raise HTTPException(400, "非法目录")
        base = (t["dir"] + "/input") if dir == "input" else (t["dir"] + "/work/output")
        name = os.path.basename(name)
        fp = os.path.join(base, name)
        if not name or not os.path.isfile(fp): raise HTTPException(404, "文件不存在")
        from urllib.parse import quote
        return FileResponse(fp, filename=name, headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name),
        })

    return router
