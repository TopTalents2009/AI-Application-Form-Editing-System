"""登录 / 注册 / 当前用户。"""
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from urllib.parse import quote
from ..config import STATIC_DIR
from .. import auth

router = APIRouter()


def _cookie_args(exp):
    return {
        "key": auth.COOKIE,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
        "max_age": auth.SESSION_DAYS * 86400,
    }


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(value=token, **_cookie_args(None))


@router.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html", headers={"Cache-Control": "no-cache"})


@router.get("/register")
def register_page():
    return FileResponse(STATIC_DIR / "register.html", headers={"Cache-Control": "no-cache"})


@router.post("/api/auth/register")
def api_register(body: dict, response: Response):
    try:
        user = auth.register(body or {})
        u2, token, exp = auth.login(user["username"], str((body or {}).get("password") or ""))
        _set_session(response, token)
        return {"ok": True, "user": u2}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/auth/login")
def api_login(body: dict, response: Response):
    try:
        user, token, exp = auth.login(str((body or {}).get("username") or ""), str((body or {}).get("password") or ""))
        _set_session(response, token)
        return {"ok": True, "user": user, "redirect": "/admin" if user.get("role") == "admin" else "/"}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/auth/logout")
def api_logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE) or "")
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def api_me(request: Request):
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return {"ok": True, "user": u}
