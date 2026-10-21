"""本地 MySQL 库：值班新材料入库 + 文件汇总材料入库记录。"""
from __future__ import annotations

import json
import re
from datetime import datetime

from pymysql.cursors import DictCursor

from .config import DATA_DIR, load_config
from .db import mysql_cfg
from .party import sender_of

DEFAULT_DB = "shenbaoshu_wecom"
JSON_FALLBACK = DATA_DIR / "data" / "wecom_talent_uploads.json"
_JSON_KEY_RE = re.compile(r"^([^|]*)\|([^|]*)\|(.*)$")
_READY = False


def wecom_db_name() -> str:
    c = load_config()
    raw = str(c.get("mysqlWecomDatabase") or DEFAULT_DB).strip() or DEFAULT_DB
    s = re.sub(r"[^A-Za-z0-9_]+", "", raw)
    return (s or DEFAULT_DB)[:64]


def _conn_kw(*, with_db: bool):
    cfg = mysql_cfg()
    kw = {
        "host": cfg["host"],
        "port": int(cfg["port"] or 3306),
        "user": cfg["user"],
        "password": cfg.get("password") or "",
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": True,
    }
    if with_db:
        kw["database"] = wecom_db_name()
    return kw


def connect(*, with_db: bool = True):
    import pymysql
    return pymysql.connect(**_conn_kw(with_db=with_db))


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def init_db() -> dict:
    """新建本地入库库并建表；有旧 JSON 则迁入。"""
    global _READY
    db = wecom_db_name()
    conn = connect(with_db=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `" + db + "` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()
    conn = connect(with_db=True)
    migrated = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS talent_ingest (
                  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                  file_key VARCHAR(420) NOT NULL,
                  source VARCHAR(24) NOT NULL DEFAULT 'file_summary'
                    COMMENT 'watch=值班 / file_summary=文件汇总',
                  session_id VARCHAR(180) NOT NULL DEFAULT '',
                  session_name VARCHAR(180) NOT NULL DEFAULT '',
                  message_id VARCHAR(32) NOT NULL DEFAULT '',
                  filename VARCHAR(255) NOT NULL DEFAULT '',
                  kind VARCHAR(32) NOT NULL DEFAULT '',
                  label VARCHAR(64) NOT NULL DEFAULT '',
                  attach_id VARCHAR(32) NOT NULL DEFAULT '',
                  sender VARCHAR(64) NOT NULL DEFAULT '',
                  msg_time VARCHAR(32) NOT NULL DEFAULT '',
                  ok TINYINT(1) NOT NULL DEFAULT 0,
                  status VARCHAR(24) NOT NULL DEFAULT '',
                  detail VARCHAR(255) NOT NULL DEFAULT '',
                  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  updated_at DATETIME NULL,
                  PRIMARY KEY (id),
                  UNIQUE KEY uk_file_key (file_key),
                  KEY idx_source (source),
                  KEY idx_attach (attach_id),
                  KEY idx_session (session_id),
                  KEY idx_ok_created (ok, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            migrated = _migrate_json(cur)
        _READY = True
    finally:
        conn.close()
    return {"ok": True, "database": db, "migrated": migrated}


def _migrate_json(cur) -> int:
    path = JSON_FALLBACK
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict) or not items:
        return 0
    n = 0
    for key, row in items.items():
        if not isinstance(row, dict) or not row.get("ok"):
            continue
        rec = _from_json_item(str(key), row)
        if not rec:
            continue
        _upsert_cur(cur, rec)
        n += 1
    if n:
        bak = path.with_suffix(".json.bak")
        try:
            if not bak.exists():
                path.replace(bak)
        except OSError:
            pass
    return n


def _from_json_item(key: str, row: dict) -> dict | None:
    hit = _JSON_KEY_RE.match(key or "")
    session_id = hit.group(1) if hit else ""
    message_id = hit.group(2) if hit else ""
    filename = hit.group(3) if hit else str(row.get("filename") or "")
    return {
        "file_key": key[:420],
        "source": "legacy",
        "session_id": session_id[:180],
        "session_name": "",
        "message_id": str(message_id or "")[:32],
        "filename": str(row.get("filename") or filename or "")[:255],
        "kind": str(row.get("kind") or "")[:32],
        "label": "",
        "attach_id": str(row.get("attachId") or "")[:32],
        "sender": "",
        "msg_time": "",
        "ok": 1,
        "status": "uploaded",
        "detail": str(row.get("detail") or "")[:255],
        "at": str(row.get("at") or _now()),
    }


def _upsert_cur(cur, rec: dict) -> None:
    cur.execute(
        """
        INSERT INTO talent_ingest (
          file_key, source, session_id, session_name, message_id, filename,
          kind, label, attach_id, sender, msg_time, ok, status, detail, updated_at
        ) VALUES (
          %(file_key)s, %(source)s, %(session_id)s, %(session_name)s, %(message_id)s, %(filename)s,
          %(kind)s, %(label)s, %(attach_id)s, %(sender)s, %(msg_time)s, %(ok)s, %(status)s, %(detail)s, NOW()
        )
        ON DUPLICATE KEY UPDATE
          source=IF(ok=1 AND VALUES(ok)=0, source, VALUES(source)),
          session_id=IF(VALUES(session_id)='', session_id, VALUES(session_id)),
          session_name=IF(VALUES(session_name)='', session_name, VALUES(session_name)),
          message_id=IF(VALUES(message_id)='', message_id, VALUES(message_id)),
          filename=IF(VALUES(filename)='', filename, VALUES(filename)),
          kind=IF(VALUES(kind)='', kind, VALUES(kind)),
          label=IF(VALUES(label)='', label, VALUES(label)),
          attach_id=IF(VALUES(attach_id)='', attach_id, VALUES(attach_id)),
          sender=IF(VALUES(sender)='', sender, VALUES(sender)),
          msg_time=IF(VALUES(msg_time)='', msg_time, VALUES(msg_time)),
          ok=IF(ok=1, 1, VALUES(ok)),
          status=IF(ok=1 AND VALUES(ok)=0, status, VALUES(status)),
          detail=VALUES(detail),
          updated_at=NOW()
        """,
        rec,
    )


def ensure() -> None:
    if _READY:
        return
    init_db()


def upsert(rec: dict) -> None:
    ensure()
    row = {
        "file_key": str(rec.get("file_key") or "")[:420],
        "source": str(rec.get("source") or "file_summary")[:24] or "file_summary",
        "session_id": str(rec.get("session_id") or "")[:180],
        "session_name": str(rec.get("session_name") or "")[:180],
        "message_id": str(rec.get("message_id") or "")[:32],
        "filename": str(rec.get("filename") or "")[:255],
        "kind": str(rec.get("kind") or "")[:32],
        "label": str(rec.get("label") or "")[:64],
        "attach_id": str(rec.get("attach_id") or rec.get("attachId") or "")[:32],
        "sender": sender_of(rec)[:64],
        "msg_time": str(rec.get("msg_time") or rec.get("time") or "")[:32],
        "ok": 1 if rec.get("ok") else 0,
        "status": str(rec.get("status") or ("uploaded" if rec.get("ok") else "error"))[:24],
        "detail": str(rec.get("detail") or "")[:255],
    }
    if not row["file_key"]:
        return
    conn = connect(with_db=True)
    try:
        with conn.cursor() as cur:
            _upsert_cur(cur, row)
    finally:
        conn.close()


def get_ok(key: str) -> dict | None:
    k = str(key or "").strip()
    if not k:
        return None
    try:
        ensure()
        conn = connect(with_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT attach_id AS attachId, kind, filename, ok, detail, created_at AS at, source "
                    "FROM talent_ingest WHERE file_key=%s AND ok=1 LIMIT 1",
                    (k,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    return {
        "attachId": str(row.get("attachId") or ""),
        "kind": str(row.get("kind") or ""),
        "filename": str(row.get("filename") or ""),
        "ok": True,
        "detail": str(row.get("detail") or ""),
        "at": str(row.get("at") or ""),
        "source": str(row.get("source") or ""),
    }


def load_ok_map() -> dict:
    try:
        ensure()
        conn = connect(with_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT file_key, attach_id AS attachId, kind, filename, detail, created_at AS at, source "
                    "FROM talent_ingest WHERE ok=1 ORDER BY id DESC LIMIT 5000"
                )
                rows = cur.fetchall() or []
        finally:
            conn.close()
    except Exception:
        return {}
    out = {}
    for row in rows:
        key = str((row or {}).get("file_key") or "")
        if not key or key in out:
            continue
        out[key] = {
            "attachId": str(row.get("attachId") or ""),
            "kind": str(row.get("kind") or ""),
            "filename": str(row.get("filename") or ""),
            "ok": True,
            "detail": str(row.get("detail") or ""),
            "at": str(row.get("at") or ""),
            "source": str(row.get("source") or ""),
        }
    return out


SOURCE_LABEL = {
    "watch": "值班",
    "file_summary": "文件汇总",
    "legacy": "历史迁入",
    "app_edit": "确认写入",
    "local": "本地附件",
}


def _kind_label(kind: str, label: str = "") -> str:
    s = str(label or "").strip()
    if s:
        return s
    try:
        from .attachments import KINDS
        for k in KINDS:
            if str(k.get("id") or "") == str(kind or ""):
                return str(k.get("label") or kind or "")
    except Exception:
        pass
    return str(kind or "") or "材料"


def _fmt_dt(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y/%m/%d %H:%M:%S")
    return str(v)[:32]


def _row_attach_id(row: dict | None) -> str:
    raw = str((row or {}).get("attach_id") or (row or {}).get("attachId") or "").strip()
    if raw and raw not in ("未识别编号", "无编号"):
        return raw
    fname = str((row or {}).get("filename") or "")
    if not fname:
        return ""
    try:
        from .wecom_ai_bind import _UNKNOWN_ATTACH, _pick_attach_id
        from . import matcher as M
        aid = _pick_attach_id([], "", fname)
        if aid and aid != _UNKNOWN_ATTACH and M.is_book_num(aid):
            return aid
    except Exception:
        return ""
    return ""


def _public_row(row: dict) -> dict:
    src = str((row or {}).get("source") or "")
    kind = str((row or {}).get("kind") or "")
    return {
        "id": int((row or {}).get("id") or 0),
        "attachId": _row_attach_id(row),
        "source": src,
        "sourceLabel": SOURCE_LABEL.get(src) or src or "入库",
        "sessionId": str((row or {}).get("session_id") or ""),
        "sessionName": str((row or {}).get("session_name") or ""),
        "messageId": str((row or {}).get("message_id") or ""),
        "filename": str((row or {}).get("filename") or ""),
        "kind": kind,
        "label": _kind_label(kind, str((row or {}).get("label") or "")),
        "sender": sender_of(row),
        "time": str((row or {}).get("msg_time") or ""),
        "ok": bool(int((row or {}).get("ok") or 0)),
        "status": str((row or {}).get("status") or ""),
        "detail": str((row or {}).get("detail") or ""),
        "at": _fmt_dt((row or {}).get("created_at") or (row or {}).get("updated_at")),
    }


def _group_files(files: list) -> tuple[list, list]:
    groups_map: dict = {}
    order: list = []
    out_files = []
    for item in files:
        if not isinstance(item, dict):
            continue
        out_files.append(item)
        try:
            from .attachments import group_attach_id
            aid = group_attach_id(item.get("attachId")) or "无编号"
        except Exception:
            aid = str(item.get("attachId") or "").strip() or "无编号"
        if aid != "无编号":
            item["attachId"] = aid
        if aid not in groups_map:
            groups_map[aid] = {
                "attachId": "" if aid == "无编号" else aid,
                "title": aid,
                "personName": "",
                "okCount": 0,
                "failCount": 0,
                "appCount": 0,
                "opinionCount": 0,
                "files": [],
                "latestAt": "",
            }
            order.append(aid)
        g = groups_map[aid]
        g["files"].append(item)
        if item.get("personName") and not g.get("personName"):
            g["personName"] = str(item.get("personName") or "")
        if item.get("ok"):
            g["okCount"] += 1
        else:
            g["failCount"] += 1
        if item.get("source") == "app_edit":
            g["appCount"] += 1
            g["opinionCount"] = int(g.get("opinionCount") or 0) + len(item.get("opinions") or [])
        if str(item.get("at") or "") > str(g.get("latestAt") or ""):
            g["latestAt"] = item.get("at") or ""
    for g in groups_map.values():
        apps = [f for f in g["files"] if f.get("source") == "app_edit"]
        local = [f for f in g["files"] if f.get("source") == "local"]
        rest = [f for f in g["files"] if f.get("source") not in ("app_edit", "local")]
        apps.sort(key=lambda f: -int(f.get("versionN") or 0))
        local.sort(key=lambda f: (str(f.get("label") or ""), str(f.get("filename") or "")))
        rest.sort(key=lambda f: str(f.get("at") or ""), reverse=True)
        g["files"] = apps + local + rest
    groups = [groups_map[k] for k in order]
    return groups, out_files


def _app_edit_items(*, q: str = "", limit: int = 500) -> list:
    try:
        from . import talent_app_files as T
        rows = T.list_recent(q=q, limit=limit)
    except Exception:
        return []
    out = []
    for raw in rows:
        item = T.as_ingest_item(raw)
        if item:
            T.attach_task_opinions(item)
            out.append(item)
    return out


def list_board(*, q: str = "", source: str = "", ok_only: bool = True, limit: int = 3000) -> dict:
    """按申报书编号归组：本地附件、值班/文件汇总材料、确认写入的申报书版本及对应修改意见。"""
    ensure()
    q = str(q or "").strip()[:80]
    source = str(source or "").strip()
    if source not in ("watch", "file_summary", "legacy", "app_edit", "local"):
        source = ""
    try:
        lim = int(limit or 3000)
    except (TypeError, ValueError):
        lim = 3000
    lim = max(1, min(lim, 5000))
    files: list = []
    if source != "app_edit":
        where = ["1=1"]
        args: list = []
        if ok_only:
            where.append("ok=1")
        if source:
            where.append("source=%s")
            args.append(source)
        if q:
            like = "%" + re.sub(r"[%_\\]", "", q) + "%"
            where.append("(attach_id LIKE %s OR filename LIKE %s OR session_name LIKE %s OR session_id LIKE %s)")
            args.extend([like, like, like, like])
        sql = (
            "SELECT id, file_key, source, session_id, session_name, message_id, filename, "
            "kind, label, attach_id, sender, msg_time, ok, status, detail, created_at, updated_at "
            "FROM talent_ingest WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT %s"
        )
        args.append(lim)
        conn = connect(with_db=True)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall() or []
        finally:
            conn.close()
        for raw in rows:
            if isinstance(raw, dict):
                files.append(_public_row(raw))
    if source in ("", "app_edit"):
        files.extend(_app_edit_items(q=q, limit=min(lim, 800)))
    if source in ("", "local"):
        try:
            from .attachments import list_local_ingest_items
            files.extend(list_local_ingest_items(q=q, limit=min(lim, 4000)))
        except Exception:
            pass
    groups, files = _group_files(files)
    return {
        "ok": True,
        "database": wecom_db_name(),
        "groupCount": len(groups),
        "fileCount": len(files),
        "okCount": sum(1 for f in files if f.get("ok")),
        "watchCount": sum(1 for f in files if f.get("source") == "watch"),
        "summaryCount": sum(1 for f in files if f.get("source") == "file_summary"),
        "appCount": sum(1 for f in files if f.get("source") == "app_edit"),
        "localCount": sum(1 for f in files if f.get("source") == "local"),
        "groups": groups,
    }
