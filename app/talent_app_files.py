"""任务产出的人才库字段 JSON：按 attach_id 入库，版本 v1 / v2 / v3 递增。

入库内容与 8091/831 申报书生成的 database.json 一致：
{"attach_id", "version", "version_n",
 "talent": {id, attach_id, name, mode, source_year, payload, last_application, ...},
 "enterprise": {...}}
"""
from __future__ import annotations

import copy
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


def _copy_version_meta(src: dict, dest: dict) -> None:
    if src.get("version"):
        dest["version"] = str(src.get("version") or "")
    if src.get("version_n") not in (None, ""):
        try:
            dest["version_n"] = int(src.get("version_n"))
        except (TypeError, ValueError):
            pass
    aid = str(src.get("attach_id") or "").strip()
    if aid:
        dest["attach_id"] = aid


def _normalize_export(export: dict | None) -> dict | None:
    if not isinstance(export, dict):
        return None
    if isinstance(export.get("talent"), dict):
        out = {"talent": dict(export["talent"])}
        if isinstance(export.get("enterprise"), dict):
            out["enterprise"] = export["enterprise"]
        _copy_version_meta(export, out)
        aid = str(out.get("attach_id") or out["talent"].get("attach_id") or "").strip()
        if aid:
            out["attach_id"] = aid
            out["talent"]["attach_id"] = aid
        return out
    if "payload" in export or "attach_id" in export:
        talent = {
            k: v for k, v in export.items()
            if k not in ("enterprise", "version", "version_n")
        }
        out = {"talent": talent}
        if isinstance(export.get("enterprise"), dict):
            out["enterprise"] = export["enterprise"]
        _copy_version_meta(export, out)
        aid = str(out.get("attach_id") or talent.get("attach_id") or "").strip()
        if aid:
            out["attach_id"] = aid
            out["talent"]["attach_id"] = aid
        return out
    return None


def stamp_export(
    export: dict | None,
    *,
    version: str,
    version_n: int,
    attach_id: str = "",
) -> dict:
    """写入与 8091 相同的版本字段：顶层 version / version_n / attach_id。"""
    packed = _normalize_export(export) or (dict(export) if isinstance(export, dict) else {})
    if not isinstance(packed.get("talent"), dict):
        packed["talent"] = {}
    packed["version"] = str(version or "").strip() or version_label(version_n)
    packed["version_n"] = int(version_n or 1)
    aid = str(attach_id or packed.get("attach_id") or packed["talent"].get("attach_id") or "").strip()
    if aid:
        packed["attach_id"] = aid
        packed["talent"]["attach_id"] = aid
    return packed


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
    packed = unmask_export(packed, app_text_for_task(task))
    talent = packed.get("talent") if isinstance(packed.get("talent"), dict) else talent
    n = next_version_n(aid)
    last_err = None
    for _ in range(8):
        ver = version_label(n)
        packed = stamp_export(packed, version=ver, version_n=n, attach_id=aid)
        raw = json.dumps(packed, ensure_ascii=False)
        data = raw.encode("utf-8")
        if not data:
            return None
        orig_name = aid + "-人才库-" + ver + ".json"
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


def list_recent(*, q: str = "", limit: int = 500) -> list[dict]:
    """入库看板用：最近确认写入的申报书字段版本。"""
    q = str(q or "").strip()[:80]
    try:
        lim = max(1, min(int(limit or 500), 2000))
    except (TypeError, ValueError):
        lim = 500
    where = ["1=1"]
    args: list = []
    if q:
        like = "%" + re.sub(r"[%\\]", "", q) + "%"
        where.append(
            "(attach_id=%s OR attach_id LIKE %s OR person_name LIKE %s OR orig_name LIKE %s OR task_id LIKE %s)"
        )
        args.extend([q, like, like, like, like])
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, attach_id, version, version_n, talent_id, person_name, mode, task_id, "
                "orig_name, stored_name, mime, size, created_at "
                "FROM talent_app_files WHERE " + " AND ".join(where) +
                " ORDER BY id DESC LIMIT %s",
                (*args, lim),
            )
            return list(cur.fetchall() or [])
    finally:
        conn.close()


def as_ingest_item(row: dict | None) -> dict | None:
    """转成入库看板行：来源=确认写入，带版本、下载和任务链接。"""
    pub = public_row(row)
    if not pub:
        return None
    ver = str(pub.get("version") or "v1")
    name = str(pub.get("name") or "").strip() or (
        str(pub.get("attachId") or "") + "-人才库-" + ver + ".json"
    )
    at = str(pub.get("createdAt") or "")
    return {
        "id": pub["id"],
        "attachId": pub["attachId"],
        "source": "app_edit",
        "sourceLabel": "确认写入",
        "sessionId": "",
        "sessionName": "",
        "messageId": "",
        "filename": name,
        "kind": "app_fields",
        "label": "申报书 " + ver,
        "sender": str(pub.get("personName") or ""),
        "personName": str(pub.get("personName") or ""),
        "version": ver,
        "versionN": int(pub.get("versionN") or 0),
        "taskId": str(pub.get("taskId") or ""),
        "url": str(pub.get("url") or ""),
        "time": at,
        "ok": True,
        "status": "uploaded",
        "detail": "",
        "at": at,
        "appName": "",
        "opinions": [],
        "clauses": [],
        "opinionCount": 0,
    }


def _stem(name: str) -> str:
    n = str(name or "")
    i = n.rfind(".")
    return n[:i] if i > 0 else n


def load_task_for_opinions(task_id: str) -> dict | None:
    """从 tasks/{id} 读回 meta，供入库看板挂修改意见。"""
    tid = str(task_id or "").strip()
    if not tid or len(tid) > 80:
        return None
    if any(ch in tid for ch in ("/", "\\", "\x00")) or ".." in tid:
        return None
    from .config import TASKS_DIR
    root = TASKS_DIR / tid
    if not root.is_dir():
        return None
    meta: dict = {}
    mp = root / "meta.json"
    if mp.is_file():
        try:
            loaded = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            loaded = None
        if isinstance(loaded, dict):
            meta = loaded
    meta["dir"] = str(root)
    return meta


def opinions_of_task(task: dict | None, *, snippet: int = 360, clause_limit: int = 16) -> dict:
    """对应任务的来源申报书、修改意见摘录和计划条款。"""
    empty = {"appName": "", "opinions": [], "clauses": []}
    if not isinstance(task, dict):
        return empty
    try:
        snip = max(40, min(int(snippet or 360), 8000))
    except (TypeError, ValueError):
        snip = 360
    try:
        cap = max(0, min(int(clause_limit or 16), 40))
    except (TypeError, ValueError):
        cap = 16
    app = task.get("app") if isinstance(task.get("app"), dict) else {}
    app_name = str(app.get("name") or app.get("inputName") or "").strip()
    root = Path(str(task.get("dir") or ""))
    txt_dir = root / "work" / "txt"
    opinions = []
    for o in task.get("opinions") or []:
        name = str(o.get("name") if isinstance(o, dict) else o or "").strip()
        if not name:
            continue
        text = ""
        p = txt_dir / (_stem(name) + ".txt")
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                text = ""
        opinions.append({
            "name": name,
            "kind": "inline" if name == "申报书标注意见.txt" or task.get("inlineOpinions") else "file",
            "snippet": " ".join(text.split())[:snip],
            "chars": len(text),
        })
    clauses = []
    plan_path = root / "work" / "tmp" / "plan.json"
    if cap and plan_path.is_file():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            plan = None
        edits = plan.get("edits") if isinstance(plan, dict) else None
        seen: set[str] = set()
        for e in edits or []:
            if not isinstance(e, dict):
                continue
            clause = str(e.get("clause") or "").strip()
            op = str(e.get("opinion") or e.get("opinionGemini") or "").strip()
            line = clause or op
            if not line:
                continue
            key = line[:80]
            if key in seen:
                continue
            seen.add(key)
            clauses.append({"clause": clause[:160], "opinion": op[:240]})
            if len(clauses) >= cap:
                break
    return {"appName": app_name, "opinions": opinions, "clauses": clauses}


def attach_task_opinions(item: dict | None, *, snippet: int = 360) -> dict | None:
    if not isinstance(item, dict):
        return item
    packed = opinions_of_task(load_task_for_opinions(item.get("taskId")), snippet=snippet)
    item["appName"] = packed.get("appName") or ""
    item["opinions"] = packed.get("opinions") or []
    item["clauses"] = packed.get("clauses") or []
    item["opinionCount"] = len(item["opinions"])
    return item


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


def load_file_bytes(row: dict, export: dict | None = None) -> tuple[bytes, str, str]:
    """返回 (json_bytes, mime, filename)，供下载。"""
    export = export if isinstance(export, dict) else load_export(row)
    data = json.dumps(export, ensure_ascii=False, indent=2).encode("utf-8")
    aid = re.sub(r"[^A-Za-z0-9_-]", "", str(row.get("attach_id") or "talent")) or "talent"
    ver = str(row.get("version") or "v1")
    name = aid + "-人才库-" + ver + ".json"
    return data, "application/json; charset=utf-8", name


def is_masked_value(s) -> bool:
    t = str(s or "").strip()
    if not t:
        return False
    if t in ("***", "***@***.***"):
        return True
    if "*" not in t:
        return False
    stars = t.count("*")
    if stars >= 3:
        return True
    return stars >= 2 and "@" in t


_PII_ALIAS = {
    "电子邮件": ("电子邮箱",),
    "电子邮箱": ("电子邮件",),
    "手机号": ("手机", "联系电话"),
    "证件号码": ("护照号码",),
}
_MASKED_KEY_TO_FIELD = {
    "id_number_masked": "证件号码",
    "email_masked": "电子邮件",
    "phone_masked": "手机号",
}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CN_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_INTL_PHONE_RE = re.compile(r"(?<!\d)(\+\d{1,3}[-\s]?\d{6,14})")
_BIRTH_RE = re.compile(
    r"(?:出生日期|Date of Birth)[^\d]{0,48}"
    r"((?:19|20)\d{2}[-./年]\s*\d{1,2}[-./月]\s*\d{1,2}|(?:19|20)\d{6})",
    re.I,
)
_PASSPORT_RE = re.compile(
    r"(?:证件号码|护照号码|护照|Passport|Other ID NO\.?|ID NO\.?)[^\nA-Z0-9]{0,48}([A-Z]{1,2}\d{6,9})",
    re.I,
)
_ADDR_RE = re.compile(r"家庭住址\s*[:：]?\s*([^\n]{4,80})")


def mask_fits(masked, plain) -> bool:
    """脱敏串里露出来的字符必须能对上明文，避免串到别人的证件号。"""
    m = str(masked or "").strip()
    p = str(plain or "").strip()
    if not m or not p or is_masked_value(p):
        return False
    if m in ("***", "***@***.***"):
        return True
    parts = [re.escape(x) for x in re.split(r"\*+", m) if x != ""]
    if not parts:
        return True
    try:
        return bool(re.search(".*".join(parts), p, re.I))
    except re.error:
        return False


def _norm_date(s: str) -> str:
    raw = str(s or "").strip()
    if re.fullmatch(r"(?:19|20)\d{6}", raw):
        return raw[:4] + "-" + raw[4:6] + "-" + raw[6:8]
    nums = re.findall(r"\d+", raw)
    if len(nums) >= 3 and len(nums[0]) == 4:
        return nums[0] + "-" + nums[1].zfill(2) + "-" + nums[2].zfill(2)
    return raw


def extract_app_pii(app_text: str) -> dict:
    raw = str(app_text or "")
    out: dict[str, str] = {}
    mails = [e for e in _EMAIL_RE.findall(raw) if not is_masked_value(e)]
    if mails:
        out["电子邮件"] = mails[0]
        out["电子邮箱"] = mails[0]
    m = _CN_MOBILE_RE.search(raw) or _INTL_PHONE_RE.search(raw)
    if m:
        phone = re.sub(r"[\s-]", "", m.group(1))
        if not is_masked_value(phone) and 7 <= len(phone) <= 16:
            out["手机号"] = phone
    m = _BIRTH_RE.search(raw)
    if m:
        out["出生日期"] = _norm_date(m.group(1))
    m = _PASSPORT_RE.search(raw)
    if m:
        out["证件号码"] = m.group(1).upper()
    m = _ADDR_RE.search(raw)
    if m:
        addr = m.group(1).strip(" ：:\t|｜")
        if addr and not is_masked_value(addr) and "填写" not in addr:
            out["家庭住址"] = addr
    return out


def _talent_names(talent: dict | None) -> list[str]:
    t = talent if isinstance(talent, dict) else {}
    payload = t.get("payload") if isinstance(t.get("payload"), dict) else {}
    basic = payload.get("申报人基本信息") if isinstance(payload.get("申报人基本信息"), dict) else {}
    names = []
    for v in (
        t.get("name"),
        basic.get("有效证件姓名"),
        basic.get("外籍专家中文姓名"),
        basic.get("中文（音译）名"),
        basic.get("中文(音译)名"),
    ):
        s = re.sub(r"\s+", " ", str(v or "")).strip()
        if len(s) >= 2:
            names.append(s)
    return names


def _app_has_name(app_text: str, names: list[str]) -> bool:
    blob = str(app_text or "")
    compact = re.sub(r"\s+", "", blob).lower()
    for n in names:
        if n and (n in blob or re.sub(r"\s+", "", n).lower() in compact):
            return True
    return False


def _pick_plain(masked, *cands) -> str:
    cur = str(masked or "")
    for c in cands:
        s = str(c or "").strip()
        if s and not is_masked_value(s) and (not is_masked_value(cur) or mask_fits(cur, s)):
            return s
    return ""


def _unmask_mapping(obj: dict, fills: dict) -> None:
    for k, v in list(obj.items()):
        if not isinstance(v, str) or not is_masked_value(v):
            continue
        cands = [fills.get(k)]
        for alt in _PII_ALIAS.get(k, ()):
            cands.append(obj.get(alt))
            cands.append(fills.get(alt))
        if k in ("电子邮件", "电子邮箱"):
            for ov in obj.values():
                if isinstance(ov, str) and "@" in ov:
                    cands.append(ov)
        hit = _pick_plain(v, *cands)
        if hit:
            obj[k] = hit


def _unmask_walk(value, fills: dict) -> None:
    if isinstance(value, list):
        for item in value:
            _unmask_walk(item, fills)
        return
    if not isinstance(value, dict):
        return
    _unmask_mapping(value, fills)
    for v in value.values():
        if isinstance(v, (dict, list)):
            _unmask_walk(v, fills)


def app_text_for_task(task: dict | None) -> str:
    if not isinstance(task, dict):
        return ""
    root = Path(str(task.get("dir") or ""))
    if not root.is_dir():
        return ""
    final = root / "work" / "tmp" / "_final.txt"
    if final.is_file() and final.stat().st_size > 20:
        try:
            return final.read_text(encoding="utf-8")
        except Exception:
            pass
    txt_dir = root / "work" / "txt"
    if not txt_dir.is_dir():
        return ""
    hits = sorted(txt_dir.glob("*申报书*.txt"), key=lambda p: -p.stat().st_mtime)
    for p in hits:
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            continue
    return ""


def unmask_export(export: dict | None, app_text: str = "") -> dict:
    """后台查看用：用同条明文和申报书正文把 *** 还原。不对得上姓名的申报书不采。"""
    packed = copy.deepcopy(export) if isinstance(export, dict) else {}
    talent = packed.get("talent") if isinstance(packed.get("talent"), dict) else packed
    if not isinstance(talent, dict):
        return packed
    fills: dict[str, str] = {}
    names = _talent_names(talent)
    if app_text and _app_has_name(app_text, names):
        fills.update(extract_app_pii(app_text))
    _unmask_walk(talent, fills)
    payload = talent.get("payload") if isinstance(talent.get("payload"), dict) else {}
    basic = payload.get("申报人基本信息") if isinstance(payload.get("申报人基本信息"), dict) else {}
    for mk, src in _MASKED_KEY_TO_FIELD.items():
        if mk not in talent:
            continue
        hit = _pick_plain(
            talent.get(mk),
            basic.get(src),
            basic.get("电子邮箱") if src == "电子邮件" else "",
            fills.get(src),
        )
        if hit:
            talent[mk] = hit
    return packed


def _walk_plain(value, title: str) -> list[dict]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (str, int, float, bool)):
        text = str(value)
        if not text:
            return []
        return [{"title": title, "text": text, "masked": is_masked_value(text)}]
    if isinstance(value, list):
        out: list[dict] = []
        for i, item in enumerate(value, 1):
            out.extend(_walk_plain(item, (title + " " + str(i)) if title else str(i)))
        return out
    if isinstance(value, dict):
        out: list[dict] = []
        for k, v in value.items():
            if str(k).startswith("_"):
                continue
            out.extend(_walk_plain(v, str(k)))
        return out
    return []


def build_plain_preview(export: dict | None) -> list[dict]:
    """把 8091 talent JSON 摊成可阅读章节，供入库看板预览。"""
    exp = export if isinstance(export, dict) else {}
    talent = exp.get("talent") if isinstance(exp.get("talent"), dict) else exp
    payload = talent.get("payload") if isinstance(talent.get("payload"), dict) else {}
    sections: list[dict] = []
    if talent.get("profile_summary"):
        sections.extend(_walk_plain(talent.get("profile_summary"), "简介"))
    for k, v in payload.items():
        if str(k).startswith("_") or k == "last_application":
            continue
        sections.extend(_walk_plain(v, str(k)))
    last = talent.get("last_application") or payload.get("last_application")
    if isinstance(last, dict) and last:
        sections.extend(_walk_plain(last, "上一版申报"))
    return sections
