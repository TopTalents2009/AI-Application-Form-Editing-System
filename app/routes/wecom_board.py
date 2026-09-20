"""各群记录看板：只读企业微信解析服务。仅管理员。"""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, JSONResponse, StreamingResponse
from urllib.parse import quote
from .. import wecom_client as W

router = APIRouter()


def _admin(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    if u.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return u


def _http(e: W.WecomError) -> HTTPException:
    status = e.status if e.status in (400, 401, 403, 404, 422) else 502
    if e.code == "NETWORK":
        status = 503
    if e.code == "BAD_REQUEST":
        status = 400
    return HTTPException(status, e.message)


@router.get("/api/wecom/health")
async def api_health(request: Request):
    _admin(request)
    return await W.health()


@router.get("/api/wecom/sources")
async def api_sources(request: Request):
    _admin(request)
    try:
        return await W.list_sources()
    except W.WecomError as e:
        raise _http(e)


@router.get("/api/wecom/groups")
async def api_groups(
    request: Request,
    kind: str = Query("group"),
    q: str = Query(""),
    source_id: str = Query(""),
    limit: int = Query(400),
):
    _admin(request)
    k = str(kind or "group").strip().lower()
    if k not in ("group", "all"):
        k = "group"
    try:
        return await W.list_merged_groups(kind=k, q=q, source_id=source_id, limit=limit)
    except W.WecomError as e:
        raise _http(e)


@router.get("/api/wecom/groups/{session_id:path}/messages")
async def api_group_messages(
    session_id: str,
    request: Request,
    source_id: str = Query(""),
    start_date: str = Query(""),
    end_date: str = Query(""),
    offset: int = Query(0),
    limit: int = Query(80),
    tail: bool = Query(False),
):
    _admin(request)
    try:
        return await W.list_merged_messages(
            session_id, source_id=source_id,
            start_date=start_date, end_date=end_date,
            offset=offset, limit=limit, tail=tail,
        )
    except W.WecomError as e:
        raise _http(e)


@router.post("/api/wecom/files")
async def api_file_any(body: dict, request: Request):
    _admin(request)
    copies = body.get("copies") if isinstance(body, dict) else None
    if not isinstance(copies, list):
        copies = []
    try:
        got = await W.fetch_attachment_any(copies)
    except W.WecomError as e:
        raise _http(e)
    if got.get("kind") == "pending":
        return JSONResponse(
            {
                "ok": True,
                "kind": "pending",
                "detail": got.get("detail") or "",
                "tried": got.get("tried") or [],
                "pending": got.get("pending") or [],
            },
            status_code=202,
        )
    if got.get("kind") == "missing":
        raise HTTPException(404, got.get("detail") or "各电脑均未缓存该文件")
    name = str(got.get("filename") or ("wecom-" + str(got.get("message_id") or "file")))
    hit = str(got.get("hit_label") or "")
    headers = {
        "Cache-Control": "no-cache",
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name),
    }
    if hit:
        headers["X-Wecom-Cache-From"] = quote(hit)
    return Response(
        content=got.get("content") or b"",
        media_type=str(got.get("content_type") or "application/octet-stream"),
        headers=headers,
    )


@router.get("/api/wecom/sources/{source_id}/sessions")
async def api_sessions(
    source_id: str,
    request: Request,
    kind: str = Query("group"),
    q: str = Query(""),
    limit: int = Query(200),
):
    _admin(request)
    k = str(kind or "group").strip().lower()
    if k not in ("group", "all"):
        k = "group"
    try:
        return await W.list_sessions(source_id, kind=k, q=q, limit=limit)
    except W.WecomError as e:
        raise _http(e)


@router.get("/api/wecom/sources/{source_id}/messages/{session_id:path}")
async def api_messages(
    source_id: str,
    session_id: str,
    request: Request,
    start_date: str = Query(""),
    end_date: str = Query(""),
    offset: int = Query(0),
    limit: int = Query(80),
):
    _admin(request)
    try:
        return await W.list_messages(
            source_id, session_id,
            start_date=start_date, end_date=end_date,
            offset=offset, limit=limit,
        )
    except W.WecomError as e:
        raise _http(e)


@router.get("/api/wecom/search")
async def api_search(
    request: Request,
    q: str = Query(""),
    source_id: str = Query(""),
    session_id: str = Query(""),
    limit: int = Query(50),
):
    _admin(request)
    try:
        return await W.search(q, source_id=source_id, session_id=session_id, limit=limit)
    except W.WecomError as e:
        raise _http(e)


@router.get("/api/wecom/sources/{source_id}/files/{message_id}")
async def api_file(source_id: str, message_id: int, request: Request, session_id: str = Query("")):
    _admin(request)
    try:
        got = await W.stream_attachment(source_id, message_id, session_id)
    except W.WecomError as e:
        raise _http(e)
    if got.get("kind") == "pending":
        return JSONResponse(
            {"ok": True, "kind": "pending", "job_id": got.get("job_id") or "", "detail": got.get("detail") or "", "filename": got.get("filename") or ""},
            status_code=202,
        )
    if got.get("kind") == "missing":
        raise HTTPException(404, got.get("detail") or "文件不在缓存")
    name = str(got.get("filename") or ("wecom-" + str(message_id)))
    headers = {
        "Cache-Control": "no-cache",
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name),
    }
    if got.get("kind") == "stream":
        return StreamingResponse(
            got["chunks"](),
            media_type=str(got.get("content_type") or "application/octet-stream"),
            headers=headers,
        )
    return Response(
        content=got.get("content") or b"",
        media_type=str(got.get("content_type") or "application/octet-stream"),
        headers=headers,
    )


@router.post("/api/wecom/locate-task")
async def api_locate_task(body: dict, request: Request):
    _admin(request)
    from ..main import runner
    from ..wecom_locate import locate_tasks
    return locate_tasks(runner, body if isinstance(body, dict) else {})


@router.post("/api/wecom/split-preview")
async def api_split_preview(body: dict, request: Request):
    _admin(request)
    from datetime import datetime, timedelta
    from ..main import runner
    from ..wecom_cases import cluster_messages, annotate_existing, split_scan_stats
    from ..wecom_split_llm import assist_split_with_gemini
    body = body if isinstance(body, dict) else {}
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "缺少 session_id")
    start = str(body.get("start_date") or "").strip()[:10]
    end = str(body.get("end_date") or "").strip()[:10]
    if not start and not end:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    try:
        data = await W.list_merged_messages(
            sid, source_id=str(body.get("source_id") or ""),
            start_date=start, end_date=end, full=True,
        )
    except W.WecomError as e:
        raise _http(e)
    session_name = str(body.get("session_name") or data.get("display_name") or "")
    cases = cluster_messages(
        data.get("items") or [],
        session_id=sid,
        session_name=session_name,
        window_hours=body.get("windowHours") or 48,
    )
    cases, gemini_note = await assist_split_with_gemini(
        data.get("items") or [],
        cases,
        session_id=sid,
        session_name=session_name,
        window_hours=body.get("windowHours") or 48,
    )
    annotate_existing(cases, runner)
    scan = split_scan_stats(data.get("items") or [])
    hint = ""
    if not cases:
        hint = "当前日期窗内没有识别到申报书文件。可放宽日期，或确认群里发的是 PDF / Word。"
    elif scan.get("fileCount") and scan.get("appFileCount", 0) < scan.get("fileCount", 0):
        hint = (
            "消息里绝大多数是聊天和其它文件。申报书只认文件名含「申报书/申请表」，"
            "或几乎只有人名的 PDF/Word；资料清单、聘用意向书、唯一申报承诺、护照等一律不算。"
        )
    return {
        "ok": True,
        "session_id": sid,
        "session_name": session_name,
        "start_date": start,
        "end_date": end,
        "messageCount": data.get("total") or 0,
        "count": len(cases),
        "ready": sum(1 for c in cases if c.get("ready") and not c.get("existing")),
        "cases": cases,
        "gemini": gemini_note,
        "fileCount": scan.get("fileCount") or 0,
        "appFileCount": scan.get("appFileCount") or 0,
        "opinionFileCount": scan.get("opinionFileCount") or 0,
        "ignoredFileCount": scan.get("ignoredFileCount") or 0,
        "ignoredSample": scan.get("ignoredSample") or [],
        "hint": hint,
    }


@router.post("/api/wecom/split-create")
async def api_split_create(body: dict, request: Request):
    _admin(request)
    import asyncio, base64
    from datetime import datetime, timedelta
    from ..main import runner
    from ..wecom_cases import cluster_messages, annotate_existing, opinion_txt_bytes
    from ..runner import sanitize
    u = _admin(request)
    body = body if isinstance(body, dict) else {}
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "缺少 session_id")
    start = str(body.get("start_date") or "").strip()[:10]
    end = str(body.get("end_date") or "").strip()[:10]
    if not start and not end:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    try:
        data = await W.list_merged_messages(
            sid, source_id=str(body.get("source_id") or ""),
            start_date=start, end_date=end, full=True,
        )
    except W.WecomError as e:
        raise _http(e)
    session_name = str(body.get("session_name") or data.get("display_name") or "")
    cases = cluster_messages(
        data.get("items") or [],
        session_id=sid,
        session_name=session_name,
        window_hours=body.get("windowHours") or 48,
    )
    annotate_existing(cases, runner)
    posted = dict(body.get("case")) if isinstance(body.get("case"), dict) else None
    want = body.get("caseIds")
    want_set = {str(x) for x in want} if isinstance(want, list) and want else set()
    if posted:
        if not posted.get("id") and want_set:
            posted["id"] = next(iter(want_set))
        by_id = {str(c.get("id") or ""): c for c in cases}
        hit = by_id.get(str(posted.get("id") or ""))
        if hit and hit.get("existing"):
            posted["existing"] = hit["existing"]
        papp = posted.get("app") if isinstance(posted.get("app"), dict) else {}
        happ = hit.get("app") if hit and isinstance(hit.get("app"), dict) else {}
        if not papp.get("copies") and happ.get("copies"):
            papp = dict(papp)
            papp["copies"] = happ["copies"]
            posted["app"] = papp
            posted["ready"] = True
        if not posted.get("opinions") and hit and hit.get("opinions"):
            posted["opinions"] = hit["opinions"]
        cases = [posted]
    elif want_set:
        cases = [c for c in cases if str(c.get("id") or "") in want_set]
        if not cases:
            return {
                "ok": False,
                "created": [],
                "skipped": [],
                "errors": [{"id": next(iter(want_set)), "detail": "没有找到该条申报书，请关闭窗口后重新拆解再上传"}],
            }
    force = bool(body.get("force"))
    runnable, blocked = [], []
    for c in cases:
        if c.get("existing") and not force:
            blocked.append({"id": c.get("id"), "filename": (c.get("app") or {}).get("filename"), "detail": "该群文件已经上传过，可打开已有任务"})
            continue
        if not (c.get("app") or {}).get("copies") and not c.get("ready"):
            blocked.append({"id": c.get("id"), "filename": (c.get("app") or {}).get("filename"), "detail": "申报书未在各电脑缓存"})
            continue
        runnable.append(c)
    cases = runnable[:20]
    if not cases:
        return {
            "ok": False,
            "created": [],
            "skipped": [],
            "errors": blocked or [{"detail": "没有可上传的申报书"}],
        }

    async def grab(copies: list) -> dict:
        last = {"kind": "missing", "detail": "未缓存"}
        try:
            got = await W.fetch_attachment_any(copies, wait_s=50)
        except W.WecomError as e:
            return {"kind": "error", "detail": e.message}
        if got.get("kind") == "file" and got.get("content"):
            return got
        last = got
        if last.get("kind") == "pending":
            last = dict(last)
            last["detail"] = last.get("detail") or "远端助手还没回传文件"
        return last

    created, skipped, errors = [], [], []
    for c in cases:
        app = c.get("app") or {}
        got = await grab(app.get("copies") or [])
        if got.get("kind") != "file":
            errors.append({"id": c.get("id"), "filename": app.get("filename"), "detail": got.get("detail") or "申报书未缓存"})
            continue
        aname = sanitize(str(got.get("filename") or app.get("filename") or "申报书.pdf"))
        raw = got.get("content") or b""
        if len(raw) < 64:
            errors.append({"id": c.get("id"), "filename": aname, "detail": "申报书文件过小"})
            continue
        opinions = []
        used = {aname}
        for op in c.get("opinions") or []:
            oname = sanitize(str(op.get("filename") or "意见.txt"))
            base, n = oname, 2
            while oname in used:
                stem, ext = (base.rsplit(".", 1) + [""])[:2] if "." in base else (base, "")
                oname = stem + "-" + str(n) + (("." + ext) if ext else "")
                n += 1
            used.add(oname)
            if op.get("kind") == "text":
                opinions.append({"name": oname, "dataB64": base64.b64encode(opinion_txt_bytes(op)).decode()})
                continue
            og = await grab(op.get("copies") or [])
            if og.get("kind") != "file":
                skipped.append({"id": c.get("id"), "filename": oname, "detail": og.get("detail") or "意见文档未缓存"})
                continue
            opinions.append({
                "name": sanitize(str(og.get("filename") or oname)),
                "dataB64": base64.b64encode(og.get("content") or b"").decode(),
            })
        try:
            t = runner.create({
                "engine": "api",
                "app": {"name": aname, "dataB64": base64.b64encode(raw).decode()},
                "opinions": opinions,
                "source": "wecom",
                "wecom": {
                    "caseId": c.get("id"),
                    "sessionId": sid,
                    "sessionName": session_name,
                    "appFile": aname,
                    "messageId": app.get("message_id"),
                },
            }, owner=str(u.get("username") or ""))
        except ValueError as e:
            errors.append({"id": c.get("id"), "filename": aname, "detail": str(e)})
            continue
        runner.enqueue(t["id"])
        created.append({"id": t["id"], "url": "/t/" + t["id"], "filename": aname, "caseId": c.get("id")})
    return {
        "ok": bool(created),
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "hint": "已上传的任务会生成修改计划，确认后才会写入文件。" if created else "",
    }


@router.get("/api/wecom/media")
async def api_media(request: Request, url: str = Query("")):
    _admin(request)
    try:
        got = await W.proxy_media(url)
    except W.WecomError as e:
        raise _http(e)
    return Response(
        content=got.get("content") or b"",
        media_type=str(got.get("content_type") or "image/jpeg"),
        headers={"Cache-Control": "private, max-age=3600"},
    )
