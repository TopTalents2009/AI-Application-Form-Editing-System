"""主界面 AI 审核：Jev 意图评分 ≥70 供组长审核。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..agent_extract import is_delivery_name
from ..agent_pipeline import analyze_session
from ..agent_review import (
    FIELDS,
    SCORE_MIN,
    delete_example,
    example_stats,
    get_hit,
    get_review,
    list_examples,
    list_hits,
    list_reviews,
    list_runs,
    load_examples,
    rebuild_examples_from_reviews,
    set_example,
    upsert_review,
)
from ..party import classify, load_party, save_party
from ..wecom_groups import group_is_watched, list_watch_groups
from ..wecom_intent import INTENTS, intent_label
from .. import wecom_client as W

router = APIRouter()


def _user(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return u


def _admin(request: Request) -> dict:
    u = _user(request)
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


@router.get("/api/agent-review/intents")
def api_intents(request: Request):
    _user(request)
    return {
        "ok": True,
        "scoreMin": SCORE_MIN,
        "data": [{"id": k, "name": v} for k, v in INTENTS.items()],
        "fields": FIELDS,
    }


@router.get("/api/agent-review/party")
def api_party(request: Request):
    _user(request)
    data = load_party()
    return {
        "ok": True,
        "data": data,
        "staffCount": len(data.get("staff") or []),
        "reachingCount": len(data.get("reachingAliases") or []),
    }


@router.put("/api/agent-review/party")
def api_party_save(payload: dict, request: Request):
    _user(request)
    if not isinstance(payload, dict):
        raise HTTPException(400, "名单格式不对")
    save_party(payload)
    data = load_party()
    return {
        "ok": True,
        "data": data,
        "staffCount": len(data.get("staff") or []),
        "reachingCount": len(data.get("reachingAliases") or []),
    }


@router.post("/api/agent-review/party/preview")
def api_party_preview(payload: dict, request: Request):
    _user(request)
    body = payload if isinstance(payload, dict) else {}
    sender = str(body.get("sender") or "").strip()
    platform = str(body.get("platform") or "wecom").strip() or "wecom"
    draft = body.get("party") if isinstance(body.get("party"), dict) else None
    return {"ok": True, "data": classify(sender, platform, draft)}


@router.get("/api/agent-review/fields")
def api_fields(request: Request):
    _user(request)
    return {"ok": True, "data": FIELDS, "scoreMin": SCORE_MIN}


@router.get("/api/agent-review/sessions")
async def api_sessions(
    request: Request,
    q: str = Query(""),
    kind: str = Query("all"),
    source_id: str = Query(""),
    limit: int = Query(400),
    watch_only: bool = Query(True),
):
    _user(request)
    k = str(kind or "all").strip().lower()
    if k not in ("group", "all"):
        k = "all"
    try:
        data = await W.list_merged_groups(
            kind=k,
            q=q,
            source_id=source_id,
            limit=max(1, min(int(limit or 400), 1000)),
            watch_only=bool(watch_only),
        )
    except W.WecomError as e:
        raise _http(e)
    watch_groups = list_watch_groups()
    rows = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("display_name") or it.get("username") or "")
        sid = str(it.get("username") or it.get("session_id") or "")
        watched = group_is_watched(name, sid)
        if watch_only and watch_groups and not watched:
            continue
        delivery = is_delivery_name(name)
        rows.append({
            "session_id": sid,
            "source_id": str(it.get("source_id") or source_id or "*"),
            "name": name,
            "msg_count": it.get("msg_count") or 0,
            "last_time": it.get("last_time") or "",
            "synced_at": it.get("synced_at") or "",
            "is_group": bool(it.get("is_group")),
            "is_delivery": delivery,
            "watched": watched,
            "kindLabel": "交付群" if delivery else ("群聊" if it.get("is_group") else "单聊"),
            "replicas": it.get("replicas") or [],
        })
    return {
        "ok": True,
        "count": len(rows),
        "data": rows,
        "watchGroups": watch_groups,
    }


@router.get("/api/agent-review/runs")
def api_runs(request: Request):
    _user(request)
    return {"ok": True, "data": list_runs()}


@router.post("/api/agent-review/analyze")
async def api_analyze(payload: dict, request: Request):
    _user(request)
    body = payload if isinstance(payload, dict) else {}
    sid = str(body.get("session_id") or "").strip()
    if not sid:
        raise HTTPException(400, "请先选择会话")
    kwargs = {
        "session_id": sid,
        "source_id": str(body.get("source_id") or ""),
        "name_hint": str(body.get("name") or body.get("name_hint") or ""),
        "force": bool(body.get("force")),
        "only_jiafang": False,
    }
    want_stream = bool(body.get("stream"))
    if not want_stream:
        try:
            return await analyze_session(**kwargs)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except W.WecomError as e:
            raise _http(e)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    queue: asyncio.Queue = asyncio.Queue()

    async def progress(ev: dict) -> None:
        await queue.put(dict(ev or {}))

    async def run() -> None:
        try:
            result = await analyze_session(progress=progress, **kwargs)
            await queue.put({"phase": "done", "percent": 100, "label": "分析完成", "result": result})
        except KeyError as e:
            await queue.put({"phase": "error", "message": str(e)})
        except W.WecomError as e:
            await queue.put({"phase": "error", "message": e.message})
        except ValueError as e:
            await queue.put({"phase": "error", "message": str(e)})
        except Exception as e:
            await queue.put({"phase": "error", "message": str(e)[:200]})

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


@router.get("/api/agent-review/hits")
def api_hits(
    request: Request,
    session_id: str,
    view: str = Query("all"),
    sender: str = Query(""),
    party: str = Query(""),
    offset: int = Query(0),
    limit: int = Query(40),
    tail: bool = Query(False),
):
    _user(request)
    if not str(session_id or "").strip():
        raise HTTPException(400, "缺少 session_id")
    data = list_hits(session_id, view=view, sender=sender, party=party, offset=offset, limit=limit, tail=tail)
    data["ok"] = True
    data["session_id"] = session_id
    data["view"] = view or "queue"
    return data


@router.get("/api/agent-review/examples")
def api_examples(request: Request, kind: str = Query(""), limit: int = Query(40)):
    _user(request)
    k = str(kind or "").strip()
    if k:
        return {"ok": True, "data": load_examples(k, limit)}
    return {
        "ok": True,
        "filter": load_examples("filter", limit),
        "intent": load_examples("intent", limit),
    }


@router.get("/api/agent-review/reviews")
def api_reviews(request: Request, session_id: str = ""):
    _user(request)
    return {"ok": True, "data": list_reviews(session_id)}


@router.get("/api/agent-review/reviews/one")
def api_review_one(request: Request, source_id: str = "", session_id: str = "", message_id: str = ""):
    _user(request)
    if not (session_id and message_id):
        raise HTTPException(400, "缺少 session_id / message_id")
    hit = get_hit(session_id, message_id)
    return {"ok": True, "data": get_review(source_id, session_id, message_id), "hit": hit}


@router.post("/api/agent-review/reviews")
def api_review_save(payload: dict, request: Request):
    u = _user(request)
    body = payload if isinstance(payload, dict) else {}
    body["reviewer"] = str(u.get("realName") or u.get("username") or "").strip() or "未命名"
    if body.get("correct_intent"):
        body["correct_intent_label"] = intent_label(str(body.get("correct_intent")))
    try:
        row = upsert_review(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "data": row}


@router.get("/api/admin/agent-review/stats")
def api_admin_ar_stats(request: Request):
    _admin(request)
    return {"ok": True, **example_stats()}


@router.get("/api/admin/agent-review/examples")
def api_admin_ar_examples(request: Request, kind: str = Query(""), limit: int = Query(400)):
    _admin(request)
    k = str(kind or "").strip()
    return {"ok": True, "data": list_examples(k, limit=limit), **example_stats()}


@router.patch("/api/admin/agent-review/examples/{eid}")
def api_admin_ar_example_set(eid: int, body: dict, request: Request):
    _admin(request)
    payload = body if isinstance(body, dict) else {}
    enabled = payload.get("enabled")
    pinned = payload.get("pinned")
    try:
        row = set_example(
            eid,
            enabled=None if enabled is None else bool(enabled),
            pinned=None if pinned is None else bool(pinned),
        )
    except KeyError:
        raise HTTPException(404, "样本不存在")
    return {"ok": True, "data": row}


@router.delete("/api/admin/agent-review/examples/{eid}")
def api_admin_ar_example_del(eid: int, request: Request):
    _admin(request)
    delete_example(eid)
    return {"ok": True}


@router.post("/api/admin/agent-review/examples/rebuild")
def api_admin_ar_rebuild(request: Request):
    _admin(request)
    return {"ok": True, **rebuild_examples_from_reviews()}


@router.get("/api/admin/agent-review/reviews")
def api_admin_ar_reviews(request: Request, session_id: str = ""):
    _admin(request)
    return {"ok": True, "data": list_reviews(session_id)}
