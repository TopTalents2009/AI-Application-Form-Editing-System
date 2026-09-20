"""对外开放 API：/api/v1/* 用 API Key 鉴权，不走网页登录会话。"""
from __future__ import annotations
import base64
import os
import re
import time
from collections import deque
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .. import apikeys
from ..config import APP_VERSION
from ..pdf_app import ALLOWED_APP_EXT, APP_EXT_HINT, ext_of
from ..opinion_extract import ALLOWED_OPINION_EXT
from ..runner import sanitize

MAX_FILE_BYTES = 40 * 1024 * 1024
_RATE: dict[int, deque] = {}


def _truthy(v) -> bool:
    s = str(v or "").strip().lower()
    return s in ("1", "true", "yes", "on", "y")


def _err_detail(resp) -> str:
    try:
        if isinstance(resp, JSONResponse):
            body = resp.body
            if isinstance(body, (bytes, bytearray)):
                import json
                j = json.loads(body.decode("utf-8"))
                return str(j.get("detail") or j.get("error") or "")[:255]
    except Exception:
        pass
    return ""


class OpenApiGate(BaseHTTPMiddleware):
    """校验 API Key、记调用日志。GET /api/v1/health 可不带密钥。"""

    async def dispatch(self, request: Request, call_next):
        p = request.url.path or ""
        if not p.startswith("/api/v1"):
            return await call_next(request)
        t0 = time.perf_counter()
        request.state.api_key = None
        request.state.api_key_row = None
        request.state.openapi_task_id = ""
        skip_auth = request.method in ("GET", "HEAD") and p.rstrip("/") in ("/api/v1/health",)
        raw = apikeys.key_from_request(request)
        auth_err = ""
        status_override = None
        if not skip_auth:
            if not raw:
                auth_err = "缺少 API Key（请求头 X-Api-Key 或 Authorization: Bearer）"
                status_override = 401
            else:
                try:
                    row = apikeys.authenticate(raw)
                    kid = int(row["id"])
                    now = time.time()
                    bucket = _RATE.setdefault(kid, deque())
                    while bucket and now - bucket[0] > 60:
                        bucket.popleft()
                    if len(bucket) >= apikeys.RATE_PER_MIN:
                        auth_err = "调用过于频繁，请稍后再试"
                        status_override = 429
                    else:
                        bucket.append(now)
                        request.state.api_key_row = row
                        request.state.api_key = apikeys.public_key(row)
                except apikeys.ApiKeyError as e:
                    auth_err = e.message
                    status_override = e.status
                except Exception as e:
                    auth_err = "密钥校验失败：" + str(e)[:120]
                    status_override = 503
        if status_override:
            resp = JSONResponse({"detail": auth_err}, status_code=status_override)
        else:
            resp = await call_next(request)
        dur = int((time.perf_counter() - t0) * 1000)
        if skip_auth:
            return resp
        row = getattr(request.state, "api_key_row", None)
        if row:
            try:
                apikeys.touch_key(int(row["id"]))
            except Exception:
                pass
        if not row and raw:
            row = {"prefix": raw[:12], "name": ""}
        tid = str(getattr(request.state, "openapi_task_id", "") or "")
        if not tid:
            m = re.match(r"^/api/v1/tasks/([^/]+)", p)
            if m:
                tid = m.group(1)[:32]
        try:
            apikeys.log_call(
                key_row=row,
                method=request.method,
                path=p[:180],
                status_code=int(getattr(resp, "status_code", 0) or 0),
                ip=apikeys.client_ip(request),
                user_agent=(request.headers.get("user-agent") or "")[:180],
                task_id=tid,
                error=auth_err or _err_detail(resp),
                duration_ms=dur,
            )
        except Exception:
            pass
        return resp


def _key_row(request: Request) -> dict:
    row = getattr(request.state, "api_key_row", None)
    if not row:
        raise HTTPException(401, "缺少 API Key")
    return row


def public_task(t: dict | None) -> dict:
    if not t:
        raise HTTPException(404, "任务不存在")
    app = dict(t.get("app") or {})
    logs = t.get("log") or []
    return {
        "id": t.get("id"),
        "status": t.get("status"),
        "createdAt": t.get("createdAt"),
        "finishedAt": t.get("finishedAt"),
        "error": t.get("error"),
        "applyWarning": t.get("applyWarning"),
        "autoApply": bool(t.get("autoApply")),
        "appliedAt": t.get("appliedAt") or "",
        "app": {
            "name": app.get("name") or app.get("inputName") or "",
            "mode": app.get("mode") or "",
            "personName": t.get("personName") or app.get("personName") or "",
            "attachId": t.get("attachId") or app.get("attachId") or "",
        },
        "opinions": [{"name": o.get("name")} for o in (t.get("opinions") or []) if isinstance(o, dict)],
        "deliverables": [{"name": o.get("name"), "size": o.get("size", 0)} for o in (t.get("deliverables") or [])],
        "log": [str(x) for x in logs[-30:]],
    }


def _guard(request: Request, t: dict | None) -> dict:
    if not t:
        raise HTTPException(404, "任务不存在")
    row = _key_row(request)
    kid = int(row.get("id") or 0)
    if int(t.get("apiKeyId") or 0) != kid:
        raise HTTPException(404, "任务不存在")
    request.state.openapi_task_id = str(t.get("id") or "")
    return t


def _edits_from_plan(plan: dict) -> tuple[list, list]:
    raw = (plan or {}).get("edits") or []
    edits = []
    for e2 in raw:
        if not isinstance(e2, dict):
            continue
        find = str(e2.get("find", "")).strip()
        if not find:
            continue
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
    leftovers = [str(x) for x in ((plan or {}).get("leftovers") or []) if str(x).strip()]
    return edits, leftovers


def _file_b64(name: str, data: bytes) -> dict:
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(400, "文件过大（单文件不超过 40MB）：" + name)
    return {"name": sanitize(name), "dataB64": base64.b64encode(data).decode("ascii")}


async def _read_upload(up) -> tuple[str, bytes]:
    name = sanitize(getattr(up, "filename", None) or "file")
    data = await up.read()
    return name, data


async def _body_from_request(request: Request) -> dict:
    ctype = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ctype:
        form = await request.form()
        app_up = form.get("app") or form.get("file") or form.get("application")
        if app_up is None or not hasattr(app_up, "read"):
            raise HTTPException(400, "请用表单字段 app 上传申报书文件")
        aname, adata = await _read_upload(app_up)
        if ext_of(aname) not in ALLOWED_APP_EXT:
            raise HTTPException(400, "申报书必须为 " + APP_EXT_HINT)
        ops = []
        raw_ops = form.getlist("opinions") or form.getlist("opinion") or []
        for up in raw_ops:
            if not hasattr(up, "read"):
                continue
            n, d = await _read_upload(up)
            if ext_of(n) not in ALLOWED_OPINION_EXT:
                raise HTTPException(400, "意见类型不支持：" + n)
            ops.append(_file_b64(n, d))
        mode = str(form.get("mode") or "").strip().upper()
        app = _file_b64(aname, adata)
        if mode in ("QM", "HJ"):
            app["mode"] = mode
        return {
            "engine": "api",
            "app": app,
            "opinions": ops,
            "autoApply": _truthy(form.get("autoApply") or form.get("auto_apply")),
        }
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "请提交 JSON 或 multipart/form-data")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体无效")
    return body


def create_router(runner):
    router = APIRouter()

    @router.get("/api/v1/health")
    def v1_health():
        return {"ok": True, "version": APP_VERSION, "service": "申报书智能修改开放 API"}

    @router.get("/api/v1")
    def v1_index(request: Request):
        _key_row(request)
        return {
            "ok": True,
            "version": APP_VERSION,
            "auth": "请求头 X-Api-Key 或 Authorization: Bearer <key>",
            "endpoints": [
                {"method": "GET", "path": "/api/v1/health", "auth": False, "desc": "健康检查"},
                {"method": "POST", "path": "/api/v1/tasks", "auth": True, "desc": "创建任务（multipart 字段 app / opinions，或 JSON dataB64）"},
                {"method": "GET", "path": "/api/v1/tasks", "auth": True, "desc": "列出本密钥创建的任务"},
                {"method": "GET", "path": "/api/v1/tasks/{id}", "auth": True, "desc": "任务状态"},
                {"method": "GET", "path": "/api/v1/tasks/{id}/plan", "auth": True, "desc": "编辑计划"},
                {"method": "POST", "path": "/api/v1/tasks/{id}/apply", "auth": True, "desc": "确认写入；空 body 则按计划自动采用 Gemini 意见"},
                {"method": "POST", "path": "/api/v1/tasks/{id}/retry", "auth": True, "desc": "失败或待确认时重试出计划"},
                {"method": "GET", "path": "/api/v1/tasks/{id}/files", "auth": True, "desc": "下载产出，query: name、dir=output|input"},
            ],
        }

    @router.post("/api/v1/tasks")
    async def v1_create(request: Request):
        row = _key_row(request)
        body = await _body_from_request(request)
        auto = bool(body.get("autoApply") or body.get("auto_apply"))
        mode = str((body.get("app") or {}).get("mode") or body.get("mode") or "").strip().upper()
        if mode in ("QM", "HJ") and isinstance(body.get("app"), dict):
            body["app"]["mode"] = mode
        body["engine"] = "api"
        body["source"] = "openapi"
        body["autoApply"] = auto
        body["apiKeyId"] = int(row["id"])
        body["apiActor"] = "API:" + str(row.get("name") or row.get("prefix") or "")
        owner = apikeys.task_owner_of(row)
        try:
            t = runner.create(body, owner=owner)
        except ValueError as e:
            raise HTTPException(400, str(e))
        runner.enqueue(t["id"])
        request.state.openapi_task_id = t["id"]
        return {"ok": True, "id": t["id"], "status": t.get("status"), "autoApply": auto}

    @router.get("/api/v1/tasks")
    def v1_list(request: Request):
        row = _key_row(request)
        kid = int(row["id"])
        items = []
        for t in runner.list_meta():
            if int(t.get("apiKeyId") or 0) != kid:
                continue
            items.append({
                "id": t.get("id"),
                "status": t.get("status"),
                "createdAt": t.get("createdAt"),
                "error": t.get("error"),
                "app": t.get("app") or {},
                "personName": t.get("personName") or "",
                "attachId": t.get("attachId") or "",
                "deliverables": t.get("deliverables") or [],
            })
        return {"ok": True, "tasks": items}

    @router.get("/api/v1/tasks/{tid}")
    def v1_get(tid: str, request: Request):
        t = _guard(request, runner.get(tid))
        return {"ok": True, **public_task(t)}

    @router.get("/api/v1/tasks/{tid}/plan")
    def v1_plan(tid: str, request: Request):
        t = _guard(request, runner.get(tid))
        plan = runner.load_plan(t)
        if plan is None:
            raise HTTPException(404, "尚未生成编辑计划")
        return plan

    @router.post("/api/v1/tasks/{tid}/apply")
    async def v1_apply(tid: str, request: Request):
        t = _guard(request, runner.get(tid))
        if t["status"] not in ("planned", "failed"):
            raise HTTPException(400, "当前状态 " + t["status"] + " 不能应用（仅待确认/失败可）")
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        raw = body.get("edits")
        leftovers = None
        if isinstance(raw, list) and raw:
            edits = []
            for e2 in raw:
                if not isinstance(e2, dict):
                    continue
                find = str(e2.get("find", "")).strip()
                if not find:
                    continue
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
        else:
            plan = runner.load_plan(t)
            if plan is None:
                raise HTTPException(404, "尚未生成编辑计划")
            edits, leftovers = _edits_from_plan(plan)
        if not edits and not leftovers:
            raise HTTPException(400, "没有可应用的编辑")
        actor = "API:" + str(_key_row(request).get("name") or "")
        await runner.apply_confirmed(t, edits, leftovers, actor=actor)
        out = public_task(t)
        if t["status"] == "failed":
            raise HTTPException(400, str(t.get("error") or "写入失败"))
        return {"ok": True, **out}

    @router.post("/api/v1/tasks/{tid}/retry")
    def v1_retry(tid: str, request: Request):
        _guard(request, runner.get(tid))
        try:
            return {"ok": True, "id": runner.replan(tid)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.get("/api/v1/tasks/{tid}/files")
    def v1_file(tid: str, request: Request, dir: str = "output", name: str = "", stamp: str = ""):
        t = _guard(request, runner.get(tid))
        name = os.path.basename(name)
        stamp = str(stamp or "").strip()
        if dir == "versions":
            if not re.fullmatch(r"[\d_-]{8,24}", stamp):
                raise HTTPException(400, "非法版本")
            base = os.path.join(t["dir"], "versions", stamp)
        elif dir in ("input", "output"):
            base = (t["dir"] + "/input") if dir == "input" else (t["dir"] + "/work/output")
        else:
            raise HTTPException(400, "非法目录")
        fp = os.path.join(base, name)
        if not name or not os.path.isfile(fp):
            raise HTTPException(404, "文件不存在")
        return FileResponse(fp, filename=name, headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name),
        })

    @router.get("/api/v1/tasks/{tid}/ext-files/{fid}")
    async def v1_ext_file(tid: str, fid: str, request: Request):
        t = _guard(request, runner.get(tid))
        from ..attachments import load_private, fetch_upstream
        from ..papers import PapersError
        from fastapi.responses import Response
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

    return router
