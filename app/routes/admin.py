"""管理员：用户列表与账号管理。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from ..config import STATIC_DIR
from .. import auth

router = APIRouter()


def _admin(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    if u.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return u


@router.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html", headers={"Cache-Control": "no-cache"})


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
