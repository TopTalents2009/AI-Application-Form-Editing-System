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
