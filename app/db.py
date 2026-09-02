"""MySQL 连接与用户表初始化。"""
from __future__ import annotations
import pymysql
from pymysql.cursors import DictCursor
from .config import load_config

BOOTSTRAP_ADMIN = "admin"
BOOTSTRAP_PASSWORD = "Admin@123456"


def mysql_cfg() -> dict:
    c = load_config()
    if not c.get("mysqlConfigured"):
        raise RuntimeError("未配置 MySQL（config.json mysql.host / user / database）")
    return {
        "host": c["mysqlHost"],
        "port": int(c["mysqlPort"] or 3306),
        "user": c["mysqlUser"],
        "password": c.get("mysqlPassword") or "",
        "database": c["mysqlDatabase"],
    }


def connect(*, with_db: bool = True):
    cfg = mysql_cfg()
    kw = {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password": cfg["password"],
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
    }
    if with_db:
        kw["database"] = cfg["database"]
    return pymysql.connect(**kw)


def init_db() -> dict:
    """建库建表；若还没有任何用户，写入初始管理员 admin。"""
    from .auth import hash_password
    cfg = mysql_cfg()
    db = cfg["database"]
    conn = connect(with_db=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `" + db.replace("`", "") + "` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()
    conn = connect(with_db=True)
    notes = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                  username VARCHAR(32) NOT NULL,
                  real_name VARCHAR(64) NOT NULL,
                  department VARCHAR(64) NOT NULL,
                  password_hash VARCHAR(255) NOT NULL,
                  role ENUM('user','admin') NOT NULL DEFAULT 'user',
                  status ENUM('active','disabled') NOT NULL DEFAULT 'active',
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  last_login_at DATETIME NULL,
                  PRIMARY KEY (id),
                  UNIQUE KEY uk_username (username)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  token CHAR(64) NOT NULL,
                  user_id INT UNSIGNED NOT NULL,
                  expires_at DATETIME NOT NULL,
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (token),
                  KEY idx_user (user_id),
                  KEY idx_exp (expires_at),
                  CONSTRAINT fk_sess_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            cur.execute("SELECT COUNT(*) AS n FROM users")
            n = int((cur.fetchone() or {}).get("n") or 0)
            if n == 0:
                cur.execute(
                    "INSERT INTO users (username, real_name, department, password_hash, role, status) "
                    "VALUES (%s,%s,%s,%s,'admin','active')",
                    (BOOTSTRAP_ADMIN, "系统管理员", "管理部", hash_password(BOOTSTRAP_PASSWORD)),
                )
                notes.append("已创建初始管理员 " + BOOTSTRAP_ADMIN)
            else:
                notes.append("用户表已有 " + str(n) + " 人")
    finally:
        conn.close()
    return {"ok": True, "database": db, "notes": notes}
