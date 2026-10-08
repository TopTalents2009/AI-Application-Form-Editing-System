"""部门字典：管理员增删，账号从中选择。"""
from __future__ import annotations
from pymysql.err import IntegrityError
from . import db


class DeptError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _norm(name: str) -> str:
    s = str(name or "").strip()
    if not s or len(s) > 64:
        raise DeptError("部门名称 1–64 字")
    return s


def list_departments(*, with_count: bool = False) -> list:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            if with_count:
                cur.execute(
                    "SELECT d.id, d.name, COUNT(u.id) AS n "
                    "FROM departments d LEFT JOIN users u ON u.department = d.name "
                    "GROUP BY d.id, d.name ORDER BY d.name ASC"
                )
                return [
                    {"id": int(r["id"]), "name": r["name"], "userCount": int(r["n"] or 0)}
                    for r in (cur.fetchall() or [])
                ]
            cur.execute("SELECT id, name FROM departments ORDER BY name ASC")
            return [{"id": int(r["id"]), "name": r["name"]} for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def public_names() -> list:
    return [x["name"] for x in list_departments()]


def exists(name: str) -> bool:
    s = str(name or "").strip()
    if not s:
        return False
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM departments WHERE name=%s LIMIT 1", (s,))
            return bool(cur.fetchone())
    finally:
        conn.close()


def require_known(name: str) -> str:
    s = str(name or "").strip()
    if not s or len(s) > 64:
        raise DeptError("请选择部门")
    if not exists(s):
        raise DeptError("请选择已有部门")
    return s


def ensure(name: str) -> str:
    """写入缺失部门（协同中心登录用）。"""
    s = _norm(name)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO departments (name) VALUES (%s)", (s,))
        return s
    finally:
        conn.close()


def create(name: str) -> dict:
    s = _norm(name)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("INSERT INTO departments (name) VALUES (%s)", (s,))
            except IntegrityError:
                raise DeptError("部门已存在")
            return {"id": int(cur.lastrowid), "name": s, "userCount": 0}
    finally:
        conn.close()


def delete(did: int) -> None:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM departments WHERE id=%s", (int(did),))
            row = cur.fetchone()
            if not row:
                raise DeptError("部门不存在", 404)
            cur.execute("SELECT COUNT(*) AS n FROM users WHERE department=%s", (row["name"],))
            n = int((cur.fetchone() or {}).get("n") or 0)
            if n:
                raise DeptError("仍有 " + str(n) + " 人属于该部门，请先改到其他部门")
            cur.execute("DELETE FROM departments WHERE id=%s", (int(did),))
    finally:
        conn.close()
