"""人才库对应的字段 JSON 版本。"""
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from .. import talent_app_files as T

router = APIRouter()


def _user(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return u


def _can_read(user: dict, row: dict) -> bool:
    if user.get("role") == "admin":
        return True
    tid = str(row.get("task_id") or "")
    if not tid:
        return False
    try:
        from ..main import runner
        t = runner.get(tid)
    except Exception:
        t = None
    if not t:
        return False
    return str(t.get("owner") or "") == str(user.get("username") or "")


@router.get("/api/talent-files")
def api_list(request: Request, attachId: str = ""):
    u = _user(request)
    aid = str(attachId or "").strip()
    if not aid:
        raise HTTPException(400, "请填写人才编号 attachId")
    try:
        rows = T.list_by_attach(aid)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])
    items = []
    for row in rows:
        if not _can_read(u, row):
            continue
        items.append(T.public_row(row))
    return {"ok": True, "attachId": aid, "items": items}


@router.get("/api/talent-files/{fid}")
def api_get(fid: int, request: Request):
    u = _user(request)
    try:
        row = T.get_row(fid)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])
    if not row:
        raise HTTPException(404, "记录不存在")
    if not _can_read(u, row):
        raise HTTPException(404, "记录不存在")
    try:
        export = T.load_export(row)
    except T.TalentFileError as e:
        raise HTTPException(e.status, e.message)
    return JSONResponse({"ok": True, "item": T.public_row(row), "export": export})


@router.get("/api/talent-files/{fid}/file")
def api_file(fid: int, request: Request):
    u = _user(request)
    try:
        row = T.get_row(fid)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])
    if not row:
        raise HTTPException(404, "记录不存在")
    if not _can_read(u, row):
        raise HTTPException(404, "记录不存在")
    try:
        data, mime, name = T.load_file_bytes(row)
    except T.TalentFileError as e:
        raise HTTPException(e.status, e.message)
    return Response(
        content=data,
        media_type=mime or "application/json",
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(name or "talent.json"),
        },
    )
