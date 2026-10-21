"""管理员：用户列表与账号管理。"""
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from ..config import STATIC_DIR
from .. import auth

router = APIRouter()

ADMIN_PAGES = frozenset({
    "overview", "newtask", "users", "feedback", "wecom", "ingest",
    "chatinsight", "aireview", "openapi", "alert", "config", "system",
})


def _admin(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    if u.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return u


@router.get("/admin")
@router.get("/admin/{page}")
def admin_page(page: str = ""):
    if page == "files":
        return RedirectResponse("/admin/wecom", status_code=302)
    if page and page not in ADMIN_PAGES:
        return RedirectResponse("/admin/overview", status_code=302)
    return FileResponse(STATIC_DIR / "admin.html", headers={"Cache-Control": "no-cache"})


@router.post("/api/admin/users")
def api_user_create(body: dict, request: Request):
    actor = _admin(request)
    try:
        return {"ok": True, "user": auth.create_user(body or {}, actor)}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.get("/api/admin/users")
def api_users(request: Request):
    _admin(request)
    try:
        return {"ok": True, "users": auth.list_users()}
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.patch("/api/admin/users/{uid}")
def api_user_update(uid: int, body: dict, request: Request):
    actor = _admin(request)
    try:
        return {"ok": True, "user": auth.update_user(uid, body or {}, actor)}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)


@router.delete("/api/admin/users/{uid}")
def api_user_delete(uid: int, request: Request):
    actor = _admin(request)
    try:
        auth.delete_user(uid, actor)
        return {"ok": True}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)


@router.get("/api/admin/departments")
def api_depts(request: Request):
    _admin(request)
    from .. import depts
    try:
        return {"ok": True, "items": depts.list_departments(with_count=True)}
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/admin/departments")
def api_dept_create(body: dict, request: Request):
    _admin(request)
    from .. import depts
    try:
        return {"ok": True, "item": depts.create((body or {}).get("name") or "")}
    except depts.DeptError as e:
        raise HTTPException(e.status, e.message)


@router.delete("/api/admin/departments/{did}")
def api_dept_delete(did: int, request: Request):
    _admin(request)
    from .. import depts
    try:
        depts.delete(did)
        return {"ok": True}
    except depts.DeptError as e:
        raise HTTPException(e.status, e.message)


@router.get("/api/admin/apikeys")
def api_keys_list(request: Request):
    _admin(request)
    from .. import apikeys
    try:
        return {"ok": True, "keys": apikeys.list_keys()}
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/admin/apikeys")
def api_keys_create(body: dict, request: Request):
    actor = _admin(request)
    from .. import apikeys
    try:
        return {"ok": True, "key": apikeys.create_key(body or {}, actor)}
    except apikeys.ApiKeyError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.patch("/api/admin/apikeys/{kid}")
def api_keys_update(kid: int, body: dict, request: Request):
    _admin(request)
    from .. import apikeys
    try:
        return {"ok": True, "key": apikeys.update_key(kid, body or {})}
    except apikeys.ApiKeyError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.delete("/api/admin/apikeys/{kid}")
def api_keys_delete(kid: int, request: Request):
    _admin(request)
    from .. import apikeys
    try:
        apikeys.delete_key(kid)
        return {"ok": True}
    except apikeys.ApiKeyError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.get("/api/admin/api-logs")
def api_logs_list(request: Request, keyId: int = 0, status: str = "", q: str = "",
                  since: str = "", until: str = "", limit: int = 50, offset: int = 0):
    _admin(request)
    from .. import apikeys
    try:
        return apikeys.list_logs(
            key_id=keyId, status_group=status, q=q, since=since, until=until,
            limit=limit, offset=offset,
        )
    except apikeys.ApiKeyError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.delete("/api/admin/api-logs")
def api_logs_purge(request: Request, days: int = 30):
    _admin(request)
    from .. import apikeys
    try:
        n = apikeys.purge_logs(before_days=days)
        return {"ok": True, "deleted": n}
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.get("/api/admin/api-requests")
def api_requests_list(request: Request, status: str = ""):
    _admin(request)
    from .. import apikeys
    try:
        return {
            "ok": True,
            "pending": apikeys.pending_request_count(),
            "requests": apikeys.list_requests(status=status),
        }
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/admin/api-requests/{rid}/review")
def api_requests_review(rid: int, body: dict, request: Request):
    actor = _admin(request)
    from .. import apikeys
    try:
        return apikeys.review_request(rid, body or {}, actor)
    except apikeys.ApiKeyError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.get("/api/admin/alert")
def api_alert_status(request: Request):
    _admin(request)
    from .. import alert as AL
    return AL.public_status()


@router.post("/api/admin/alert/test")
def api_alert_test(request: Request):
    _admin(request)
    from .. import alert as AL
    from .. import wecom_notify as N
    if not N.configured():
        raise HTTPException(400, "未配置 alert.webhook")
    got = AL.test_send()
    if not got.get("ok"):
        raise HTTPException(400, got.get("error") or got.get("skipped") or "发送失败")
    return {"ok": True, "item": got}


@router.get("/api/admin/due-broadcast")
def api_due_broadcast_status(request: Request):
    _admin(request)
    from .. import due_broadcast as DB
    return DB.public_status()


@router.post("/api/admin/due-broadcast/test")
def api_due_broadcast_test(request: Request):
    _admin(request)
    from .. import due_broadcast as DB
    from .. import wecom_notify as N
    if not N.configured():
        raise HTTPException(400, "未配置 alert.webhook")
    got = DB.send_now(test=True, reason="test")
    if not got.get("ok"):
        raise HTTPException(400, got.get("error") or "发送失败")
    return {"ok": True, **got}


@router.post("/api/admin/due-broadcast/run")
def api_due_broadcast_run(request: Request):
    _admin(request)
    from .. import due_broadcast as DB
    from .. import wecom_notify as N
    if not N.configured():
        raise HTTPException(400, "未配置 alert.webhook")
    got = DB.send_now(test=False, reason="manual")
    if not got.get("ok"):
        raise HTTPException(400, got.get("error") or "发送失败")
    return {"ok": True, **got}


@router.get("/api/admin/alert/logs")
def api_alert_logs(request: Request, st: str = ""):
    _admin(request)
    from .. import alert as AL
    return {"ok": True, "items": AL.list_logs(st)}


@router.delete("/api/admin/alert/logs")
def api_alert_logs_clear(request: Request):
    _admin(request)
    from .. import alert as AL
    AL.clear_logs()
    return {"ok": True}


@router.get("/api/admin/talent-ingest")
def api_talent_ingest(
    request: Request,
    q: str = Query(""),
    source: str = Query(""),
    ok: str = Query("1"),
):
    """入库看板：本地附件、值班 / 文件汇总材料，以及确认写入的申报书版本和对应修改意见。"""
    _admin(request)
    ok_only = str(ok or "1").strip() not in ("0", "all", "false", "no")
    try:
        from ..wecom_talent_db import list_board
        return list_board(q=q, source=source, ok_only=ok_only)
    except Exception as e:
        raise HTTPException(503, "入库库不可用：" + str(e)[:160])


@router.get("/api/admin/local-attach")
def api_local_attach(request: Request, path: str = Query("")):
    """按「附件/{编号}/…」相对路径下载本机对应文件。"""
    _admin(request)
    rel = str(path or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise HTTPException(400, "缺少文件路径")
    from .. import attachments as A
    from .. import papers as P
    try:
        fp = A._safe_local_file(str(A.LOCAL_ATTACH_ROOT / rel))
    except P.PapersError as e:
        raise HTTPException(404, e.message)
    except Exception as e:
        raise HTTPException(404, str(e)[:160])
    name = fp.name
    return FileResponse(
        fp,
        filename=name,
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name),
        },
    )


@router.get("/api/admin/api-docs")
def api_docs_text(request: Request):
    """管理员预览开放 API 文档全文。"""
    _admin(request)
    from .api_apply import DOC_PATH
    if not DOC_PATH.is_file():
        raise HTTPException(404, "文档文件不存在")
    try:
        text = DOC_PATH.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, "读取文档失败：" + str(e)[:120])
    return {"ok": True, "markdown": text, "filename": "申报书开放API文档.md"}
