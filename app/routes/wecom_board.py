"""各群记录看板：只读企业微信解析服务。登录用户可用（主界面下滑可见）。"""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, JSONResponse, StreamingResponse
from urllib.parse import quote
from .. import wecom_client as W

router = APIRouter()


def _user(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
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
    _user(request)
    return await W.health()


@router.get("/api/wecom/watch")
async def api_watch_status(request: Request):
    _user(request)
    from ..wecom_watch import public_status
    return public_status()


@router.post("/api/wecom/watch-run")
async def api_watch_run(request: Request):
    _user(request)
    from ..wecom_watch import scan_once
    return await scan_once(force=True)


@router.post("/api/wecom/watch-probe")
async def api_watch_probe(request: Request):
    u = _user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "仅管理员可检测值班模型")
    from ..wecom_watch_llm import probe_watch
    return await probe_watch()


@router.get("/api/wecom/sources")
async def api_sources(request: Request):
    _user(request)
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
    _user(request)
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
    _user(request)
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
    _user(request)
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
    _user(request)
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
    _user(request)
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
    _user(request)
    try:
        return await W.search(q, source_id=source_id, session_id=session_id, limit=limit)
    except W.WecomError as e:
        raise _http(e)


@router.get("/api/wecom/sources/{source_id}/files/{message_id}")
async def api_file(source_id: str, message_id: int, request: Request, session_id: str = Query("")):
    _user(request)
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
    _user(request)
    from ..main import runner
    from ..wecom_locate import locate_tasks
    return locate_tasks(runner, body if isinstance(body, dict) else {})


@router.post("/api/wecom/split-preview")
async def api_split_preview(body: dict, request: Request):
    _user(request)
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
    _user(request)
    from datetime import datetime, timedelta
    from ..main import runner
    from ..wecom_cases import cluster_messages, annotate_existing
    from ..wecom_submit import submit_cases
    u = _user(request)
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
    return await submit_cases(
        runner,
        cases,
        session_id=sid,
        session_name=session_name,
        owner=str(u.get("username") or ""),
        force=force,
        source_tag="wecom",
    )


def _norm_copies(raw) -> list:
    out = []
    if not isinstance(raw, list):
        return out
    for c in raw:
        if not isinstance(c, dict):
            continue
        sid = str(c.get("source_id") or "").strip()
        try:
            mid = int(c.get("message_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if sid and mid:
            out.append({
                "source_id": sid,
                "message_id": mid,
                "session_id": str(c.get("session_id") or ""),
                "source_label": str(c.get("source_label") or c.get("label") or ""),
            })
    return out


@router.post("/api/wecom/manual-create")
async def api_manual_create(body: dict, request: Request):
    """浏览记录时人手勾选申报书 + 修改意见（聊天文本或文件）后上传。"""
    import base64
    from ..main import runner
    from ..wecom_cases import _case_id, annotate_existing, opinion_txt_bytes, strip_cache_prefix
    from ..pdf_app import ALLOWED_APP_EXT, APP_EXT_HINT, ext_of
    from ..opinion_extract import ALLOWED_OPINION_EXT
    from ..runner import sanitize
    u = _user(request)
    body = body if isinstance(body, dict) else {}
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "缺少 session_id")
    session_name = str(body.get("session_name") or "").strip()
    app_in = body.get("app") if isinstance(body.get("app"), dict) else {}
    copies = _norm_copies(app_in.get("copies"))
    if not copies:
        raise HTTPException(400, "请选择一份申报书文件")
    fake = {
        "message_id": app_in.get("message_id") or (copies[0].get("message_id") if copies else 0),
        "attachment_name": app_in.get("filename") or "",
        "time_text": app_in.get("time") or "",
    }
    case = {
        "id": _case_id(sid, fake),
        "sessionId": sid,
        "sessionName": session_name,
        "app": {
            "filename": str(app_in.get("filename") or "申报书.pdf"),
            "message_id": fake["message_id"],
            "copies": copies,
            "time": str(app_in.get("time") or ""),
            "sender": str(app_in.get("sender") or ""),
        },
        "opinions": [],
        "ready": True,
    }
    annotate_existing([case], runner)
    force = bool(body.get("force"))
    if case.get("existing") and not force:
        ex = case["existing"]
        return {
            "ok": False,
            "created": [],
            "skipped": [],
            "errors": [{
                "id": case.get("id"),
                "filename": case["app"]["filename"],
                "detail": "该群文件已经上传过",
                "existing": ex,
            }],
            "needForce": True,
        }

    async def grab(cps: list) -> dict:
        try:
            got = await W.fetch_attachment_any(cps, wait_s=50)
        except W.WecomError as e:
            return {"kind": "error", "detail": e.message}
        if got.get("kind") == "file" and got.get("content"):
            return got
        if got.get("kind") == "pending":
            return {"kind": "pending", "detail": got.get("detail") or "远端助手还没回传文件"}
        return {"kind": got.get("kind") or "missing", "detail": got.get("detail") or "未缓存"}

    got = await grab(copies)
    if got.get("kind") != "file":
        raise HTTPException(404, got.get("detail") or "申报书未在各电脑缓存")
    raw = got.get("content") or b""
    if len(raw) < 64:
        raise HTTPException(400, "申报书文件过小")
    chat_name = str(app_in.get("filename") or "")
    cache_name = strip_cache_prefix(str(got.get("filename") or ""), app_in.get("message_id"))
    aname = sanitize(chat_name or cache_name or "申报书.pdf")
    if ext_of(aname) not in ALLOWED_APP_EXT:
        if ext_of(cache_name) in ALLOWED_APP_EXT:
            aname = sanitize(cache_name)
        else:
            raise HTTPException(400, "申报书必须为 " + APP_EXT_HINT)

    opinions = []
    used = {aname}
    skipped = []
    raw_ops = body.get("opinions") if isinstance(body.get("opinions"), list) else []
    for i, op in enumerate(raw_ops[:20]):
        if not isinstance(op, dict):
            continue
        kind = str(op.get("kind") or "file").strip().lower()
        if kind == "text":
            text = str(op.get("text") or "").strip()
            if len(text) < 2:
                continue
            oname = sanitize(str(op.get("filename") or "群聊修改意见.txt"))
            if ext_of(oname) not in ALLOWED_OPINION_EXT:
                oname = "群聊修改意见.txt"
            base, n = oname, 2
            while oname in used:
                stem, ext = (base.rsplit(".", 1) + [""])[:2] if "." in base else (base, "")
                oname = stem + "-" + str(n) + (("." + ext) if ext else "")
                n += 1
            used.add(oname)
            opinions.append({"name": oname, "dataB64": base64.b64encode(opinion_txt_bytes({
                "text": text,
                "sender": op.get("sender") or "",
                "time": op.get("time") or "",
            })).decode()})
            continue
        ocps = _norm_copies(op.get("copies"))
        oname = sanitize(str(op.get("filename") or ("意见-" + str(i + 1))))
        if not ocps:
            skipped.append({"filename": oname, "detail": "没有可查询的电脑副本"})
            continue
        og = await grab(ocps)
        if og.get("kind") != "file":
            skipped.append({"filename": oname, "detail": og.get("detail") or "意见文档未缓存"})
            continue
        stored = sanitize(strip_cache_prefix(str(og.get("filename") or oname), op.get("message_id")) or oname)
        if ext_of(stored) not in ALLOWED_OPINION_EXT:
            if ext_of(oname) in ALLOWED_OPINION_EXT:
                stored = oname
            else:
                skipped.append({"filename": stored or oname, "detail": "意见类型不支持（Word / Excel / 图片 / 录音 / txt / md）"})
                continue
        base, n = stored, 2
        while stored in used:
            stem, ext = (base.rsplit(".", 1) + [""])[:2] if "." in base else (base, "")
            stored = stem + "-" + str(n) + (("." + ext) if ext else "")
            n += 1
        used.add(stored)
        opinions.append({
            "name": stored,
            "dataB64": base64.b64encode(og.get("content") or b"").decode(),
        })

    try:
        t = runner.create({
            "engine": "api",
            "app": {"name": aname, "dataB64": base64.b64encode(raw).decode()},
            "opinions": opinions,
            "source": "wecom",
            "wecom": {
                "caseId": case.get("id"),
                "sessionId": sid,
                "sessionName": session_name,
                "appFile": aname,
                "messageId": fake["message_id"],
                "manual": True,
            },
        }, owner=str(u.get("username") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    runner.enqueue(t["id"])
    return {
        "ok": True,
        "created": [{"id": t["id"], "url": "/t/" + t["id"], "filename": aname, "caseId": case.get("id")}],
        "skipped": skipped,
        "errors": [],
        "hint": "已上传的任务会生成修改计划，确认后才会写入文件。",
    }


@router.get("/api/wecom/media")
async def api_media(request: Request, url: str = Query("")):
    _user(request)
    try:
        got = await W.proxy_media(url)
    except W.WecomError as e:
        raise _http(e)
    return Response(
        content=got.get("content") or b"",
        media_type=str(got.get("content_type") or "image/jpeg"),
        headers={"Cache-Control": "private, max-age=3600"},
    )
