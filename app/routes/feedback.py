"""用户反馈 API。"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from .. import feedback as F

router = APIRouter()


def _user(request: Request) -> dict:
    u = getattr(request.state, "user", None)
    if not u:
        raise HTTPException(401, "未登录")
    return u


@router.get("/api/feedback")
def api_list(request: Request):
    u = _user(request)
    try:
        return F.list_feedback(u)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.get("/api/feedback/unread")
def api_unread(request: Request):
    u = _user(request)
    try:
        return {"ok": True, "unread": F.unread_count(u)}
    except F.FeedbackError as e:
        raise HTTPException(e.status, e.message)
    except Exception as e:
        raise HTTPException(503, "数据库不可用：" + str(e)[:160])


@router.post("/api/feedback")
async def api_create(request: Request):
    u = _user(request)
    ct = (request.headers.get("content-type") or "").lower()
    uploads = []
    content = ""
    try:
        if "multipart/form-data" in ct:
            form = await request.form()
            content = str(form.get("content") or "")
            for item in form.getlist("files"):
                if not hasattr(item, "read"):
                    continue
                data = await item.read()
                uploads.append({"filename": getattr(item, "filename", "") or "img", "data": data})
        else:
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
            content = str(body.get("content") or "")
            import base64
            for it in body.get("files") or []:
                if not isinstance(it, dict):
                    continue
                raw = it.get("dataB64") or it.get("data") or ""
                try:
                    data = base64.b64decode(raw)
                except Exception:
                    raise HTTPException(400, "图片编码无效")
                uploads.append({"filename": it.get("name") or "img", "data": data})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, "无法读取反馈：" + str(e)[:120])
    try:
        item = F.create(u, content, uploads)
        return {"ok": True, "item": item}
    except F.FeedbackError as e:
        raise HTTPException(e.status, e.message)


@router.get("/api/feedback/{fid}/files/{file_id}")
def api_file(fid: int, file_id: int, request: Request):
    u = _user(request)
    try:
        path, mime, name = F.get_file(u, fid, file_id)
    except F.FeedbackError as e:
        raise HTTPException(e.status, e.message)
    return FileResponse(
        path,
        media_type=mime or "application/octet-stream",
        filename=name,
        content_disposition_type="inline",
    )


@router.patch("/api/feedback/{fid}")
def api_status(fid: int, body: dict, request: Request):
    u = _user(request)
    try:
        item = F.set_status(u, fid, (body or {}).get("status"))
        return {"ok": True, "item": item}
    except F.FeedbackError as e:
        raise HTTPException(e.status, e.message)


@router.post("/api/feedback/{fid}/reply")
def api_reply(fid: int, body: dict, request: Request):
    u = _user(request)
    try:
        item = F.reply(u, fid, (body or {}).get("reply"))
        return {"ok": True, "item": item}
    except F.FeedbackError as e:
        raise HTTPException(e.status, e.message)


@router.delete("/api/feedback/{fid}")
def api_delete(fid: int, request: Request):
    u = _user(request)
    try:
        F.delete_feedback(u, fid)
        return {"ok": True}
    except F.FeedbackError as e:
        raise HTTPException(e.status, e.message)
