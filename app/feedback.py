"""用户反馈：正文 + 截图，存 MySQL 与 feedback/ 目录。"""
from __future__ import annotations
import re, shutil
from datetime import datetime
from pathlib import Path
from . import db
from .config import FEEDBACK_DIR

MAX_FILES = 8
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_CONTENT = 4000
MAX_REPLY = 4000
ALLOWED_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


class FeedbackError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _dt(v) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def _sanitize_name(name: str) -> str:
    base = str(name or "img").replace("\\", "/").split("/")[-1]
    base = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip() or "img"
    if len(base) > 120:
        i = base.rfind(".")
        ext = base[i:] if i > 0 else ""
        base = base[: 120 - len(ext)] + ext
    return base


def _ext(name: str) -> str:
    i = str(name or "").rfind(".")
    return name[i:].lower() if i >= 0 else ""


def _sniff(data: bytes, name: str) -> tuple[str, str]:
    ext = _ext(name)
    mime = ALLOWED_EXT.get(ext) or ""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif", "image/gif"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if mime:
        raise FeedbackError("图片内容与扩展名不符：" + (name or "file"))
    raise FeedbackError("仅支持 jpg / png / gif / webp：" + (name or "file"))


def _dir(fid: int) -> Path:
    p = FEEDBACK_DIR / str(int(fid))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pack_files(fid: int, rows: list) -> list:
    out = []
    for r in rows or []:
        fid_i = int(r["id"])
        out.append({
            "id": fid_i,
            "name": r.get("orig_name") or r.get("stored_name") or "",
            "mime": r.get("mime") or "",
            "size": int(r.get("size") or 0),
            "url": "/api/feedback/" + str(int(fid)) + "/files/" + str(fid_i),
        })
    return out


def _row_public(row: dict, files: list, *, is_admin: bool) -> dict:
    item = {
        "id": int(row["id"]),
        "content": row.get("content") or "",
        "status": row.get("status") or "new",
        "reply": row.get("reply") or "",
        "replyBy": row.get("reply_by") or "",
        "replyAt": _dt(row.get("reply_at")),
        "createdAt": _dt(row.get("created_at")),
        "updatedAt": _dt(row.get("updated_at")),
        "files": files,
    }
    item["userId"] = int(row.get("user_id") or 0)
    item["username"] = row.get("username") or ""
    item["realName"] = row.get("real_name") or ""
    item["department"] = row.get("department") or ""
    if not is_admin:
        item.pop("username", None)
    return item


def create(user: dict, content: str, uploads: list) -> dict:
    text = str(content or "").strip()
    if len(text) > MAX_CONTENT:
        raise FeedbackError("反馈内容不超过 " + str(MAX_CONTENT) + " 字")
    files = list(uploads or [])
    if len(files) > MAX_FILES:
        raise FeedbackError("最多上传 " + str(MAX_FILES) + " 张图片")
    parsed = []
    for f in files:
        if isinstance(f, dict):
            name = _sanitize_name(f.get("filename") or f.get("name") or "img")
            data = f.get("data")
        else:
            name = _sanitize_name(getattr(f, "filename", None) or "img")
            data = None
        if data is None:
            raise FeedbackError("图片读取失败")
        if not data:
            continue
        if len(data) > MAX_FILE_BYTES:
            raise FeedbackError("单张图片不超过 8MB：" + name)
        ext, mime = _sniff(data, name)
        if not _ext(name):
            name = name + ext
        parsed.append({"name": name, "data": data, "mime": mime, "ext": ext, "size": len(data)})
    if not text and not parsed:
        raise FeedbackError("请填写反馈内容或上传截图")
    uid = int(user["id"])
    conn = db.connect()
    fid = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO feedback (user_id, content, status) VALUES (%s,%s,'new')",
                (uid, text),
            )
            fid = int(cur.lastrowid)
            stored = []
            d = _dir(fid)
            for i, it in enumerate(parsed, 1):
                stored_name = str(i) + it["ext"]
                (d / stored_name).write_bytes(it["data"])
                cur.execute(
                    "INSERT INTO feedback_files (feedback_id, stored_name, orig_name, mime, size) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (fid, stored_name, it["name"][:180], it["mime"], it["size"]),
                )
                stored.append({
                    "id": int(cur.lastrowid),
                    "orig_name": it["name"],
                    "stored_name": stored_name,
                    "mime": it["mime"],
                    "size": it["size"],
                })
        row = {
            "id": fid, "user_id": uid, "content": text, "status": "new",
            "created_at": datetime.now(), "updated_at": None,
            "username": user.get("username") or "",
            "real_name": user.get("realName") or user.get("real_name") or "",
            "department": user.get("department") or "",
        }
        return _row_public(row, _pack_files(fid, stored), is_admin=False)
    except Exception:
        if fid:
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM feedback WHERE id=%s", (fid,))
            except Exception:
                pass
            shutil.rmtree(_dir(fid), ignore_errors=True)
        raise
    finally:
        conn.close()


def list_feedback(user: dict) -> dict:
    is_admin = user.get("role") == "admin"
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            if is_admin:
                cur.execute(
                    "SELECT f.*, u.username, u.real_name, u.department "
                    "FROM feedback f JOIN users u ON u.id = f.user_id "
                    "ORDER BY f.id DESC LIMIT 300"
                )
            else:
                cur.execute(
                    "SELECT f.*, u.username, u.real_name, u.department "
                    "FROM feedback f JOIN users u ON u.id = f.user_id "
                    "WHERE f.user_id = %s ORDER BY f.id DESC LIMIT 100",
                    (int(user["id"]),),
                )
            rows = cur.fetchall() or []
            ids = [int(r["id"]) for r in rows]
            files_by = {i: [] for i in ids}
            if ids:
                q = ",".join(["%s"] * len(ids))
                cur.execute("SELECT * FROM feedback_files WHERE feedback_id IN (" + q + ") ORDER BY id", ids)
                for fr in cur.fetchall() or []:
                    files_by.setdefault(int(fr["feedback_id"]), []).append(fr)
            unread = 0
            if is_admin:
                cur.execute("SELECT COUNT(*) AS n FROM feedback WHERE status = 'new'")
                unread = int((cur.fetchone() or {}).get("n") or 0)
            items = [_row_public(r, _pack_files(int(r["id"]), files_by.get(int(r["id"])) or []), is_admin=is_admin) for r in rows]
            return {"ok": True, "items": items, "unread": unread}
    finally:
        conn.close()


def unread_count(user: dict) -> int:
    """仅管理员：待处理反馈条数（供后台定时刷新角标，避免整表拉取）。"""
    if user.get("role") != "admin":
        raise FeedbackError("仅管理员可查看待处理统计", 403)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM feedback WHERE status = 'new'")
            return int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()


def _load(fid: int):
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT f.*, u.username, u.real_name, u.department "
                "FROM feedback f JOIN users u ON u.id = f.user_id WHERE f.id = %s",
                (int(fid),),
            )
            row = cur.fetchone()
            if not row:
                raise FeedbackError("反馈不存在", 404)
            cur.execute("SELECT * FROM feedback_files WHERE feedback_id = %s ORDER BY id", (int(fid),))
            files = cur.fetchall() or []
            return row, files
    finally:
        conn.close()


def get_file(user: dict, fid: int, file_id: int) -> tuple[Path, str, str]:
    row, files = _load(fid)
    is_admin = user.get("role") == "admin"
    if not is_admin and int(row["user_id"]) != int(user["id"]):
        raise FeedbackError("无权查看该反馈", 403)
    hit = None
    for fr in files:
        if int(fr["id"]) == int(file_id):
            hit = fr
            break
    if not hit:
        raise FeedbackError("图片不存在", 404)
    path = _dir(fid) / str(hit["stored_name"])
    if not path.exists():
        raise FeedbackError("图片文件缺失", 404)
    return path, str(hit.get("mime") or "image/jpeg"), str(hit.get("orig_name") or path.name)


def set_status(user: dict, fid: int, status: str) -> dict:
    if user.get("role") != "admin":
        raise FeedbackError("仅管理员可更新反馈状态", 403)
    st = str(status or "").strip()
    if st not in ("new", "read", "done"):
        raise FeedbackError("状态只能是 new / read / done")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE feedback SET status=%s, updated_at=NOW() WHERE id=%s", (st, int(fid)))
            if cur.rowcount == 0:
                raise FeedbackError("反馈不存在", 404)
    finally:
        conn.close()
    row, files = _load(fid)
    return _row_public(row, _pack_files(fid, files), is_admin=True)


def reply(user: dict, fid: int, text: str) -> dict:
    """管理员回复用户：处理结果会展示给提交者。"""
    if user.get("role") != "admin":
        raise FeedbackError("仅管理员可回复反馈", 403)
    t = str(text or "").strip()
    if not t:
        raise FeedbackError("请填写回复内容")
    if len(t) > MAX_REPLY:
        raise FeedbackError("回复不超过 " + str(MAX_REPLY) + " 字")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE feedback SET reply=%s, reply_by=%s, reply_at=NOW(), updated_at=NOW() WHERE id=%s",
                (t, str(user.get("username") or "")[:32], int(fid)),
            )
            if cur.rowcount == 0:
                raise FeedbackError("反馈不存在", 404)
    finally:
        conn.close()
    row, files = _load(fid)
    return _row_public(row, _pack_files(fid, files), is_admin=True)


def delete_feedback(user: dict, fid: int) -> None:
    row, _files = _load(fid)
    is_admin = user.get("role") == "admin"
    if not is_admin and int(row["user_id"]) != int(user["id"]):
        raise FeedbackError("无权删除该反馈", 403)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM feedback WHERE id=%s", (int(fid),))
    finally:
        conn.close()
    shutil.rmtree(_dir(fid), ignore_errors=True)
