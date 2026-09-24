"""任务产出的人才库字段 JSON：按 attach_id 入库，版本 v1 / v2 / v3 递增。

入库内容与 831 申报书生成的 database.json 一致：
{"talent": {..., "payload": {...}, "last_application": {...}}, "enterprise": {...}}
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import db


class TalentFileError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def version_label(n: int) -> str:
    return "v" + str(max(1, int(n)))


def _dt(v) -> str:
    if not v:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def public_row(row: dict | None, *, with_url: bool = True) -> dict | None:
    if not row:
        return None
    fid = int(row["id"])
    item = {
        "id": fid,
        "attachId": str(row.get("attach_id") or ""),
        "version": str(row.get("version") or ""),
        "versionN": int(row.get("version_n") or 0),
        "talentId": int(row["talent_id"]) if row.get("talent_id") not in (None, "") else None,
        "personName": str(row.get("person_name") or ""),
        "mode": str(row.get("mode") or ""),
        "taskId": str(row.get("task_id") or ""),
        "name": str(row.get("orig_name") or row.get("stored_name") or ""),
        "mime": str(row.get("mime") or "application/json"),
        "size": int(row.get("size") or 0),
        "createdAt": _dt(row.get("created_at")),
    }
    if with_url:
        item["url"] = "/api/talent-files/" + str(fid) + "/file"
    return item


def next_version_n(attach_id: str) -> int:
    aid = str(attach_id or "").strip()
    if not aid:
        return 1
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(version_n) AS n FROM talent_app_files WHERE attach_id=%s",
                (aid,),
            )
            row = cur.fetchone() or {}
            return int(row.get("n") or 0) + 1
    finally:
        conn.close()


def _pool_meta(task: dict) -> dict:
    out = {"talent_id": None, "person_name": "", "mode": "", "attach_id": ""}
    from .wecom_locate import identity_of
    ident = identity_of(task)
    out["attach_id"] = str(ident.get("attachId") or "")
    out["person_name"] = str(ident.get("personName") or "")
    app = task.get("app") if isinstance(task.get("app"), dict) else {}
    out["mode"] = str(app.get("mode") or "")
    pool_path = Path(str(task.get("dir") or "")) / "work" / "tmp" / "pool.json"
    if not pool_path.is_file():
        return out
    try:
        snap = json.loads(pool_path.read_text(encoding="utf-8"))
    except Exception:
        return out
    talent = snap.get("talent") if isinstance(snap, dict) and isinstance(snap.get("talent"), dict) else {}
    if talent.get("id") not in (None, ""):
        try:
            out["talent_id"] = int(talent["id"])
        except (TypeError, ValueError):
            pass
    if talent.get("attach_id") and not out["attach_id"]:
        out["attach_id"] = str(talent.get("attach_id") or "")
    if talent.get("name") and not out["person_name"]:
        out["person_name"] = str(talent.get("name") or "")
    if talent.get("mode") and not out["mode"]:
        out["mode"] = str(talent.get("mode") or "")
    return out


def _normalize_export(export: dict | None) -> dict | None:
    if not isinstance(export, dict):
        return None
    if isinstance(export.get("talent"), dict):
        out = {"talent": dict(export["talent"])}
        if isinstance(export.get("enterprise"), dict):
            out["enterprise"] = export["enterprise"]
        return out
    if "payload" in export or "attach_id" in export:
        return {"talent": dict(export)}
    return None


def _load_output_json(task: dict) -> dict | None:
    out_dir = Path(str(task.get("dir") or "")) / "work" / "output"
    if not out_dir.is_dir():
        return None
    hits = [p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() == ".json" and p.stat().st_size > 0]
    hits.sort(key=lambda x: (("人才库" not in x.name), -x.stat().st_mtime))
    for p in hits:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        norm = _normalize_export(data)
        if norm:
            return norm
    return None


def save_from_task(task: dict, export: dict | None = None) -> dict | None:
    """确认写入成功后：把人才库字段 JSON 写入 talent_app_files，版本从 v1 递增。"""
    packed = _normalize_export(export) or _load_output_json(task)
    if not packed:
        return None
    talent = packed["talent"]
    meta = _pool_meta(task)
    aid = str(talent.get("attach_id") or meta.get("attach_id") or "").strip()
    if not aid:
        return None
    talent["attach_id"] = aid
    if meta.get("talent_id") and talent.get("id") in (None, ""):
        talent["id"] = meta["talent_id"]
    if meta.get("person_name") and not talent.get("name"):
        talent["name"] = meta["person_name"]
    if meta.get("mode") and not talent.get("mode"):
        talent["mode"] = meta["mode"]
    packed["talent"] = talent
    raw = json.dumps(packed, ensure_ascii=False)
    data = raw.encode("utf-8")
    if not data:
        return None
    orig_name = aid + "-人才库.json"
    n = next_version_n(aid)
    last_err = None
    for _ in range(8):
        ver = version_label(n)
        stored_name = ver + ".json"
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO talent_app_files "
                    "(attach_id, version, version_n, talent_id, person_name, mode, task_id, "
                    "orig_name, stored_name, mime, size, payload_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        aid[:32],
                        ver[:16],
                        int(n),
                        talent.get("id") if talent.get("id") not in (None, "") else meta.get("talent_id"),
                        str(talent.get("name") or meta.get("person_name") or "")[:128],
                        str(talent.get("mode") or meta.get("mode") or "")[:8],
                        str(task.get("id") or "")[:64],
                        orig_name[:255],
                        stored_name[:180],
                        "application/json",
                        len(data),
                        raw,
                    ),
                )
                fid = int(cur.lastrowid)
            row = get_row(fid)
            if row:
                return row
        except Exception as e:
            last_err = e
            code = e.args[0] if getattr(e, "args", None) else None
            if code == 1062:
                n += 1
                continue
            raise
        finally:
            conn.close()
        n += 1
    if last_err:
        raise last_err
    return None


def get_row(fid: int) -> dict | None:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, attach_id, version, version_n, talent_id, person_name, mode, task_id, "
                "orig_name, stored_name, mime, size, created_at "
                "FROM talent_app_files WHERE id=%s",
                (int(fid),),
            )
            return cur.fetchone()
    finally:
        conn.close()


def list_by_attach(attach_id: str, *, limit: int = 50) -> list[dict]:
    aid = str(attach_id or "").strip()
    if not aid:
        return []
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, attach_id, version, version_n, talent_id, person_name, mode, task_id, "
                "orig_name, stored_name, mime, size, created_at "
                "FROM talent_app_files WHERE attach_id=%s "
                "ORDER BY version_n DESC, id DESC LIMIT %s",
                (aid, int(limit)),
            )
            return list(cur.fetchall() or [])
    finally:
        conn.close()


def load_export(row: dict) -> dict:
    fid = int(row["id"])
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload_json, file_data FROM talent_app_files WHERE id=%s",
                (fid,),
            )
            hit = cur.fetchone() or {}
    finally:
        conn.close()
    text = hit.get("payload_json")
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")
    if text:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    blob = hit.get("file_data")
    if blob:
        raw = bytes(blob)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict):
            return data
    raise TalentFileError("人才库 JSON 缺失", 404)


def load_file_bytes(row: dict) -> tuple[bytes, str, str]:
    """返回 (json_bytes, mime, filename)，供下载。"""
    export = load_export(row)
    data = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
    aid = re.sub(r"[^A-Za-z0-9_-]", "", str(row.get("attach_id") or "talent")) or "talent"
    ver = str(row.get("version") or "v1")
    name = aid + "-人才库-" + ver + ".json"
    return data, "application/json; charset=utf-8", name
