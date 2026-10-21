"""各群记录看板：只读企业微信解析服务。登录用户可用（主界面下滑可见）。"""
import asyncio
import json
import time
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, JSONResponse, StreamingResponse
from urllib.parse import quote
from .. import wecom_client as W
from ..party import sender_of

router = APIRouter()


def _user(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return u


def _admin(request: Request) -> dict:
    u = _user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "仅管理员可操作")
    return u


def _http(e: W.WecomError) -> HTTPException:
    status = e.status if e.status in (400, 401, 403, 404, 422) else 502
    if e.code == "NETWORK":
        status = 503
    if e.code == "BAD_REQUEST":
        status = 400
    return HTTPException(status, e.message)


def _file_upload_public() -> dict:
    from ..pool import talent_upload_target
    t = talent_upload_target()
    return {
        "hasKey": bool(t.get("key")),
        "configured": bool(t.get("ok")),
    }


async def _emit_progress(progress, phase: str, percent: int, label: str, **extra) -> None:
    if not progress:
        return
    ev = {
        "phase": phase,
        "percent": max(0, min(100, int(percent))),
        "label": str(label or ""),
    }
    ev.update(extra)
    out = progress(ev)
    if asyncio.iscoroutine(out):
        await out


def _sse_job(work):
    """work(progress) -> coroutine returning a JSON-serializable dict."""
    queue: asyncio.Queue = asyncio.Queue()

    async def progress(ev: dict) -> None:
        await queue.put(dict(ev or {}))

    async def run() -> None:
        try:
            result = await work(progress)
            await queue.put({"phase": "done", "percent": 100, "label": "完成", "result": result})
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            await queue.put({"phase": "error", "message": detail or "请求失败"})
        except W.WecomError as e:
            await queue.put({"phase": "error", "message": e.message})
        except ValueError as e:
            await queue.put({"phase": "error", "message": str(e)})
        except Exception:
            await queue.put({"phase": "error", "message": "解析失败，请稍后重试"})

    async def gen():
        task = asyncio.create_task(run())
        try:
            while True:
                ev = await queue.get()
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                if ev.get("phase") in ("done", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _apply_file_peers(groups: list, session_id: str) -> list:
    try:
        peers = await W.session_peers(session_id)
    except Exception:
        peers = []
    if not peers:
        return groups
    sid = str(session_id or "").strip()
    for g in groups or []:
        for f in (g.get("files") or []) if isinstance(g, dict) else []:
            if not isinstance(f, dict):
                continue
            copies = list(f.get("copies") or [])
            if not copies and f.get("messageId"):
                copies = [{"message_id": f.get("messageId"), "session_id": sid}]
            f["copies"] = W.merge_copy_peers(copies, peers)
    return groups


def _file_summary_payload(
    *,
    sid: str,
    sess_name: str,
    message_count: int,
    groups: list,
    classify_note: str,
    used_gemini: bool,
    counts: dict,
    mode: str,
    new_file_count: int = 0,
    from_cache: bool = False,
) -> dict:
    from ..wecom_file_class import refresh_group_image_flags, stamp_cached_file_rows
    incremental = mode == "incremental"
    groups = stamp_cached_file_rows(refresh_group_image_flags(groups or []))
    return {
        "ok": True,
        "session_id": sid,
        "session_name": sess_name,
        "messageCount": int(message_count or 0),
        "fileCount": sum(len(g.get("files") or []) for g in groups if isinstance(g, dict)),
        "groups": groups,
        "upload": _file_upload_public(),
        "classifyNote": classify_note or "",
        "usedGemini": bool(used_gemini),
        "counts": counts or {},
        "fromCache": bool(from_cache),
        "incremental": incremental,
        "mode": mode,
        "newFileCount": int(new_file_count or 0),
    }


async def _file_summary_work(body: dict, u: dict, progress=None) -> dict:
    """当前会话文件按聊天上下文分类汇总。第一次全量扫描并落盘，之后只扫新文件。"""
    from ..wecom_file_class import (
        after_cursor,
        build_file_groups,
        classify_session_files,
        counts_from_groups,
        incremental_work_window,
        load_summary_state,
        merge_file_groups,
        save_summary_state,
        session_data_stamp,
    )
    sid = str(body.get("session_id") or "").strip()
    start_date = str(body.get("start_date") or "")
    end_date = str(body.get("end_date") or "")
    force = bool(body.get("force") or body.get("refresh"))
    stamp = session_data_stamp(sid, start_date=start_date, end_date=end_date)
    auto_raw = body.get("autoUpload")
    do_auto = bool(auto_raw) and u.get("role") == "admin"
    await _emit_progress(progress, "load", 4, "准备读取会话")
    state = None if force else load_summary_state(sid, start_date, end_date)
    if (
        state
        and stamp
        and str(state.get("stamp") or "") == stamp
        and state.get("full_done")
        and isinstance(state.get("result"), dict)
    ):
        await _emit_progress(progress, "cache", 70, "沿用已扫描结果")
        cached = _file_summary_payload(
            sid=sid,
            sess_name=str((state.get("result") or {}).get("session_name") or body.get("session_name") or ""),
            message_count=int((state.get("result") or {}).get("messageCount") or 0),
            groups=list((state.get("result") or {}).get("groups") or []),
            classify_note=str((state.get("result") or {}).get("classifyNote") or "沿用已扫描结果"),
            used_gemini=bool((state.get("result") or {}).get("usedGemini")),
            counts=dict((state.get("result") or {}).get("counts") or {}),
            mode="cache",
            from_cache=True,
        )
        cached["upload"] = _file_upload_public()
        cached["groups"] = await _apply_file_peers(cached.get("groups") or [], sid)
        await _emit_progress(progress, "group", 92, "整理文件列表")
        return await _with_auto_upload(cached, session_id=sid, do_auto=do_auto)

    last_time = str((state or {}).get("last_time") or "")
    last_id = str((state or {}).get("last_message_id") or "")
    incremental = bool(
        not force
        and state
        and state.get("full_done")
        and (last_time or last_id)
        and isinstance(state.get("result"), dict)
    )
    fetch_start = start_date
    if incremental and len(last_time) >= 10 and last_time[4:5] == "-":
        day = last_time[:10]
        if not start_date or day >= str(start_date)[:10]:
            fetch_start = day
    await _emit_progress(
        progress, "load", 10,
        "正在读取聊天记录" + ("（增量）" if incremental else "（全量）"),
    )
    try:
        data = await W.list_merged_messages(
            sid,
            source_id=str(body.get("source_id") or ""),
            start_date=fetch_start,
            end_date=end_date,
            full=True,
        )
    except W.WecomError as e:
        raise _http(e)
    messages = [m for m in (data.get("items") or []) if isinstance(m, dict)]
    sess_name = str(data.get("display_name") or body.get("session_name") or "")
    await _emit_progress(
        progress, "load", 22,
        "已读取 " + str(len(messages)) + " 条消息",
        messages=len(messages),
    )

    if incremental:
        old_result = dict(state.get("result") or {})
        old_groups = list(old_result.get("groups") or [])
        first_new, work = incremental_work_window(messages, last_time, last_id)
        if first_new >= len(messages):
            await _emit_progress(progress, "scan", 80, "增量扫描，没有新文件")
            note = "增量扫描，没有新文件"
            result = _file_summary_payload(
                sid=sid,
                sess_name=sess_name or str(old_result.get("session_name") or ""),
                message_count=int(old_result.get("messageCount") or 0),
                groups=old_groups,
                classify_note=note,
                used_gemini=bool(old_result.get("usedGemini")),
                counts=dict(old_result.get("counts") or {}),
                mode="incremental",
                from_cache=True,
            )
            result["groups"] = await _apply_file_peers(result["groups"], sid)
            if stamp:
                save_summary_state(
                    sid,
                    start_date=start_date,
                    end_date=end_date,
                    stamp=stamp,
                    result=result,
                    last_time=last_time,
                    last_message_id=last_id,
                    full_done=True,
                )
            return await _with_auto_upload(result, session_id=sid, do_auto=do_auto)

        await _emit_progress(progress, "scan", 28, "发现新消息，正在分类文件", messages=len(work))
        typed = await classify_session_files(
            work, session_id=sid, use_gemini=True, keep_entries=True, progress=progress,
        )
        new_entries = []
        for it in typed.get("entries") or typed.get("items") or []:
            if not isinstance(it, dict):
                continue
            idx = it.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(work):
                continue
            if after_cursor(work[idx], last_time, last_id):
                new_entries.append(it)
        extra_groups, _extra_n = build_file_groups(
            new_entries, work, session_id=sid, session_name=sess_name,
        )
        groups = merge_file_groups(old_groups, extra_groups)
        await _emit_progress(progress, "group", 82, "正在整理分类结果")
        groups = await _apply_file_peers(groups, sid)
        old_n = sum(len(g.get("files") or []) for g in old_groups if isinstance(g, dict))
        new_n = sum(len(g.get("files") or []) for g in groups if isinstance(g, dict))
        added = max(0, new_n - old_n)
        gemini_note = str(typed.get("geminiNote") or "").strip()
        note = "增量扫描，新增 " + str(added) + " 个文件"
        if gemini_note:
            note = note + "。" + gemini_note
        result = _file_summary_payload(
            sid=sid,
            sess_name=sess_name or str(old_result.get("session_name") or ""),
            message_count=int(old_result.get("messageCount") or 0) + (len(messages) - first_new),
            groups=groups,
            classify_note=note,
            used_gemini=bool(typed.get("usedGemini")),
            counts=counts_from_groups(groups),
            mode="incremental",
            new_file_count=added,
        )
        if stamp:
            save_summary_state(
                sid,
                start_date=start_date,
                end_date=end_date,
                stamp=stamp,
                result=result,
                messages=messages,
                last_time=last_time,
                last_message_id=last_id,
                full_done=True,
            )
        await _emit_progress(progress, "group", 92, "整理完成", files=new_n)
        return await _with_auto_upload(result, session_id=sid, do_auto=do_auto)

    await _emit_progress(progress, "scan", 26, "正在扫描并分类文件")
    typed = await classify_session_files(
        messages, session_id=sid, use_gemini=True, keep_entries=True, progress=progress,
    )
    await _emit_progress(
        progress, "group", 78, "分类完成，正在整理",
        files=int(typed.get("fileCount") or 0),
    )
    groups, _file_n = build_file_groups(
        typed.get("entries") or typed.get("items") or [],
        messages,
        session_id=sid,
        session_name=sess_name,
    )
    groups = await _apply_file_peers(groups, sid)
    gemini_note = str(typed.get("geminiNote") or "").strip()
    note = "全量扫描完成"
    if gemini_note:
        note = note + "。" + gemini_note
    result = _file_summary_payload(
        sid=sid,
        sess_name=sess_name,
        message_count=data.get("total") or 0,
        groups=groups,
        classify_note=note,
        used_gemini=bool(typed.get("usedGemini")),
        counts=typed.get("counts") or counts_from_groups(groups),
        mode="full",
    )
    if stamp:
        save_summary_state(
            sid,
            start_date=start_date,
            end_date=end_date,
            stamp=stamp,
            result=result,
            messages=messages,
            full_done=True,
        )
    await _emit_progress(progress, "group", 92, "正在收尾", files=_file_n)
    result = await _with_auto_upload(result, session_id=sid, do_auto=do_auto)
    return result


@router.post("/api/wecom/file-summary")
async def api_file_summary(body: dict, request: Request):
    """当前会话文件按聊天上下文分类汇总。第一次全量扫描并落盘，之后只扫新文件。"""
    u = _user(request)
    body = body if isinstance(body, dict) else {}
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "请先选择会话")
    if not body.get("stream"):
        return await _file_summary_work(body, u)
    return _sse_job(lambda progress: _file_summary_work(body, u, progress=progress))


async def _with_auto_upload(result: dict, *, session_id: str, do_auto: bool) -> dict:
    from ..wecom_talent_upload import apply_status_to_groups, auto_upload_files, flatten_groups
    groups = result.get("groups") or []
    files = flatten_groups(groups, session_id=session_id)
    if do_auto:
        files, summary = await auto_upload_files(files, session_id=session_id, source="file_summary")
    else:
        from ..wecom_talent_upload import annotate_files, upload_configured
        files = annotate_files(files)
        summary = {
            "ok": True,
            "configured": upload_configured(),
            "uploaded": 0,
            "failed": 0,
            "pending": 0,
            "noId": sum(1 for f in files if f.get("uploadStatus") == "no_id"),
            "note": "未自动入库",
        }
    apply_status_to_groups(groups, files)
    result["groups"] = groups
    result["autoUpload"] = summary
    result["upload"] = _file_upload_public()
    return result


@router.post("/api/wecom/talent-files")
async def api_upload_talent_files(body: dict, request: Request):
    """把已分类文件上传到人才库人才附件。"""
    _user(request)
    info = _file_upload_public()
    if not info["configured"]:
        raise HTTPException(503, "未配置人才库或人才附件上传接口")
    body = body if isinstance(body, dict) else {}
    aid = str(body.get("attachId") or body.get("attach_id") or "").strip()
    files = body.get("files") if isinstance(body.get("files"), list) else []
    if not files:
        raise HTTPException(400, "请选择要上传的文件")
    from ..wecom_talent_upload import upload_one
    out, errors = [], []
    for raw in files:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if aid:
            row["attachId"] = aid
        if not row.get("type") and row.get("kind"):
            row["type"] = row.get("kind")
        if not row.get("type") and row.get("label"):
            from ..wecom_file_class import LABEL_TO_ID
            row["type"] = LABEL_TO_ID.get(str(row.get("label") or ""), row.get("label"))
        got = await upload_one(row, force=True, source="file_summary")
        if got.get("uploadStatus") == "uploaded":
            out.append(got)
        else:
            errors.append({
                "filename": got.get("filename") or row.get("filename") or "",
                "detail": got.get("uploadNote") or "上传失败",
            })
    if not out and errors:
        raise HTTPException(400, errors[0].get("detail") or "上传失败")
    return {
        "ok": True,
        "uploaded": len(out),
        "failed": len(errors),
        "items": out,
        "errors": errors,
        "detail": "已上传 " + str(len(out)) + " 个" + (("，失败 " + str(len(errors)) + " 个") if errors else ""),
    }


@router.post("/api/wecom/intent")
async def api_intent(body: dict, request: Request):
    """单条聊天记录的意图判断。气泡固定先标明已读。"""
    _user(request)
    from ..wecom_intent import judge_message
    body = body if isinstance(body, dict) else {}
    message = body.get("message") if isinstance(body.get("message"), dict) else {}
    context = body.get("context") if isinstance(body.get("context"), list) else []
    if not (str(message.get("text") or message.get("snippet") or "").strip() or str(message.get("filename") or message.get("attachment_name") or "").strip()):
        return {
            "ok": True,
            "read": True,
            "readLabel": "已读",
            "intent": "other",
            "intentLabel": "其他",
            "need": "",
            "action": "这条没有可分析的正文",
        }
    got = await judge_message(message, context)
    got["ok"] = True
    return got


@router.post("/api/wecom/intent-page")
async def api_intent_page(body: dict, request: Request):
    """分析当前页聊天记录。结果与消息顺序一一对应。"""
    _user(request)
    from ..wecom_intent import judge_page
    body = body if isinstance(body, dict) else {}
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    if not messages:
        raise HTTPException(400, "本页没有可分析的聊天记录")
    items = await judge_page(messages)
    sid = str(body.get("session_id") or "").strip()
    keys = body.get("keys") if isinstance(body.get("keys"), list) else []
    if sid and keys:
        from ..wecom_intent_store import save_many
        save_many(sid, list(zip(keys, items)))
    return {"ok": True, "items": items}


@router.post("/api/wecom/intents/lookup")
def api_intent_lookup(body: dict, request: Request):
    """取已保存的意图，供刷新后挂回本页消息。"""
    _user(request)
    from ..wecom_intent_store import load_many
    body = body if isinstance(body, dict) else {}
    sid = str(body.get("session_id") or "").strip()
    keys = body.get("keys") if isinstance(body.get("keys"), list) else []
    return {"ok": True, "items": load_many(sid, keys[:80])}


@router.get("/api/wecom/health")
async def api_health(request: Request):
    _user(request)
    return await W.health()


@router.get("/api/wecom/watch")
async def api_watch_status(request: Request):
    _user(request)
    from ..wecom_watch import public_status
    return public_status()


@router.post("/api/wecom/watch-pending")
async def api_watch_pending(body: dict, request: Request):
    u = _user(request)
    from ..wecom_watch import dismiss_pending, submit_pending
    body = body if isinstance(body, dict) else {}
    action = str(body.get("action") or "").strip().lower()
    case_id = str(body.get("caseId") or "").strip()
    if action == "skip":
        got = dismiss_pending(case_id)
        if not got.get("ok"):
            raise HTTPException(400, got.get("detail") or "无法移出待审")
        return got
    if action == "upload":
        got = await submit_pending(case_id, owner=str(u.get("username") or ""))
        if not got.get("ok"):
            raise HTTPException(400, got.get("detail") or "上传失败")
        return got
    raise HTTPException(400, "action 须为 upload 或 skip")


_user_scan_until = 0.0
_USER_SCAN_COOLDOWN = 60.0


@router.post("/api/wecom/watch-run")
async def api_watch_run(request: Request):
    global _user_scan_until
    u = _user(request)
    if u.get("role") != "admin":
        now = time.time()
        left = _user_scan_until - now
        if left > 0:
            sec = max(1, int(left + 0.999))
            return JSONResponse(
                {"ok": False, "detail": "请 " + str(sec) + " 秒后再扫描", "retryAfter": sec},
                status_code=429,
            )
        _user_scan_until = now + _USER_SCAN_COOLDOWN
    from ..wecom_watch import scan_once
    return await scan_once(force=True)


@router.post("/api/wecom/watch-probe")
async def api_watch_probe(request: Request):
    _admin(request)
    from ..wecom_watch_llm import probe_watch
    return await probe_watch()


@router.get("/api/wecom/watch-config")
async def api_watch_config_get(request: Request):
    _admin(request)
    from ..config import editor_config
    from ..wecom_watch import public_status
    ed = (editor_config().get("edit") or {}).get("wecomChat") or {}
    return {"ok": True, "config": ed.get("watch") or {}, "status": public_status()}


@router.post("/api/wecom/watch-config")
async def api_watch_config_save(body: dict, request: Request):
    _admin(request)
    from ..config import save_config
    body = body if isinstance(body, dict) else {}
    watch = body.get("watch") if isinstance(body.get("watch"), dict) else body
    try:
        got = save_config({"wecomChat": {"watch": watch}})
    except ValueError as e:
        raise HTTPException(400, str(e))
    ed = (got.get("edit") or {}).get("wecomChat") or {}
    from ..wecom_watch import public_status
    return {"ok": True, "config": ed.get("watch") or {}, "status": public_status()}


@router.get("/api/wecom/watch-logs")
async def api_watch_logs(request: Request, limit: int = Query(40)):
    _user(request)
    from ..wecom_watch import apply_review_to_log_list
    from ..wecom_watch_store import list_scan_runs
    return apply_review_to_log_list(list_scan_runs(limit=limit))


@router.get("/api/wecom/watch-logs/{record_id}")
async def api_watch_log_one(record_id: str, request: Request):
    _user(request)
    from ..wecom_watch import apply_review_to_log
    from ..wecom_watch_store import get_scan_run
    row = get_scan_run(record_id)
    if not row:
        raise HTTPException(404, "扫描记录不存在")
    return apply_review_to_log(row)


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
    watch_only: bool = Query(False),
):
    _user(request)
    k = str(kind or "group").strip().lower()
    if k not in ("group", "all"):
        k = "group"
    try:
        data = await W.list_merged_groups(
            kind=k, q=q, source_id=source_id, limit=limit, watch_only=watch_only,
        )
        from ..wecom_groups import list_watch_groups
        data["watchGroups"] = list_watch_groups()
        return data
    except W.WecomError as e:
        raise _http(e)


@router.get("/api/wecom/watch-groups")
async def api_watch_groups(request: Request):
    _user(request)
    from ..wecom_groups import list_watch_groups
    groups = list_watch_groups()
    return {
        "ok": True,
        "groups": groups,
        "count": len(groups),
        "canEdit": True,
    }


@router.post("/api/wecom/watch-groups/toggle")
async def api_watch_groups_toggle(body: dict, request: Request):
    _user(request)
    from ..wecom_groups import toggle_watch_group
    body = body if isinstance(body, dict) else {}
    name = str(body.get("display_name") or body.get("session_name") or "").strip()
    sid = str(body.get("session_id") or body.get("username") or "").strip()
    if "watch" not in body:
        raise HTTPException(400, "缺少 watch 参数")
    try:
        got = toggle_watch_group(name, sid, watch=bool(body.get("watch")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "ok": True,
        "watch": bool(body.get("watch")),
        "display_name": name,
        "session_id": sid,
        "groups": got.get("groups") or [],
        "count": got.get("count") or 0,
    }


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
    from_end: int = Query(0),
    around_id: str = Query(""),
    around_time: str = Query(""),
    full: bool = Query(False),
):
    _user(request)
    try:
        return await W.list_merged_messages(
            session_id, source_id=source_id,
            start_date=start_date, end_date=end_date,
            offset=offset, limit=limit, tail=tail, from_end=from_end,
            around_id=around_id, around_time=around_time,
            full=full,
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


def _lookup_message_filename(source_id: str, session_id: str, message_id: int) -> str:
    from .. import wecom_local as L
    if not L.available() or not session_id or int(message_id or 0) <= 0:
        return ""
    want = int(message_id)
    sids = [str(source_id or "").strip()]
    try:
        extra = [str(s.get("id") or "") for s in (L.list_sources() or [])]
    except Exception:
        extra = []
    for sid in sids + extra:
        if not sid:
            continue
        try:
            rows, _older = L.read_window(sid, session_id, need=0)
        except Exception:
            continue
        for m in rows or []:
            if not isinstance(m, dict):
                continue
            try:
                if int(m.get("message_id") or 0) != want:
                    continue
            except (TypeError, ValueError):
                continue
            return str(m.get("attachment_name") or m.get("filename") or m.get("cache_name") or "")
    return ""


def _parse_message_ids(*vals) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for val in vals:
        if val is None:
            continue
        if isinstance(val, (list, tuple)):
            parts = val
        else:
            parts = str(val).replace(";", ",").split(",")
        for part in parts:
            try:
                n = int(str(part).strip() or 0)
            except (TypeError, ValueError):
                n = 0
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _local_shot_response(name: str, source_id: str = "", message_id: int = 0, extra_ids=None):
    from ..wecom_file_class import (
        file_basename,
        image_media_type,
        resolve_wecom_cached_file,
        resolve_wecom_temp_image,
    )
    path = resolve_wecom_temp_image(name)
    mids = _parse_message_ids(message_id, extra_ids)
    if not mids:
        mids = [0]
    if path is None:
        for mid in mids:
            path = resolve_wecom_cached_file(source_id, mid, name)
            if path is not None:
                break
    if path is None and name:
        path = resolve_wecom_cached_file("", 0, name)
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    disp = file_basename(path.name) or path.name
    if "_" in disp and disp.split("_", 1)[0].isdigit():
        rest = disp.split("_", 1)[1]
        if rest:
            disp = rest
    img_type = image_media_type(disp, "", raw) or "image/png"
    return Response(
        content=raw,
        media_type=img_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": "inline; filename*=UTF-8''" + quote(disp),
        },
    )


@router.get("/api/wecom/sources/{source_id}/files/{message_id}")
async def api_file(
    source_id: str,
    message_id: int,
    request: Request,
    session_id: str = Query(""),
    inline: bool = Query(False),
    name: str = Query(""),
):
    _user(request)
    from ..wecom_file_class import file_basename, image_media_type
    local_name = str(name or "").strip()
    extra_ids = request.query_params.get("message_ids") or ""
    if inline:
        hit = _local_shot_response(local_name, source_id, message_id, extra_ids)
        if hit is not None:
            return hit
    try:
        got = await W.stream_attachment(source_id, message_id, session_id)
    except W.WecomError as e:
        if inline:
            hint = local_name or _lookup_message_filename(source_id, session_id, message_id)
            hit = _local_shot_response(hint, source_id, message_id, extra_ids)
            if hit is not None:
                return hit
        raise _http(e)
    if got.get("kind") in ("pending", "missing"):
        hint = local_name or _lookup_message_filename(source_id, session_id, message_id)
        hit = _local_shot_response(hint, source_id, message_id, extra_ids)
        if hit is not None:
            return hit
        if got.get("kind") == "pending":
            accept = (request.headers.get("accept") or "").lower()
            if inline and "json" not in accept:
                return Response(status_code=204)
            return JSONResponse(
                {"ok": True, "kind": "pending", "job_id": got.get("job_id") or "", "detail": got.get("detail") or "", "filename": got.get("filename") or ""},
                status_code=202,
            )
        raise HTTPException(404, got.get("detail") or "文件不在缓存")
    fname = str(got.get("filename") or ("wecom-" + str(message_id)))
    disp = file_basename(fname) or fname
    raw_type = str(got.get("content_type") or "application/octet-stream")
    body = b"" if got.get("kind") == "stream" else (got.get("content") or b"")
    img_type = image_media_type(disp, raw_type, body if body else None)
    if inline and not img_type and "." not in disp:
        img_type = "image/jpeg"
    if inline and img_type:
        headers = {
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": "inline; filename*=UTF-8''" + quote(disp),
        }
        media_type = img_type
    else:
        headers = {
            "Cache-Control": "no-cache",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(disp),
        }
        media_type = raw_type
    if got.get("kind") == "stream":
        return StreamingResponse(
            got["chunks"](),
            media_type=media_type,
            headers=headers,
        )
    return Response(
        content=body,
        media_type=media_type,
        headers=headers,
    )


@router.get("/api/wecom/local-image")
async def api_local_image(
    request: Request,
    name: str = Query(""),
    source_id: str = Query(""),
    session_id: str = Query(""),
    message_id: int = Query(0),
    message_ids: str = Query(""),
):
    """文件汇总/会话预览：直接读本机 Temp 或解析同步目录里的截图，不经过远端回传。"""
    _user(request)
    extra = message_ids
    hit = _local_shot_response(name, source_id, message_id, extra)
    if hit is None and session_id:
        for mid in _parse_message_ids(message_id, extra) or [0]:
            hint = _lookup_message_filename(source_id, session_id, mid)
            if hint:
                hit = _local_shot_response(hint, source_id, mid, extra)
                if hit is not None:
                    break
    if hit is None:
        raise HTTPException(404, "本机没有这张图片")
    return hit


async def _ai_read_work(body: dict, u: dict, progress=None) -> dict:
    from ..main import runner
    from ..wecom_ai_read import ai_read_chat
    from ..wecom_ai_store import save_ai_read
    scope = str(body.get("scope") or "").strip().lower()
    sid = str(body.get("session_id") or "").strip()
    if not scope:
        scope = "session" if sid else "watch"
    if scope != "watch" and not sid:
        raise HTTPException(400, "缺少 session_id")
    await _emit_progress(progress, "load", 4, "开始解读")
    try:
        bind_raw = body.get("bind")
        bind = True if bind_raw is None else bool(bind_raw)
        bind_prompts = body.get("bindPrompts") if isinstance(body.get("bindPrompts"), list) else None
        got = await ai_read_chat(
            session_id=sid,
            session_name=str(body.get("session_name") or ""),
            source_id=str(body.get("source_id") or ""),
            start_date=str(body.get("start_date") or ""),
            end_date=str(body.get("end_date") or ""),
            window_hours=int(body.get("windowHours") or 48),
            runner=runner,
            scope=scope,
            bind=bind,
            bind_prompts=bind_prompts,
            user=u,
            progress=progress,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except W.WecomError as e:
        raise _http(e)
    await _emit_progress(progress, "save", 97, "正在保存解读记录")
    meta = save_ai_read(got, user=str(u.get("username") or u.get("realName") or ""))
    got["recordId"] = meta.get("id") or ""
    got["recordAt"] = meta.get("at") or ""
    return got


@router.post("/api/wecom/ai-read")
async def api_ai_read(body: dict, request: Request):
    u = _user(request)
    body = body if isinstance(body, dict) else {}
    if not body.get("stream"):
        return await _ai_read_work(body, u)
    return _sse_job(lambda progress: _ai_read_work(body, u, progress=progress))


@router.get("/api/wecom/ai-reads")
async def api_ai_reads(
    request: Request,
    session_id: str = Query(""),
    limit: int = Query(40),
):
    _user(request)
    from ..wecom_ai_store import list_ai_reads
    return list_ai_reads(session_id=session_id, limit=limit)


@router.get("/api/wecom/ai-read-latest")
def api_ai_read_latest(request: Request, session_id: str = Query("")):
    _user(request)
    from ..wecom_ai_store import latest_ai_read
    sid = str(session_id or "").strip()
    if not sid:
        raise HTTPException(400, "缺少 session_id")
    row = latest_ai_read(sid)
    if not row:
        raise HTTPException(404, "当前会话还没有解读记录")
    return row


@router.get("/api/wecom/ai-reads/{record_id}")
async def api_ai_read_one(record_id: str, request: Request):
    _user(request)
    from ..wecom_ai_store import get_ai_read
    row = get_ai_read(record_id)
    if not row:
        raise HTTPException(404, "解读记录不存在")
    return row


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
    from ..wecom_ai_read import scan_files_then_pair
    from ..wecom_submit import submit_cases
    u = _user(request)
    body = body if isinstance(body, dict) else {}
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "缺少 session_id")
    start = str(body.get("start_date") or "").strip()[:10]
    end = str(body.get("end_date") or "").strip()[:10]
    all_history = bool(body.get("allHistory")) or (
        not start and not end and int(body.get("windowHours") or 48) <= 0
    )
    if not all_history and not start and not end:
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
    paired = scan_files_then_pair(
        data.get("items") or [],
        session_id=sid,
        session_name=session_name,
        window_hours=body.get("windowHours") or 48,
        runner=runner,
    )
    cases = paired.get("cases") or []
    posted = dict(body.get("case")) if isinstance(body.get("case"), dict) else None
    want = body.get("caseIds")
    want_set = {str(x) for x in want} if isinstance(want, list) and want else set()
    want_mid = ""
    if posted:
        if not posted.get("id") and want_set:
            posted["id"] = next(iter(want_set))
        papp = posted.get("app") if isinstance(posted.get("app"), dict) else {}
        want_mid = str(
            posted.get("message_id") or posted.get("messageId") or papp.get("message_id") or ""
        )
    hit = None
    if want_set:
        for c in cases:
            if str(c.get("id") or "") in want_set:
                hit = c
                break
    if hit is None and posted:
        pid = str(posted.get("id") or "")
        if pid:
            for c in cases:
                if str(c.get("id") or "") == pid:
                    hit = c
                    break
    if hit is None and want_mid:
        for c in cases:
            app = c.get("app") if isinstance(c.get("app"), dict) else {}
            if str(app.get("message_id") or "") == want_mid:
                hit = c
                break
    if posted or want_set:
        if hit:
            chosen = dict(hit)
            if posted:
                papp = posted.get("app") if isinstance(posted.get("app"), dict) else {}
                happ = chosen.get("app") if isinstance(chosen.get("app"), dict) else {}
                if papp.get("copies") and not happ.get("copies"):
                    happ = dict(happ)
                    happ["copies"] = papp["copies"]
                    chosen["app"] = happ
                    chosen["ready"] = True
                if posted.get("opinions") and not chosen.get("opinions"):
                    chosen["opinions"] = posted["opinions"]
            cases = [chosen]
        elif posted and (posted.get("app") or {}).get("copies"):
            cases = [posted]
        else:
            return {
                "ok": False,
                "created": [],
                "skipped": [],
                "errors": [{"id": next(iter(want_set), ""), "detail": "没有找到该条申报书，请关闭窗口后重新拆解再上传"}],
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
            "sender": sender_of(app_in),
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
                "sender": sender_of(op),
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
