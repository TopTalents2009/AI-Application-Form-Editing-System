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
        return {"ok": True, "user": u2, "token": token, "redirect": "/"}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/auth/login")
def api_login(body: dict, response: Response):
    try:
        user, token, exp = auth.login(str((body or {}).get("username") or ""), str((body or {}).get("password") or ""))
        _set_session(response, token)
        return {"ok": True, "user": user, "token": token, "redirect": "/"}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/auth/logout")
def api_logout(request: Request, response: Response):
    auth.logout(auth.session_token_from_request(request))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def api_me(request: Request):
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return {"ok": True, "user": u}


@router.get("/portal")
def portal_page():
    return FileResponse(STATIC_DIR / "portal.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/auth/portal-config")
def api_portal_config():
    from ..config import load_config
    p = load_config().get("portal") or {}
    return {
        "ok": True,
        "sdkUrl": p.get("sdkUrl") or "",
        "portalId": p.get("portalId") or "",
        "configured": bool(p.get("configured")),
    }


@router.post("/api/auth/portal-login")
def api_portal_login(body: dict, response: Response):
    body = body if isinstance(body, dict) else {}
    real_name, department = auth.extract_portal_identity(body)
    try:
        user, token, exp, created = auth.portal_login(
            real_name=real_name,
            department=department,
            ticket=str(body.get("ticket") or ((body.get("session") or {}).get("ticket") if isinstance(body.get("session"), dict) else "") or ""),
            portal_id=str(body.get("portalId") or body.get("portal_id") or ""),
        )
        _set_session(response, token)
        return {
            "ok": True,
            "user": user,
            "token": token,
            "created": created,
            "mustSetCredentials": bool(user.get("mustSetCredentials")),
            "redirect": "/",
        }
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/auth/complete-credentials")
def api_complete_credentials(body: dict, request: Request):
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    try:
        user = auth.complete_credentials(int(u["id"]), body or {})
        return {"ok": True, "user": user}
    except auth.AuthError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])
