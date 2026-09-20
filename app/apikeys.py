"""开放 API 密钥与调用日志（MySQL）。哈希用于鉴权；明文存 secret_plain，供管理员后台查看。"""
from __future__ import annotations
import hashlib, re, secrets
from datetime import datetime, timedelta
from . import db

KEY_PREFIX = "sbk_"
NAME_RE = re.compile(r"^.{1,64}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9_]{0,32}$")
RATE_PER_MIN = 120


class ApiKeyError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _now():
    return datetime.now()


def _dt(v) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)[:19]


def hash_key(raw: str) -> str:
    return hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest()


def generate_secret() -> tuple[str, str]:
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:12]


def public_key(row: dict | None, *, secret: str = "", include_secret: bool = False) -> dict | None:
    if not row:
        return None
    stored = str(row.get("secret_plain") or "").strip()
    shown = str(secret or "").strip() or (stored if include_secret else "")
    out = {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "prefix": row.get("prefix") or "",
        "display": (row.get("prefix") or "") + "…",
        "ownerUsername": row.get("owner_username") or "",
        "status": row.get("status") or "active",
        "note": row.get("note") or "",
        "createdBy": row.get("created_by") or "",
        "createdAt": _dt(row.get("created_at")),
        "lastUsedAt": _dt(row.get("last_used_at")),
        "expireAt": _dt(row.get("expire_at")),
        "callCount": int(row.get("call_count") or 0),
        "hasSecret": bool(stored or shown),
    }
    if shown:
        out["secret"] = shown
    return out


def _parse_range(val, *, end: bool = False) -> datetime | None:
    s = str(val or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s[:19] if "T" in s or " " in s else s[:10], fmt)
            if fmt == "%Y-%m-%d":
                d = d.replace(hour=23, minute=59, second=59) if end else d.replace(hour=0, minute=0, second=0)
            return d
        except ValueError:
            continue
    return None


def _parse_expire(val) -> datetime | None:
    s = str(val or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s[:19] if "T" in s or " " in s else s[:10], fmt)
            if fmt == "%Y-%m-%d":
                d = d.replace(hour=23, minute=59, second=59)
            return d
        except ValueError:
            continue
    raise ApiKeyError("过期时间格式无效（YYYY-MM-DD）")


def create_key(body: dict, actor: dict) -> dict:
    name = str((body or {}).get("name") or "").strip()
    if not name or not NAME_RE.fullmatch(name):
        raise ApiKeyError("请填写密钥名称（1–64 字）")
    note = str((body or {}).get("note") or "").strip()
    if len(note) > 255:
        raise ApiKeyError("备注不超过 255 字")
    owner = str((body or {}).get("ownerUsername") or (body or {}).get("owner_username") or "").strip()
    if owner and not OWNER_RE.fullmatch(owner):
        raise ApiKeyError("绑定用户名不合法")
    expire = _parse_expire((body or {}).get("expireAt") or (body or {}).get("expire_at"))
    secret, prefix = generate_secret()
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            if owner:
                cur.execute("SELECT id FROM users WHERE username=%s", (owner,))
                if not cur.fetchone():
                    raise ApiKeyError("绑定的用户不存在", 404)
            for _ in range(6):
                try:
                    cur.execute(
                        "INSERT INTO api_keys (name, prefix, key_hash, secret_plain, owner_username, note, created_by, expire_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (name, prefix, hash_key(secret), secret, owner, note,
                         str((actor or {}).get("username") or "")[:32], expire),
                    )
                    break
                except Exception as e:
                    if getattr(e, "args", None) and e.args and e.args[0] == 1062:
                        secret, prefix = generate_secret()
                        continue
                    raise
            uid = cur.lastrowid
            cur.execute("SELECT * FROM api_keys WHERE id=%s", (uid,))
            return public_key(cur.fetchone(), secret=secret)
    finally:
        conn.close()


def list_keys() -> list:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM api_keys ORDER BY id DESC")
            return [public_key(r, include_secret=True) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def get_key(kid: int) -> dict | None:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM api_keys WHERE id=%s", (kid,))
            return public_key(cur.fetchone(), include_secret=True)
    finally:
        conn.close()


def update_key(kid: int, body: dict) -> dict:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM api_keys WHERE id=%s", (kid,))
            row = cur.fetchone()
            if not row:
                raise ApiKeyError("密钥不存在", 404)
            fields, args = [], []
            if "name" in body:
                name = str(body.get("name") or "").strip()
                if not name or not NAME_RE.fullmatch(name):
                    raise ApiKeyError("请填写密钥名称（1–64 字）")
                fields.append("name=%s")
                args.append(name)
            if "note" in body:
                note = str(body.get("note") or "").strip()
                if len(note) > 255:
                    raise ApiKeyError("备注不超过 255 字")
                fields.append("note=%s")
                args.append(note)
            if "ownerUsername" in body or "owner_username" in body:
                owner = str(body.get("ownerUsername") or body.get("owner_username") or "").strip()
                if owner and not OWNER_RE.fullmatch(owner):
                    raise ApiKeyError("绑定用户名不合法")
                if owner:
                    cur.execute("SELECT id FROM users WHERE username=%s", (owner,))
                    if not cur.fetchone():
                        raise ApiKeyError("绑定的用户不存在", 404)
                fields.append("owner_username=%s")
                args.append(owner)
            if "status" in body:
                st = str(body.get("status") or "")
                if st not in ("active", "disabled"):
                    raise ApiKeyError("状态无效")
                fields.append("status=%s")
                args.append(st)
            if "expireAt" in body or "expire_at" in body:
                fields.append("expire_at=%s")
                args.append(_parse_expire(body.get("expireAt") or body.get("expire_at")))
            if not fields:
                return public_key(row, include_secret=True)
            args.append(kid)
            cur.execute("UPDATE api_keys SET " + ", ".join(fields) + " WHERE id=%s", args)
            cur.execute("SELECT * FROM api_keys WHERE id=%s", (kid,))
            return public_key(cur.fetchone(), include_secret=True)
    finally:
        conn.close()


def delete_key(kid: int) -> None:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM api_keys WHERE id=%s", (kid,))
            if not cur.fetchone():
                raise ApiKeyError("密钥不存在", 404)
            cur.execute(
                "UPDATE api_key_requests SET status='revoked', reveal_secret='' "
                "WHERE api_key_id=%s AND status='approved'",
                (kid,),
            )
            cur.execute("DELETE FROM api_keys WHERE id=%s", (kid,))
    finally:
        conn.close()


def lookup_raw(raw: str) -> dict | None:
    """按明文密钥查找；不校验状态/过期。"""
    raw = str(raw or "").strip()
    if not raw or len(raw) < 16:
        return None
    digest = hash_key(raw)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM api_keys WHERE key_hash=%s", (digest,))
            return cur.fetchone()
    finally:
        conn.close()


def authenticate(raw: str) -> dict:
    row = lookup_raw(raw)
    if not row:
        raise ApiKeyError("无效的 API Key", 401)
    if row.get("status") != "active":
        raise ApiKeyError("API Key 已停用", 403)
    exp = row.get("expire_at")
    if exp and isinstance(exp, datetime) and exp < _now():
        raise ApiKeyError("API Key 已过期", 403)
    return row


def touch_key(kid: int) -> None:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE api_keys SET last_used_at=%s, call_count=call_count+1 WHERE id=%s",
                (_now(), kid),
            )
    finally:
        conn.close()


def task_owner_of(row: dict) -> str:
    owner = str((row or {}).get("owner_username") or "").strip()
    if owner:
        return owner
    name = str((row or {}).get("name") or "").strip() or (row or {}).get("prefix") or "openapi"
    return "api:" + name[:28]


def client_ip(request) -> str:
    xff = ""
    try:
        xff = request.headers.get("x-forwarded-for") or ""
    except Exception:
        xff = ""
    if xff:
        return xff.split(",")[0].strip()[:64]
    c = getattr(request, "client", None)
    return str(getattr(c, "host", "") or "")[:64]


def key_from_request(request) -> str:
    h = ""
    try:
        h = request.headers.get("x-api-key") or ""
    except Exception:
        h = ""
    if str(h).strip():
        return str(h).strip()
    auth = ""
    try:
        auth = request.headers.get("authorization") or ""
    except Exception:
        auth = ""
    if str(auth).lower().startswith("bearer "):
        tok = auth[7:].strip()
        if tok:
            return tok
    try:
        q = request.query_params.get("api_key") or ""
        if str(q).strip():
            return str(q).strip()
    except Exception:
        pass
    return ""


def log_call(
    *,
    key_row: dict | None,
    method: str,
    path: str,
    status_code: int,
    ip: str = "",
    user_agent: str = "",
    task_id: str = "",
    error: str = "",
    duration_ms: int = 0,
) -> None:
    kid = None
    prefix = ""
    name = ""
    if key_row:
        try:
            kid = int(key_row.get("id") or 0) or None
        except Exception:
            kid = None
        prefix = str(key_row.get("prefix") or "")[:16]
        name = str(key_row.get("name") or "")[:64]
    path = str(path or "")[:180]
    ua = str(user_agent or "")[:180]
    err = str(error or "")[:255]
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_call_logs (api_key_id, key_prefix, key_name, method, path, status_code, "
                "ip, user_agent, task_id, error, duration_ms) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (kid, prefix, name, str(method or "")[:8], path, int(status_code or 0),
                 str(ip or "")[:64], ua, str(task_id or "")[:32], err, max(0, int(duration_ms or 0))),
            )
            if secrets.randbelow(80) == 0:
                cutoff = _now() - timedelta(days=90)
                cur.execute("DELETE FROM api_call_logs WHERE created_at < %s", (cutoff,))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def public_log(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "apiKeyId": int(row["api_key_id"]) if row.get("api_key_id") else None,
        "keyPrefix": row.get("key_prefix") or "",
        "keyName": row.get("key_name") or "",
        "method": row.get("method") or "",
        "path": row.get("path") or "",
        "statusCode": int(row.get("status_code") or 0),
        "ip": row.get("ip") or "",
        "userAgent": row.get("user_agent") or "",
        "taskId": row.get("task_id") or "",
        "error": row.get("error") or "",
        "durationMs": int(row.get("duration_ms") or 0),
        "createdAt": _dt(row.get("created_at")),
    }


def list_logs(*, key_id: int = 0, status_group: str = "", q: str = "",
              since: str = "", until: str = "", limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(200, int(limit or 50)))
    offset = max(0, int(offset or 0))
    where, args = ["1=1"], []
    if key_id:
        where.append("api_key_id=%s")
        args.append(int(key_id))
    sg = str(status_group or "").strip().lower()
    if sg in ("2xx", "ok"):
        where.append("status_code BETWEEN 200 AND 299")
    elif sg in ("4xx", "client"):
        where.append("status_code BETWEEN 400 AND 499")
    elif sg in ("5xx", "server"):
        where.append("status_code >= 500")
    elif sg.isdigit():
        where.append("status_code=%s")
        args.append(int(sg))
    q = str(q or "").strip()
    if q:
        where.append("(path LIKE %s OR task_id LIKE %s OR error LIKE %s OR ip LIKE %s OR key_name LIKE %s)")
        like = "%" + q[:80] + "%"
        args.extend([like, like, like, like, like])
    since_d = _parse_range(since, end=False)
    until_d = _parse_range(until, end=True)
    if since_d:
        where.append("created_at >= %s")
        args.append(since_d)
    if until_d:
        where.append("created_at <= %s")
        args.append(until_d)
    sql_where = " AND ".join(where)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM api_call_logs WHERE " + sql_where, args)
            total = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                "SELECT * FROM api_call_logs WHERE " + sql_where +
                " ORDER BY id DESC LIMIT %s OFFSET %s",
                args + [limit, offset],
            )
            items = [public_log(r) for r in (cur.fetchall() or [])]
            today = _now().strftime("%Y-%m-%d")
            cur.execute(
                "SELECT COUNT(*) AS n FROM api_call_logs WHERE created_at >= %s",
                (today + " 00:00:00",),
            )
            today_n = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                "SELECT COUNT(*) AS n FROM api_call_logs WHERE created_at >= %s AND status_code >= 400",
                (today + " 00:00:00",),
            )
            today_err = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute("SELECT COUNT(*) AS n FROM api_keys WHERE status='active'")
            keys_n = int((cur.fetchone() or {}).get("n") or 0)
            pending = 0
            try:
                cur.execute("SELECT COUNT(*) AS n FROM api_key_requests WHERE status='pending'")
                pending = int((cur.fetchone() or {}).get("n") or 0)
            except Exception:
                pending = 0
            return {
                "ok": True,
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": items,
                "stats": {
                    "today": today_n,
                    "errorsToday": today_err,
                    "keysActive": keys_n,
                    "pendingRequests": pending,
                },
            }
    finally:
        conn.close()


def purge_logs(*, before_days: int = 30) -> int:
    days = max(0, min(3650, int(before_days)))
    cutoff = _now() - timedelta(days=days)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM api_call_logs WHERE created_at < %s", (cutoff,))
            return int(cur.rowcount or 0)
    finally:
        conn.close()


def public_request(row: dict | None, *, include_secret: bool = False) -> dict | None:
    if not row:
        return None
    out = {
        "id": int(row["id"]),
        "userId": int(row.get("user_id") or 0),
        "username": row.get("username") or "",
        "realName": row.get("real_name") or "",
        "department": row.get("department") or "",
        "purpose": row.get("purpose") or "",
        "status": row.get("status") or "pending",
        "apiKeyId": int(row["api_key_id"]) if row.get("api_key_id") else None,
        "rejectReason": row.get("reject_reason") or "",
        "reviewedBy": row.get("reviewed_by") or "",
        "reviewedAt": _dt(row.get("reviewed_at")),
        "createdAt": _dt(row.get("created_at")),
        "hasSecret": bool(str(row.get("reveal_secret") or "").strip()),
    }
    if include_secret and str(row.get("reveal_secret") or "").strip():
        out["secret"] = str(row.get("reveal_secret") or "")
    return out


def pending_request_count() -> int:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM api_key_requests WHERE status='pending'")
            return int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()


def list_requests(*, status: str = "", limit: int = 80) -> list:
    limit = max(1, min(200, int(limit or 80)))
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            if status in ("pending", "approved", "rejected", "revoked"):
                cur.execute(
                    "SELECT * FROM api_key_requests WHERE status=%s ORDER BY id DESC LIMIT %s",
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM api_key_requests ORDER BY FIELD(status,'pending','approved','revoked','rejected'), id DESC LIMIT %s",
                    (limit,),
                )
            return [public_request(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def list_mine(user: dict) -> dict:
    uid = int((user or {}).get("id") or 0)
    uname = str((user or {}).get("username") or "")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM api_key_requests WHERE user_id=%s ORDER BY id DESC LIMIT 50",
                (uid,),
            )
            reqs = [public_request(r, include_secret=True) for r in (cur.fetchall() or [])]
            keys = []
            if uname:
                cur.execute(
                    "SELECT * FROM api_keys WHERE owner_username=%s ORDER BY id DESC",
                    (uname,),
                )
                keys = [public_key(r) for r in (cur.fetchall() or [])]
            by_id = {int(k["id"]): k for k in keys}
            for req in reqs:
                kid = req.get("apiKeyId")
                if req.get("status") == "revoked":
                    req["keyStatus"] = "revoked"
                elif kid and int(kid) in by_id:
                    req["keyStatus"] = by_id[int(kid)].get("status") or "active"
                elif req.get("status") == "approved":
                    req["status"] = "revoked"
                    req["keyStatus"] = "revoked"
                    cur.execute(
                        "UPDATE api_key_requests SET status='revoked', reveal_secret='' "
                        "WHERE id=%s AND status='approved'",
                        (req["id"],),
                    )
            return {"ok": True, "requests": reqs, "keys": keys}
    finally:
        conn.close()


def apply_key(user: dict, body: dict) -> dict:
    uid = int((user or {}).get("id") or 0)
    if not uid:
        raise ApiKeyError("未登录", 401)
    purpose = str((body or {}).get("purpose") or "").strip()
    if len(purpose) < 4 or len(purpose) > 500:
        raise ApiKeyError("请填写申请用途（4–500 字）")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM api_key_requests WHERE user_id=%s AND status='pending' LIMIT 1",
                (uid,),
            )
            if cur.fetchone():
                raise ApiKeyError("已有待审核的申请，请等待管理员处理")
            cur.execute(
                "INSERT INTO api_key_requests (user_id, username, real_name, department, purpose) "
                "VALUES (%s,%s,%s,%s,%s)",
                (
                    uid,
                    str((user or {}).get("username") or "")[:32],
                    str((user or {}).get("realName") or (user or {}).get("real_name") or "")[:64],
                    str((user or {}).get("department") or "")[:64],
                    purpose,
                ),
            )
            rid = cur.lastrowid
            cur.execute("SELECT * FROM api_key_requests WHERE id=%s", (rid,))
            return public_request(cur.fetchone())
    finally:
        conn.close()


def ack_secret(user: dict, rid: int) -> None:
    uid = int((user or {}).get("id") or 0)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM api_key_requests WHERE id=%s AND user_id=%s", (rid, uid))
            row = cur.fetchone()
            if not row:
                raise ApiKeyError("申请不存在", 404)
            cur.execute("UPDATE api_key_requests SET reveal_secret='' WHERE id=%s", (rid,))
    finally:
        conn.close()


def review_request(rid: int, body: dict, actor: dict) -> dict:
    action = str((body or {}).get("action") or "").strip().lower()
    if action not in ("approve", "reject"):
        raise ApiKeyError("请选择通过或拒绝")
    reason = str((body or {}).get("reason") or (body or {}).get("rejectReason") or "").strip()
    if len(reason) > 255:
        raise ApiKeyError("拒绝原因不超过 255 字")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM api_key_requests WHERE id=%s", (rid,))
            row = cur.fetchone()
            if not row:
                raise ApiKeyError("申请不存在", 404)
            if row.get("status") != "pending":
                raise ApiKeyError("该申请已处理")
            now = _now()
            reviewer = str((actor or {}).get("username") or "")[:32]
            if action == "reject":
                cur.execute(
                    "UPDATE api_key_requests SET status='rejected', reject_reason=%s, "
                    "reviewed_by=%s, reviewed_at=%s WHERE id=%s",
                    (reason, reviewer, now, rid),
                )
                cur.execute("SELECT * FROM api_key_requests WHERE id=%s", (rid,))
                return {"ok": True, "request": public_request(cur.fetchone())}
    finally:
        conn.close()
    owner = str(row.get("username") or "").strip()
    real = str(row.get("real_name") or owner or "用户")
    key = create_key(
        {
            "name": real + "的开放API",
            "note": str(row.get("purpose") or "")[:255],
            "ownerUsername": owner,
        },
        actor,
    )
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE api_key_requests SET status='approved', api_key_id=%s, reveal_secret=%s, "
                "reviewed_by=%s, reviewed_at=%s WHERE id=%s",
                (int(key["id"]), str(key.get("secret") or ""), reviewer, _now(), rid),
            )
            cur.execute("SELECT * FROM api_key_requests WHERE id=%s", (rid,))
            return {
                "ok": True,
                "request": public_request(cur.fetchone()),
                "key": key,
            }
    finally:
        conn.close()
