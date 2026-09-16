"""用户注册 / 登录 / 会话。密码使用 PBKDF2-SHA256。"""
from __future__ import annotations
import hashlib, re, secrets
from datetime import datetime, timedelta
from . import db

COOKIE = "sb_session"
SESSION_DAYS = 7
USER_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
NAME_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9·•\s]{2,32}$")
DEPT_RE = re.compile(r"^.{1,64}$")
ITER = 180000


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def session_token_from_request(request) -> str:
    """cookie 或 Authorization: Bearer，供 iframe 内第三方 Cookie 被拦时续上会话。"""
    h = ""
    try:
        h = request.headers.get("authorization") or ""
    except Exception:
        h = ""
    if str(h).lower().startswith("bearer "):
        tok = h[7:].strip()
        if tok:
            return tok
    cookies = getattr(request, "cookies", None) or {}
    try:
        return cookies.get(COOKIE) or ""
    except Exception:
        return ""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITER)
    return "pbkdf2$%d$%s$%s" % (ITER, salt.hex(), dk.hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, it, salt_hex, dk_hex = str(stored or "").split("$", 3)
        if kind != "pbkdf2":
            return False
        n = int(it)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), n)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def public_user(row: dict | None) -> dict | None:
    if not row:
        return None
    uname = str(row.get("username") or "")
    need = bool(int(row.get("must_set_credentials") or 0)) or uname.startswith("_p")
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "realName": row["real_name"],
        "department": row["department"],
        "role": row["role"],
        "status": row["status"],
        "createdAt": _dt(row.get("created_at")),
        "lastLoginAt": _dt(row.get("last_login_at")),
        "mustSetCredentials": need,
    }


def _dt(v) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def _now():
    return datetime.now()


def validate_register(body: dict) -> tuple[str, str, str, str]:
    username = str((body or {}).get("username") or "").strip()
    real_name = str((body or {}).get("realName") or (body or {}).get("real_name") or "").strip()
    department = str((body or {}).get("department") or "").strip()
    password = str((body or {}).get("password") or "")
    confirm = str((body or {}).get("confirmPassword") or (body or {}).get("confirm") or "")
    if not USER_RE.fullmatch(username):
        raise AuthError("用户名为 3–32 位字母、数字或下划线")
    if not NAME_RE.fullmatch(real_name):
        raise AuthError("请填写真实姓名（2–32 字）")
    if not department or len(department) > 64:
        raise AuthError("请填写部门（不超过 64 字）")
    if len(password) < 6 or len(password) > 64:
        raise AuthError("密码长度 6–64 位")
    if password != confirm:
        raise AuthError("两次输入的密码不一致")
    if username.lower() in ("admin",) and _user_count():
        raise AuthError("该用户名不可注册")
    return username, real_name, department, password


def _user_count() -> int:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM users")
            return int((cur.fetchone() or {}).get("n") or 0)
    finally:
        conn.close()


def register(body: dict) -> dict:
    username, real_name, department, password = validate_register(body)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", (username,))
            if cur.fetchone():
                raise AuthError("用户名已被占用")
            role = "admin" if _user_count() == 0 else "user"
            cur.execute(
                "INSERT INTO users (username, real_name, department, password_hash, role, status) "
                "VALUES (%s,%s,%s,%s,%s,'active')",
                (username, real_name, department, hash_password(password), role),
            )
            uid = cur.lastrowid
            cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
            return public_user(cur.fetchone())
    finally:
        conn.close()


def extract_portal_identity(body: dict) -> tuple[str, str]:
    sess = (body or {}).get("session") if isinstance((body or {}).get("session"), dict) else {}
    nested = []
    for k in ("user", "profile", "account", "staff"):
        v = sess.get(k)
        if isinstance(v, dict):
            nested.append(v)
        v2 = (body or {}).get(k)
        if isinstance(v2, dict):
            nested.append(v2)

    def pick(blobs, keys):
        for b in blobs:
            if not isinstance(b, dict):
                continue
            for k in keys:
                v = str(b.get(k) or "").strip()
                if v and v not in ("未知", "undefined", "null"):
                    return v
        return ""

    name_keys = ("realName", "real_name", "displayName", "fullName", "trueName", "真实姓名", "姓名")
    dept_keys = ("department", "dept", "orgName", "org", "unit", "deptName", "部门")
    real_name = pick([body or {}, sess] + nested, name_keys) or pick(nested, ("name",))
    department = pick([body or {}, sess] + nested, dept_keys)
    return real_name, department


def _issue_session(cur, row: dict) -> tuple[dict, str, datetime]:
    token = secrets.token_hex(32)
    exp = _now() + timedelta(days=SESSION_DAYS)
    cur.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s,%s,%s)",
        (token, row["id"], exp),
    )
    cur.execute("UPDATE users SET last_login_at=%s WHERE id=%s", (_now(), row["id"]))
    cur.execute("SELECT * FROM users WHERE id=%s", (row["id"],))
    return public_user(cur.fetchone()), token, exp


def _new_placeholder_username(cur) -> str:
    for _ in range(8):
        uname = "_p" + secrets.token_hex(8)
        cur.execute("SELECT id FROM users WHERE username=%s", (uname,))
        if not cur.fetchone():
            return uname
    raise AuthError("无法分配临时用户名，请重试")


def portal_login(*, real_name: str, department: str, ticket: str, portal_id: str = "") -> tuple[dict, str, datetime, bool]:
    """按协同中心真实姓名+部门找到或创建本系统账号并签发会话。"""
    real_name = str(real_name or "").strip()
    department = str(department or "").strip() or "协同中心"
    ticket = str(ticket or "").strip()
    if not NAME_RE.fullmatch(real_name):
        raise AuthError("协同中心未提供有效真实姓名")
    if not department or len(department) > 64:
        raise AuthError("协同中心未提供部门")
    if len(ticket) < 8:
        raise AuthError("协同中心会话无效，请从侧栏重新打开", 401)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE real_name=%s AND department=%s AND status='active' "
                "ORDER BY id ASC",
                (real_name, department),
            )
            row = None
            for r in cur.fetchall() or []:
                if str(r.get("username") or "").lower() == "admin":
                    continue
                row = r
                break
            created = False
            if not row:
                uname = _new_placeholder_username(cur)
                cur.execute(
                    "INSERT INTO users (username, real_name, department, password_hash, role, status, must_set_credentials) "
                    "VALUES (%s,%s,%s,%s,'user','active',1)",
                    (uname, real_name, department, hash_password(secrets.token_urlsafe(32))),
                )
                uid = cur.lastrowid
                cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
                row = cur.fetchone()
                created = True
            elif row.get("status") != "active":
                raise AuthError("账号已停用，请联系管理员", 403)
            user, token, exp = _issue_session(cur, row)
            return user, token, exp, created
    finally:
        conn.close()


def complete_credentials(uid: int, body: dict) -> dict:
    username = str((body or {}).get("username") or "").strip()
    password = str((body or {}).get("password") or "")
    confirm = str((body or {}).get("confirmPassword") or (body or {}).get("confirm") or "")
    if not USER_RE.fullmatch(username):
        raise AuthError("用户名为 3–32 位字母、数字或下划线")
    if username.lower() == "admin":
        raise AuthError("该用户名不可用")
    if username.startswith("_p"):
        raise AuthError("请设置正式用户名，不要使用系统临时名")
    if len(password) < 6 or len(password) > 64:
        raise AuthError("密码长度 6–64 位")
    if password != confirm:
        raise AuthError("两次输入的密码不一致")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
            row = cur.fetchone()
            if not row:
                raise AuthError("用户不存在", 404)
            cur.execute("SELECT id FROM users WHERE username=%s AND id<>%s", (username, uid))
            if cur.fetchone():
                raise AuthError("用户名已被占用")
            cur.execute(
                "UPDATE users SET username=%s, password_hash=%s, must_set_credentials=0 WHERE id=%s",
                (username, hash_password(password), uid),
            )
            cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
            return public_user(cur.fetchone())
    finally:
        conn.close()


def login(username: str, password: str) -> tuple[dict, str, datetime]:
    username = str(username or "").strip()
    if not username or not password:
        raise AuthError("请输入用户名和密码")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
            if not row or not verify_password(password, row.get("password_hash") or ""):
                raise AuthError("用户名或密码错误", 401)
            if row.get("status") != "active":
                raise AuthError("账号已停用，请联系管理员", 403)
            return _issue_session(cur, row)
    finally:
        conn.close()


def logout(token: str) -> None:
    if not token:
        return
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token=%s", (token,))
    finally:
        conn.close()


def user_by_token(token: str) -> dict | None:
    if not token:
        return None
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token=%s AND s.expires_at > %s",
                (token, _now()),
            )
            row = cur.fetchone()
            if not row or row.get("status") != "active":
                return None
            return public_user(row)
    finally:
        conn.close()


def list_users() -> list:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users ORDER BY id ASC")
            return [public_user(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def update_user(uid: int, body: dict, actor: dict) -> dict:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
            row = cur.fetchone()
            if not row:
                raise AuthError("用户不存在", 404)
            fields, args = [], []
            if "realName" in body or "real_name" in body:
                nm = str(body.get("realName") or body.get("real_name") or "").strip()
                if not NAME_RE.fullmatch(nm):
                    raise AuthError("真实姓名不合法")
                fields.append("real_name=%s")
                args.append(nm)
            if "department" in body:
                dep = str(body.get("department") or "").strip()
                if not dep or len(dep) > 64:
                    raise AuthError("部门不合法")
                fields.append("department=%s")
                args.append(dep)
            if "role" in body:
                role = str(body.get("role") or "")
                if role not in ("user", "admin"):
                    raise AuthError("角色无效")
                if row["role"] == "admin" and role != "admin" and _admin_count(cur) <= 1:
                    raise AuthError("不能取消最后一名管理员")
                fields.append("role=%s")
                args.append(role)
            if "status" in body:
                st = str(body.get("status") or "")
                if st not in ("active", "disabled"):
                    raise AuthError("状态无效")
                if int(row["id"]) == int(actor.get("id") or 0) and st == "disabled":
                    raise AuthError("不能停用自己的账号")
                if row["role"] == "admin" and st == "disabled" and _admin_count(cur) <= 1:
                    raise AuthError("不能停用最后一名管理员")
                fields.append("status=%s")
                args.append(st)
            pw = str(body.get("password") or "")
            if pw:
                if len(pw) < 6 or len(pw) > 64:
                    raise AuthError("新密码长度 6–64 位")
                fields.append("password_hash=%s")
                args.append(hash_password(pw))
            if not fields:
                return public_user(row)
            args.append(uid)
            cur.execute("UPDATE users SET " + ", ".join(fields) + " WHERE id=%s", args)
            if pw:
                cur.execute("DELETE FROM sessions WHERE user_id=%s", (uid,))
            cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
            return public_user(cur.fetchone())
    finally:
        conn.close()


def delete_user(uid: int, actor: dict) -> None:
    if int(uid) == int(actor.get("id") or 0):
        raise AuthError("不能删除自己的账号")
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
            row = cur.fetchone()
            if not row:
                raise AuthError("用户不存在", 404)
            if row["role"] == "admin" and _admin_count(cur) <= 1:
                raise AuthError("不能删除最后一名管理员")
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    finally:
        conn.close()


def _admin_count(cur) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND status='active'")
    return int((cur.fetchone() or {}).get("n") or 0)
