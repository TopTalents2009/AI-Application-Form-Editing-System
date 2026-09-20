"""登录用户：申请开放 API、查看自己的密钥、下载对接文档。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from urllib.parse import quote
from pathlib import Path
from .. import apikeys

router = APIRouter()
DOC_PATH = Path(__file__).resolve().parent.parent.parent / "开放api文档.md"


def _user(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return u


@router.get("/api/openapi/guide")
def api_guide(request: Request):
    _user(request)
    if not DOC_PATH.is_file():
        raise HTTPException(404, "文档文件不存在")
    name = "申报书开放API文档.md"
    return FileResponse(
        str(DOC_PATH),
        media_type="text/markdown; charset=utf-8",
        filename=name,
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name),
        },
    )


@router.get("/api/openapi/mine")
def api_mine(request: Request):
    _user(request)
    try:
        return apikeys.list_mine(_user(request))
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/openapi/apply")
def api_apply(body: dict, request: Request):
    u = _user(request)
    try:
        return {"ok": True, "request": apikeys.apply_key(u, body or {})}
    except apikeys.ApiKeyError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/openapi/requests/{rid}/ack")
def api_ack(rid: int, request: Request):
    u = _user(request)
    try:
        apikeys.ack_secret(u, rid)
        return {"ok": True}
    except apikeys.ApiKeyError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])
