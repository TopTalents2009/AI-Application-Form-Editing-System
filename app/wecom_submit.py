"""把已配对的申报书+意见建成任务（停在待确认计划）。"""
from __future__ import annotations
import base64
from . import wecom_client as W
from .wecom_cases import opinion_txt_bytes, resolve_app_upload, strip_cache_prefix
from .runner import sanitize


async def grab_copies(copies: list, *, wait_s: float = 50.0) -> dict:
    last = {"kind": "missing", "detail": "未缓存"}
    try:
        got = await W.fetch_attachment_any(copies, wait_s=wait_s)
    except W.WecomError as e:
        return {"kind": "error", "detail": e.message}
    if got.get("kind") == "file" and got.get("content"):
        return got
    last = got
    if last.get("kind") == "pending":
        last = dict(last)
        last["detail"] = last.get("detail") or "远端助手还没回传文件"
    return last


async def submit_cases(
    runner,
    cases: list,
    *,
    session_id: str,
    session_name: str = "",
    owner: str = "",
    force: bool = False,
    source_tag: str = "wecom",
) -> dict:
    """下载附件并 runner.create + enqueue。不自动确认写入。"""
    runnable, blocked = [], []
    for c in cases or []:
        if not isinstance(c, dict):
            continue
        if c.get("existing") and not force:
            blocked.append({
                "id": c.get("id"),
                "filename": (c.get("app") or {}).get("filename"),
                "detail": "该群文件已经上传过，可打开已有任务",
            })
            continue
        if source_tag == "wecom-watch" and c.get("similar") and not force:
            blocked.append({
                "id": c.get("id"),
                "filename": (c.get("app") or {}).get("filename"),
                "detail": "系统里已有较接近的修改任务",
            })
            continue
        if not (c.get("app") or {}).get("copies") and not c.get("ready"):
            blocked.append({
                "id": c.get("id"),
                "filename": (c.get("app") or {}).get("filename"),
                "detail": "申报书未在各电脑缓存",
            })
            continue
        runnable.append(c)
    cases = runnable[:20]
    if not cases:
        return {
            "ok": False,
            "created": [],
            "skipped": [],
            "errors": blocked or [{"detail": "没有可上传的申报书"}],
        }

    created, skipped, errors = [], [], []
    for c in cases:
        app = c.get("app") or {}
        got = await grab_copies(app.get("copies") or [])
        if got.get("kind") != "file":
            errors.append({"id": c.get("id"), "filename": app.get("filename"), "detail": got.get("detail") or "申报书未缓存"})
            continue
        raw = got.get("content") or b""
        if len(raw) < 64:
            errors.append({"id": c.get("id"), "filename": app.get("filename"), "detail": "申报书文件过小"})
            continue
        decided = resolve_app_upload(
            str(app.get("filename") or ""),
            str(got.get("filename") or ""),
            app.get("message_id"),
        )
        if not decided.get("ok"):
            errors.append({
                "id": c.get("id"),
                "filename": decided.get("filename") or app.get("filename"),
                "detail": decided.get("detail") or "不是申报书",
            })
            continue
        aname = sanitize(str(decided.get("filename") or app.get("filename") or "申报书.pdf"))
        opinions = []
        used = {aname}
        for op in c.get("opinions") or []:
            oname = sanitize(str(op.get("filename") or "意见.txt"))
            base, n = oname, 2
            while oname in used:
                stem, ext = (base.rsplit(".", 1) + [""])[:2] if "." in base else (base, "")
                oname = stem + "-" + str(n) + (("." + ext) if ext else "")
                n += 1
            used.add(oname)
            if op.get("kind") == "text":
                opinions.append({"name": oname, "dataB64": base64.b64encode(opinion_txt_bytes(op)).decode()})
                continue
            og = await grab_copies(op.get("copies") or [])
            if og.get("kind") != "file":
                skipped.append({"id": c.get("id"), "filename": oname, "detail": og.get("detail") or "意见文档未缓存"})
                continue
            opinions.append({
                "name": sanitize(strip_cache_prefix(str(og.get("filename") or oname), op.get("message_id")) or oname),
                "dataB64": base64.b64encode(og.get("content") or b"").decode(),
            })
        try:
            t = runner.create({
                "engine": "api",
                "app": {"name": aname, "dataB64": base64.b64encode(raw).decode()},
                "opinions": opinions,
                "source": source_tag,
                "wecom": {
                    "caseId": c.get("id"),
                    "sessionId": session_id,
                    "sessionName": session_name,
                    "appFile": aname,
                    "messageId": app.get("message_id"),
                    "watch": source_tag == "wecom-watch",
                },
            }, owner=str(owner or ""))
        except ValueError as e:
            errors.append({"id": c.get("id"), "filename": aname, "detail": str(e)})
            continue
        runner.enqueue(t["id"])
        created.append({"id": t["id"], "url": "/t/" + t["id"], "filename": aname, "caseId": c.get("id")})
    return {
        "ok": bool(created),
        "created": created,
        "skipped": skipped,
        "errors": (blocked + errors),
        "hint": "已上传的任务会生成修改计划，确认后才会写入文件。" if created else "",
    }
