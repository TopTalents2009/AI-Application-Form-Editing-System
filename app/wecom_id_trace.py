"""按人才编号汇总：各群痕迹、同一文件、交付记录、各版修改意见。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import matcher as M
from .agent_extract import is_delivery_name
from .config import DATA_DIR
from .wecom_ai_bind import _file_label, _pick_attach_id, _UNKNOWN_ATTACH
from .wecom_file_class import TYPE_LABEL, _SUMMARY_DIR

_MAX_HITS = 160
_MAX_SESSIONS = 40
_MAX_FILE_BYTES = 24 * 1024 * 1024


def normalize_attach_id(q: str) -> str:
    raw = str(q or "").strip()
    if not raw:
        raise ValueError("请输入人才编号")
    nums = M.extract_book_nums(raw)
    if nums:
        return str(nums[0])
    digits = re.sub(r"\D", "", raw)
    if re.fullmatch(r"\d{4,6}", digits) and M.is_book_num(digits):
        return digits
    raise ValueError("请输入 4～6 位人才编号")


def _aid_match(aid: str, filename: str = "", text: str = "") -> bool:
    blob = " ".join(x for x in (filename, text) if x)
    if not blob:
        return False
    picked = _pick_attach_id([], text, filename)
    if picked == aid:
        return True
    if picked != _UNKNOWN_ATTACH:
        return False
    return bool(re.search(r"(^|[^0-9])" + re.escape(aid) + r"([^0-9]|$)", blob))


def _file_key(name: str) -> str:
    s = _file_label(name).lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\.[a-z0-9]{1,8}$", "", s)
    return s or str(name or "").lower()


def _slim_file(row: dict) -> dict:
    return {
        "filename": str(row.get("filename") or ""),
        "kind": str(row.get("kind") or row.get("type") or ""),
        "label": str(row.get("label") or TYPE_LABEL.get(row.get("kind") or "", "") or row.get("kind") or ""),
        "sessionId": str(row.get("sessionId") or ""),
        "sessionName": str(row.get("sessionName") or ""),
        "time": str(row.get("time") or row.get("at") or ""),
        "sender": str(row.get("sender") or ""),
        "messageId": str(row.get("messageId") or row.get("message_id") or ""),
        "isDelivery": bool(row.get("isDelivery")),
        "source": str(row.get("source") or ""),
    }


def _is_aid(raw: str) -> bool:
    s = str(raw or "").strip()
    return bool(re.fullmatch(r"\d{4,6}", s) and M.is_book_num(s))


def _aid_of(filename: str = "", text: str = "") -> str:
    picked = _pick_attach_id([], text, filename)
    if picked and picked != _UNKNOWN_ATTACH and _is_aid(picked):
        return picked
    return ""


def _extract_aids(filename: str = "", text: str = "") -> list[str]:
    picked = _aid_of(filename, text)
    out = []
    if picked:
        out.append(picked)
    blob = " ".join(x for x in (filename, text) if x)
    for n in M.extract_book_nums(blob):
        if _is_aid(n) and n not in out:
            out.append(n)
    return out[:4]


def _person_from_filename(fname: str) -> str:
    s = _file_label(fname)
    s = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", s)
    s = re.sub(r"\d{4,6}", " ", s)
    s = re.sub(r"[_\-]+", " ", s).strip()
    full, toks = M.accept_person_name(s)
    person = ""
    if toks:
        cn = next((t for t in toks if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t)), "")
        person = cn or (str(full)[:40] if full else "")
    if not person:
        m = re.search(r"[\u4e00-\u9fa5]{2,4}", s)
        person = m.group(0) if m else ""
    if not person or person in M.NAME_STOP:
        return ""
    if person in {"企业微信", "文件传输助手", "微信团队"}:
        return ""
    if person.endswith(("市", "区", "县")) or "公司" in person:
        return ""
    return person


def _is_watched_row(sid: str = "", sname: str = "") -> bool:
    from .wecom_groups import group_is_watched
    return group_is_watched(sname, sid)


def _watch_list() -> list:
    from .wecom_groups import list_watch_groups
    return list_watch_groups()


def _watched_sessions() -> list[dict]:
    """本地同步里、且在关注名单中的会话。优先交付群。"""
    from .wecom_groups import group_is_watched
    from . import wecom_local as L

    if not _watch_list() or not L.data_root():
        return []
    out = []
    seen = set()
    for src in L.list_sources():
        src_id = str(src.get("id") or "")
        for sess in L.list_sessions(src_id, 0):
            uid = str(sess.get("username") or sess.get("session_id") or "")
            sname = str(sess.get("display_name") or uid)
            if not group_is_watched(sname, uid):
                continue
            key = (src_id, uid or sname)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "sourceId": src_id,
                "sessionId": uid,
                "sessionName": sname,
                "path": L._msg_path(src_id, uid),
            })
    out.sort(key=lambda w: 0 if is_delivery_name(str(w.get("sessionName") or "")) else 1)
    return out


def _file_kind_of(fname: str, sname: str = "", text: str = "") -> tuple[str, str]:
    from .wecom_file_class import classify_name_with_context

    if not fname:
        return "chat", "群聊痕迹"
    hit = classify_name_with_context(fname, {
        "caption": sname,
        "blob": " ".join(x for x in (sname, fname, text[:240]) if x),
        "before": [sname] if sname else [],
        "after": [],
    })
    return str(hit.get("type") or "file"), str(hit.get("label") or "群文件")


def _looks_opinion(kind: str = "", fname: str = "", text: str = "") -> bool:
    from .wecom_file_class import _STRONG_OPINION_NAME
    from .wecom_cases import is_opinion_text

    if str(kind or "") == "opinion":
        return True
    if fname and _STRONG_OPINION_NAME.search(str(fname)):
        return True
    if text and not fname and is_opinion_text(text):
        return True
    return False


def _keep_summary_file(aid: str, fname: str, kind: str, session_has_aid: bool) -> bool:
    """会话里已出现该编号时，无编号的修改意见文件也留下。"""
    if not aid:
        return bool(_extract_aids(fname))
    if _aid_match(aid, fname):
        return True
    return bool(session_has_aid and _looks_opinion(kind, fname))


def _sess_token(sid: str = "", sname: str = "") -> tuple[str, str]:
    return (str(sid or "").strip(), str(sname or "").strip())


def _from_summaries(aid: str = "", *, watched_only: bool = True) -> list[dict]:
    root = Path(_SUMMARY_DIR)
    if not root.is_dir():
        return []
    cap = _MAX_HITS if aid else 2000
    out = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            continue
        sid = str(data.get("session_id") or result.get("session_id") or "")
        sname = str(result.get("session_name") or result.get("sessionName") or "")
        if watched_only and not _is_watched_row(sid, sname):
            continue
        delivery = is_delivery_name(sname)
        packed = []
        session_has_aid = False
        for g in result.get("groups") or []:
            if not isinstance(g, dict):
                continue
            kind = str(g.get("id") or "")
            label = str(g.get("label") or TYPE_LABEL.get(kind, kind))
            for f in g.get("files") or []:
                if not isinstance(f, dict):
                    continue
                fname = str(f.get("filename") or "")
                fsid = str(f.get("sessionId") or sid)
                fsname = str(f.get("sessionName") or sname)
                if watched_only and not _is_watched_row(fsid, fsname):
                    continue
                if aid and _aid_match(aid, fname):
                    session_has_aid = True
                packed.append((fname, kind, label, fsid, fsname, f))
        for fname, kind, label, fsid, fsname, f in packed:
            if aid:
                if not _keep_summary_file(aid, fname, kind, session_has_aid):
                    continue
                aids = [aid]
            else:
                aids = _extract_aids(fname)
                if not aids:
                    continue
            person = _person_from_filename(fname)
            for got in aids:
                row = _slim_file({
                    "filename": fname,
                    "kind": kind,
                    "label": label,
                    "sessionId": fsid,
                    "sessionName": fsname,
                    "time": f.get("time") or "",
                    "sender": f.get("sender") or "",
                    "messageId": f.get("messageId") or "",
                    "isDelivery": delivery or is_delivery_name(fsname),
                    "source": "file_summary",
                })
                row["attachId"] = got
                if person:
                    row["personName"] = person
                out.append(row)
                if len(out) >= cap:
                    return out
    return out


def _from_synced(aid: str = "", *, watched_only: bool = True) -> list[dict]:
    from . import wecom_local as L
    from .party import sender_of

    if not L.data_root():
        return []
    jobs: list[tuple[int, str, str, Path]] = []
    if watched_only:
        for w in _watched_sessions():
            path = w.get("path")
            if path is None:
                path = L._msg_path(str(w.get("sourceId") or ""), str(w.get("sessionId") or ""))
            if path is None:
                continue
            uid = str(w.get("sessionId") or "")
            sname = str(w.get("sessionName") or uid)
            pri = 0 if (aid and aid in (sname + " " + uid)) else (0 if is_delivery_name(sname) else 1)
            jobs.append((pri, uid, sname, path))
    else:
        for src in L.list_sources():
            src_id = str(src.get("id") or "")
            for sess in L.list_sessions(src_id, 0):
                uid = str(sess.get("username") or sess.get("session_id") or "")
                sname = str(sess.get("display_name") or uid)
                path = L._msg_path(src_id, uid)
                if path is None:
                    continue
                pri = 3
                blob = sname + " " + uid
                if aid and aid in blob:
                    pri = 0
                elif is_delivery_name(sname):
                    pri = 1
                elif uid[:1] in ("R", "S"):
                    pri = 2
                jobs.append((pri, uid, sname, path))
    jobs.sort(key=lambda x: x[0])
    needle = aid.encode("utf-8") if aid else None
    cap = _MAX_HITS if aid else 2000
    out = []
    seen = set()
    deadline = time.monotonic() + 22
    for _pri, sess_id, sname, path in jobs:
        if time.monotonic() > deadline:
            break
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if needle and needle not in raw:
            continue
        try:
            rows = json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        delivery = is_delivery_name(sname)
        for m in rows:
            if not isinstance(m, dict):
                continue
            fname = str(m.get("attachment_name") or "")
            text = str(m.get("text") or m.get("snippet") or "")
            if aid:
                if not _aid_match(aid, fname, text):
                    continue
                aids = [aid]
            else:
                aids = _extract_aids(fname, text)
                if not aids:
                    continue
            mid = str(m.get("message_id") or "")
            kind, label = _file_kind_of(fname, sname, text)
            person = _person_from_filename(fname)
            display = fname or (text[:40] + ("…" if len(text) > 40 else ""))
            for got in aids:
                key = (got, sess_id, mid or fname or text[:40])
                if key in seen:
                    continue
                seen.add(key)
                row = _slim_file({
                    "filename": display,
                    "kind": kind,
                    "label": label,
                    "sessionId": sess_id,
                    "sessionName": sname,
                    "time": m.get("time_text") or "",
                    "sender": sender_of(m),
                    "messageId": mid,
                    "isDelivery": delivery,
                    "source": "chat",
                })
                row["attachId"] = got
                if person:
                    row["personName"] = person
                out.append(row)
                if len(out) >= cap:
                    return out
    return out


def _from_ai_reads(aid: str = "", *, watched_only: bool = True) -> list[dict]:
    from .wecom_ai_store import _INDEX_PATH, _STORE_DIR
    from .wecom_cases import classify_file

    out = []
    if not _INDEX_PATH.is_file():
        return out
    try:
        index = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(index, list):
        return out
    cap = 40 if aid else 800
    for meta in index[:80]:
        if not isinstance(meta, dict):
            continue
        path = _STORE_DIR / (str(meta.get("id") or "") + ".json")
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        binds = data.get("bindPrompts") if isinstance(data.get("bindPrompts"), dict) else {}
        pack = binds.get("time_window") or next(iter(binds.values()), None) if binds else None
        if not isinstance(pack, dict):
            continue
        sname = str(data.get("session_name") or meta.get("session_name") or "")
        sid = str(data.get("session_id") or meta.get("session_id") or "")
        if watched_only and not _is_watched_row(sid, sname):
            continue
        for talent in pack.get("talents") or []:
            if not isinstance(talent, dict):
                continue
            tid = str(talent.get("attachId") or "")
            if aid and tid != aid:
                continue
            if not _is_aid(tid):
                continue
            tperson = str(talent.get("person") or "")
            for ver in talent.get("versions") or []:
                if not isinstance(ver, dict):
                    continue
                fname = str(ver.get("filename") or "")
                if fname and classify_file(fname) != "app":
                    continue
                person = tperson or _person_from_filename(fname)
                out.append({
                    "attachId": tid,
                    "chatVersion": str(ver.get("chatVersion") or ""),
                    "filename": fname,
                    "time": str(ver.get("time") or ""),
                    "sender": str(ver.get("sender") or ""),
                    "sessionId": sid,
                    "sessionName": sname,
                    "isDelivery": is_delivery_name(sname),
                    "opinions": list(ver.get("opinions") or [])[:24],
                    "libraryVersion": ver.get("libraryVersion") or None,
                    "personName": person,
                })
                if len(out) >= cap:
                    return out
    return out


def _from_summaries_all() -> list[dict]:
    return _from_summaries("")


def _from_tasks_all() -> list[dict]:
    try:
        from .main import runner
        rows = runner.list_meta()
    except Exception:
        rows = []
    out = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        app = t.get("app") if isinstance(t.get("app"), dict) else {}
        aid = str(t.get("attachId") or app.get("attachId") or app.get("no") or "")
        if not _is_aid(aid):
            continue
        out.append({
            "attachId": aid,
            "id": t.get("id"),
            "status": t.get("status") or "",
            "createdAt": t.get("createdAt") or "",
            "personName": t.get("personName") or app.get("personName") or "",
            "filename": (app.get("name") or app.get("inputName") or ""),
            "sessionName": t.get("wecomSession") or "",
        })
        if len(out) >= 400:
            break
    return out


def _from_tasks(aid: str) -> list[dict]:
    try:
        from .main import runner
        rows = runner.list_meta()
    except Exception:
        rows = []
    out = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        app = t.get("app") if isinstance(t.get("app"), dict) else {}
        got = str(t.get("attachId") or app.get("attachId") or app.get("no") or "")
        if got != aid:
            continue
        out.append({
            "id": t.get("id"),
            "status": t.get("status") or "",
            "createdAt": t.get("createdAt") or "",
            "personName": t.get("personName") or app.get("personName") or "",
            "filename": (app.get("name") or app.get("inputName") or ""),
            "sessionName": t.get("wecomSession") or "",
            "source": t.get("source") or "",
            "version": ((t.get("talentAppFile") or {}) if isinstance(t.get("talentAppFile"), dict) else {}).get("version") or "",
        })
        if len(out) >= 40:
            break
    return out


def _from_versions(aid: str) -> list[dict]:
    from . import talent_app_files as T

    rows = T.list_by_attach(aid, limit=40)
    out = []
    for raw in rows:
        item = T.as_ingest_item(raw)
        if not item:
            continue
        T.attach_task_opinions(item)
        out.append({
            "id": item.get("id"),
            "version": item.get("version") or "",
            "versionN": item.get("versionN") or 0,
            "filename": item.get("filename") or "",
            "taskId": item.get("taskId") or "",
            "at": item.get("at") or "",
            "personName": item.get("personName") or "",
            "url": item.get("url") or "",
            "appName": item.get("appName") or "",
            "opinions": item.get("opinions") or [],
            "clauses": item.get("clauses") or [],
        })
    return out


def _from_ingest(aid: str) -> list[dict]:
    from . import wecom_talent_db as D

    try:
        board = D.list_board(q=aid, source="", ok_only=False, limit=800)
    except Exception:
        return []
    out = []
    for g in board.get("groups") or []:
        if str(g.get("attachId") or g.get("title") or "") != aid:
            continue
        for f in g.get("files") or []:
            if not isinstance(f, dict):
                continue
            if f.get("source") == "app_edit":
                continue
            out.append(_slim_file({
                "filename": f.get("filename") or "",
                "kind": f.get("kind") or "",
                "label": f.get("label") or "",
                "sessionId": f.get("sessionId") or "",
                "sessionName": f.get("sessionName") or "",
                "time": f.get("time") or f.get("at") or "",
                "sender": f.get("sender") or "",
                "isDelivery": is_delivery_name(str(f.get("sessionName") or "")),
                "source": f.get("source") or "ingest",
            }))
    return out


def _from_local(aid: str) -> list[dict]:
    from .attachments import list_local_ingest_items

    out = []
    for f in list_local_ingest_items(q=aid, limit=400):
        if str(f.get("attachId") or "") != aid:
            continue
        out.append(_slim_file({
            "filename": f.get("filename") or "",
            "kind": f.get("kind") or "",
            "label": f.get("label") or "本地附件",
            "time": f.get("at") or "",
            "source": "local",
        }))
    return out


def _merge_sessions(files: list[dict], chats: list[dict]) -> list[dict]:
    bag: dict[str, dict] = {}
    for row in list(files) + list(chats):
        sid = str(row.get("sessionId") or "").strip()
        name = str(row.get("sessionName") or "").strip()
        if not sid and not name:
            continue
        key = sid or ("name:" + name)
        cur = bag.get(key) or {
            "sessionId": sid,
            "sessionName": name,
            "isDelivery": False,
            "hitCount": 0,
            "lastTime": "",
            "sources": [],
        }
        cur["hitCount"] += 1
        if name and not cur["sessionName"]:
            cur["sessionName"] = name
        if row.get("isDelivery"):
            cur["isDelivery"] = True
        elif is_delivery_name(name):
            cur["isDelivery"] = True
        t = str(row.get("time") or "")
        if t > str(cur.get("lastTime") or ""):
            cur["lastTime"] = t
        src = str(row.get("source") or "")
        if src and src not in cur["sources"]:
            cur["sources"].append(src)
        bag[key] = cur
    rows = list(bag.values())
    rows.sort(key=lambda x: str(x.get("lastTime") or ""), reverse=True)
    rows.sort(key=lambda x: 0 if x.get("isDelivery") else 1)
    return rows[:_MAX_SESSIONS]


def _merge_same_files(rows: list[dict]) -> list[dict]:
    bag: dict[str, dict] = {}
    for row in rows:
        fname = str(row.get("filename") or "").strip()
        if not fname:
            continue
        key = _file_key(fname)
        cur = bag.get(key) or {
            "filename": fname,
            "label": row.get("label") or "",
            "kind": row.get("kind") or "",
            "copies": [],
        }
        copy = {
            "sessionId": row.get("sessionId") or "",
            "sessionName": row.get("sessionName") or "",
            "time": row.get("time") or "",
            "sender": row.get("sender") or "",
            "source": row.get("source") or "",
            "isDelivery": bool(row.get("isDelivery")),
            "kind": row.get("kind") or "",
            "label": row.get("label") or "",
        }
        if copy not in cur["copies"]:
            cur["copies"].append(copy)
        if not cur.get("label") and row.get("label"):
            cur["label"] = row.get("label")
        bag[key] = cur
    out = list(bag.values())
    out.sort(key=lambda x: (-len(x.get("copies") or []), str(x.get("filename") or "")))
    return out[:80]


def _session_msg_jobs(session_id: str = "", session_name: str = "") -> list[tuple[str, str, Path]]:
    sid = str(session_id or "").strip()
    sname = str(session_name or "").strip()
    if not sid and not sname:
        return []
    best: dict[str, tuple[int, str, str, Path]] = {}
    for w in _watched_sessions():
        wid = str(w.get("sessionId") or "").strip()
        wname = str(w.get("sessionName") or "").strip()
        if sid and wid != sid:
            if not (sname and wname == sname):
                continue
        elif not sid and sname and wname != sname:
            continue
        path = w.get("path")
        if path is None:
            from . import wecom_local as L
            path = L._msg_path(str(w.get("sourceId") or ""), wid)
        if path is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        key = wid or sid or wname or sname
        cur = best.get(key)
        if cur is None or size > cur[0]:
            best[key] = (size, wid or sid, wname or sname, path)
    return [(sid_, name_, path) for _sz, sid_, name_, path in best.values()]


def _bind_one_session(
    aid: str,
    sess_id: str,
    sname: str,
    path: Path,
    seed_versions: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """从该群聊天原文抽出申报书版本，并用时间窗挂上修改意见。"""
    from .wecom_ai_bind import (
        PROMPT_TIME_WINDOW,
        _bind_result_for_prompt,
        build_talent_versions,
        extract_catalog,
    )
    from .wecom_cases import classify_file, is_opinion_text
    from .wecom_file_class import (
        _EXPLICIT_APP,
        _STRONG_OPINION_NAME,
        apply_type_cache,
        can_be_app_file,
        list_file_entries,
        make_cluster_kind_of,
    )
    from .party import sender_of

    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return [], []
        raw = path.read_bytes()
    except OSError:
        return [], []
    try:
        rows = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        return [], []
    if not isinstance(rows, list):
        return [], []

    clustered = make_cluster_kind_of(apply_type_cache(sess_id, list_file_entries(rows)))

    def kind_of(m: dict) -> str:
        fname = str(m.get("attachment_name") or m.get("filename") or "")
        text = str(m.get("text") or m.get("snippet") or "")
        k = clustered(m)
        if k in ("app", "opinion", "text"):
            return k
        if fname:
            if _STRONG_OPINION_NAME.search(fname) or _looks_opinion("file", fname, text):
                return "opinion"
            if can_be_app_file(fname) and (
                _EXPLICIT_APP.search(fname) or classify_file(fname, message_id=m.get("message_id")) == "app"
            ):
                return "app"
            fk, _lab = _file_kind_of(fname, sname, text)
            return fk if fk in ("app", "opinion") else "other"
        if is_opinion_text(text):
            return "text"
        return "other"

    cat = extract_catalog(rows, session_id=sess_id, session_name=sname, kind_of=kind_of)
    apps = list(cat.get("apps") or [])
    seen_app = {
        (str(a.get("messageId") or ""), str(a.get("filename") or "").lower())
        for a in apps
    }
    for v in seed_versions or []:
        if not isinstance(v, dict):
            continue
        vsid = str(v.get("sessionId") or "")
        vname = str(v.get("sessionName") or "")
        if vsid and vsid != sess_id and vname != sname:
            continue
        if not vsid and vname and vname != sname:
            continue
        fname = str(v.get("filename") or "")
        if fname and classify_file(fname) != "app" and not _EXPLICIT_APP.search(fname):
            continue
        key = (str(v.get("messageId") or ""), fname.lower())
        if key in seen_app:
            continue
        seen_app.add(key)
        apps.append({
            "role": "app",
            "kind": "file",
            "attachId": aid,
            "filename": fname,
            "time": v.get("time") or "",
            "sender": v.get("sender") or "",
            "messageId": v.get("messageId") or "",
            "sessionId": sess_id,
            "sessionName": sname,
            "nums": [aid],
        })
    talents = build_talent_versions(apps)
    pack = _bind_result_for_prompt(PROMPT_TIME_WINDOW, talents, cat.get("opinions") or [])
    delivery = is_delivery_name(sname)
    versions = []
    files = []
    for t in pack.get("talents") or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("attachId") or "")
        if tid != aid:
            continue
        for ver in t.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            fname = str(ver.get("filename") or "")
            person = _person_from_filename(fname)
            versions.append({
                "attachId": aid,
                "chatVersion": str(ver.get("chatVersion") or ""),
                "filename": fname,
                "time": str(ver.get("time") or ""),
                "sender": str(ver.get("sender") or ""),
                "sessionId": sess_id,
                "sessionName": sname,
                "isDelivery": delivery,
                "opinions": list(ver.get("opinions") or [])[:24],
                "libraryVersion": ver.get("libraryVersion") or None,
                "personName": person,
            })
            if fname:
                row = _slim_file({
                    "filename": fname,
                    "kind": "app",
                    "label": "申报书",
                    "sessionId": sess_id,
                    "sessionName": sname,
                    "time": ver.get("time") or "",
                    "sender": ver.get("sender") or sender_of(ver),
                    "messageId": ver.get("messageId") or "",
                    "isDelivery": delivery,
                    "source": "chat_bind",
                })
                row["attachId"] = aid
                if person:
                    row["personName"] = person
                files.append(row)
            for op in ver.get("opinions") or []:
                if not isinstance(op, dict):
                    continue
                ofn = str(op.get("filename") or "")
                okind = str(op.get("kind") or "file")
                if okind == "text" and not ofn:
                    ofn = (str(op.get("text") or "")[:40] or "文本意见")
                row = _slim_file({
                    "filename": ofn,
                    "kind": "opinion",
                    "label": "修改意见",
                    "sessionId": sess_id,
                    "sessionName": sname,
                    "time": op.get("time") or "",
                    "sender": op.get("sender") or "",
                    "messageId": op.get("messageId") or "",
                    "isDelivery": delivery,
                    "source": "chat_bind",
                })
                row["attachId"] = aid
                files.append(row)
    return versions, files


def _bind_hit_sessions(
    aid: str,
    sessions: list[dict],
    seed_versions: list[dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """只扫已出现该编号的关注会话，把各版申报书和修改意见绑上。"""
    vers: list[dict] = []
    files: list[dict] = []
    seen_path = set()
    deadline = time.monotonic() + 12
    seeds = list(seed_versions or [])
    for sess in sessions or []:
        if time.monotonic() > deadline:
            break
        if not isinstance(sess, dict):
            continue
        sid = str(sess.get("sessionId") or "")
        sname = str(sess.get("sessionName") or "")
        for got_id, got_name, path in _session_msg_jobs(sid, sname):
            if time.monotonic() > deadline:
                break
            key = str(path)
            if key in seen_path:
                continue
            seen_path.add(key)
            try:
                more_v, more_f = _bind_one_session(
                    aid, got_id, got_name, path, seed_versions=seeds,
                )
            except Exception:
                continue
            vers.extend(more_v)
            files.extend(more_f)
            if len(vers) >= 40:
                return vers[:40], files[:_MAX_HITS]
    return vers[:40], files[:_MAX_HITS]


def _merge_chat_versions(ai: list[dict], bound: list[dict]) -> list[dict]:
    """有时间窗绑定结果时补上解读里空着的修改意见。"""
    if not bound:
        return list(ai or [])
    if not ai:
        return list(bound or [])

    def keys_of(v: dict) -> list[tuple]:
        sid = str(v.get("sessionId") or "")
        fn = str(v.get("filename") or "").lower()
        ver = str(v.get("chatVersion") or "")
        return [(sid, fn, ver), (sid, fn, "")]

    bag: dict[tuple, dict] = {}
    for v in bound:
        for k in keys_of(v):
            if k not in bag:
                bag[k] = v
    out = []
    used = set()
    for raw in ai:
        v = dict(raw)
        hit = None
        for k in keys_of(v):
            if k in bag:
                hit = bag[k]
                break
        if hit:
            if not (v.get("opinions") or []) and (hit.get("opinions") or []):
                v["opinions"] = list(hit.get("opinions") or [])
            used.add((str(hit.get("sessionId") or ""), str(hit.get("filename") or "").lower()))
        out.append(v)
        used.add((str(v.get("sessionId") or ""), str(v.get("filename") or "").lower()))
    for v in bound:
        key = (str(v.get("sessionId") or ""), str(v.get("filename") or "").lower())
        if key in used:
            continue
        used.add(key)
        out.append(v)
    return out


def _empty_collect(aid: str, *, lite: bool, note: str, watch_count: int = 0) -> dict:
    return {
        "ok": True,
        "attachId": aid,
        "personName": "",
        "lite": bool(lite),
        "sessionCount": 0,
        "fileCount": 0,
        "versionCount": 0,
        "sessions": [],
        "files": [],
        "deliveries": [],
        "versions": [],
        "chatVersions": [],
        "tasks": [],
        "traces": [],
        "fileOpinions": [],
        "geminiNote": "",
        "watchCount": watch_count,
        "note": note,
    }


def collect(q: str, *, lite: bool = False) -> dict:
    """只从关注会话归纳该编号：文件汇总、同步聊天、AI 解读。不读入库/任务库。"""
    aid = normalize_attach_id(q)
    allow = _watch_list()
    watched = _watched_sessions()
    watch_n = len({(w.get("sessionId") or w.get("sessionName") or "") for w in watched})
    if not allow:
        return _empty_collect(aid, lite=lite, note="还没有关注会话，无法从对话归纳编号", watch_count=0)
    versions = []
    try:
        versions = _from_versions(aid)
    except Exception:
        versions = []
    chat_vers = _from_ai_reads(aid)
    summaries = _from_summaries(aid)
    chats: list[dict] = []
    if not lite:
        try:
            chats = _from_synced(aid)
        except Exception:
            chats = []
    hit_sess = []
    seen_sess = set()
    for row in list(chat_vers) + list(summaries) + list(chats):
        token = _sess_token(str(row.get("sessionId") or ""), str(row.get("sessionName") or ""))
        if not (token[0] or token[1]) or token in seen_sess:
            continue
        seen_sess.add(token)
        hit_sess.append({"sessionId": token[0], "sessionName": token[1]})
    bound_vers: list[dict] = []
    bound_files: list[dict] = []
    try:
        bound_vers, bound_files = _bind_hit_sessions(aid, hit_sess, seed_versions=chat_vers)
    except Exception:
        bound_vers, bound_files = [], []
    chat_vers = _merge_chat_versions(chat_vers, bound_vers)
    files = summaries + bound_files + [x for x in chats if x.get("kind") != "chat"]
    extra_sess = []
    for v in chat_vers:
        extra_sess.append({
            "sessionId": v.get("sessionId") or "",
            "sessionName": v.get("sessionName") or "",
            "isDelivery": v.get("isDelivery"),
            "time": v.get("time") or "",
            "source": "ai_read",
        })
    sessions = _merge_sessions(files, chats + extra_sess)
    same_files = _merge_same_files(files)
    person = ""
    for row in list(versions) + list(summaries) + list(chats) + list(chat_vers) + list(bound_files):
        person = str(row.get("personName") or "")
        if person:
            break
    if not person:
        for row in list(versions) + list(summaries) + list(chats) + list(chat_vers) + list(bound_files):
            person = _person_from_filename(str(row.get("filename") or ""))
            if person:
                break
    deliveries = [s for s in sessions if s.get("isDelivery")]
    note = ""
    if not sessions and not same_files and not chat_vers:
        note = "关注会话里尚未出现该编号"
    return {
        "ok": True,
        "attachId": aid,
        "personName": person,
        "lite": bool(lite),
        "sessionCount": len(sessions),
        "fileCount": len(same_files),
        "versionCount": len(versions) + len(chat_vers),
        "sessions": sessions,
        "files": same_files,
        "deliveries": deliveries[:40],
        "versions": versions,
        "chatVersions": chat_vers[:40],
        "tasks": [],
        "traces": chats[:80],
        "fileOpinions": [],
        "geminiNote": "",
        "watchCount": watch_n,
        "note": note,
    }


def _flatten_trace_files(data: dict) -> list[dict]:
    rows = []
    for f in (data or {}).get("files") or []:
        if not isinstance(f, dict):
            continue
        copies = f.get("copies")
        if not isinstance(copies, list) or not copies:
            copies = [{
                "sessionId": f.get("sessionId") or "",
                "sessionName": f.get("sessionName") or "",
                "time": f.get("time") or "",
                "sender": f.get("sender") or "",
                "source": f.get("source") or "",
                "isDelivery": f.get("isDelivery"),
            }]
            f["copies"] = copies
        for c in copies:
            if not isinstance(c, dict):
                continue
            rows.append({
                "filename": str(f.get("filename") or c.get("filename") or ""),
                "kind": str(c.get("kind") or f.get("kind") or ""),
                "label": str(c.get("label") or f.get("label") or ""),
                "sessionId": str(c.get("sessionId") or f.get("sessionId") or ""),
                "sessionName": str(c.get("sessionName") or f.get("sessionName") or ""),
                "time": str(c.get("time") or f.get("time") or ""),
                "sender": str(c.get("sender") or f.get("sender") or ""),
                "messageId": str(c.get("messageId") or f.get("messageId") or ""),
                "_copy": c,
                "_file": f,
            })
    for t in (data or {}).get("traces") or []:
        if not isinstance(t, dict):
            continue
        fname = str(t.get("filename") or "")
        if not fname or str(t.get("kind") or "") == "chat":
            continue
        rows.append({
            "filename": fname,
            "kind": str(t.get("kind") or ""),
            "label": str(t.get("label") or ""),
            "sessionId": str(t.get("sessionId") or ""),
            "sessionName": str(t.get("sessionName") or ""),
            "time": str(t.get("time") or ""),
            "sender": str(t.get("sender") or ""),
            "messageId": str(t.get("messageId") or ""),
            "_copy": t,
            "_file": t,
        })
    return rows


def _write_kind(row: dict, kid: str, label: str, source: str, reason: str) -> None:
    from .wecom_file_class import TYPE_LABEL

    kid = str(kid or "other")
    lab = str(label or TYPE_LABEL.get(kid, kid) or "其他")
    row["kind"] = kid
    row["label"] = lab
    row["classSource"] = source
    row["classReason"] = reason
    copy = row.get("_copy")
    if isinstance(copy, dict):
        copy["kind"] = kid
        copy["label"] = lab
        copy["classSource"] = source
        copy["classReason"] = reason
    parent = row.get("_file")
    if isinstance(parent, dict) and parent is not copy:
        if not parent.get("kind") or parent.get("kind") in ("file", "chat", "other", ""):
            parent["kind"] = kid
            parent["label"] = lab


async def attach_gemini_kinds(data: dict) -> dict:
    """用规则 + 类型缓存 + Gemini 判断足迹文件是否为修改意见等材料类型。"""
    from .wecom_file_class import (
        TYPE_LABEL,
        _EXPLICIT_APP,
        _STRONG_OPINION_NAME,
        _gemini_overlay,
        cache_key,
        classify_name_with_context,
        is_screenshot_name,
        load_type_cache,
        save_type_cache,
        sanitize_classified_entry,
    )

    data = data if isinstance(data, dict) else {"ok": True}
    rows = _flatten_trace_files(data)
    if not rows:
        data["fileOpinions"] = []
        data["geminiNote"] = ""
        return data
    cache_by_sess: dict[str, dict] = {}
    pending_entries = []
    pending_rows = []
    seen = set()
    for row in rows:
        fname = str(row.get("filename") or "")
        sid = str(row.get("sessionId") or "")
        key = (sid, str(row.get("messageId") or ""), fname.lower())
        if key in seen:
            continue
        seen.add(key)
        sname = str(row.get("sessionName") or "")
        cached = cache_by_sess.get(sid)
        if cached is None:
            cached = load_type_cache(sid) if sid else {}
            cache_by_sess[sid] = cached
        hit = cached.get(cache_key(row.get("messageId"), fname)) if cached else None
        if hit:
            _write_kind(row, hit.get("type") or "other", hit.get("label") or "", hit.get("source") or "cache", str(hit.get("reason") or ""))
            continue
        rule = classify_name_with_context(fname, {
            "caption": sname,
            "blob": (sname + " " + fname).strip(),
            "before": [sname] if sname else [],
            "after": [],
        })
        _write_kind(
            row,
            rule.get("type") or "other",
            rule.get("label") or "",
            rule.get("source") or "filename",
            str(rule.get("reason") or ""),
        )
        if is_screenshot_name(fname):
            continue
        if _STRONG_OPINION_NAME.search(fname) or _EXPLICIT_APP.search(fname):
            continue
        pending_rows.append(row)
        pending_entries.append({
            "id": len(pending_entries) + 1,
            "filename": fname,
            "sender": row.get("sender") or "",
            "time": row.get("time") or "",
            "caption": sname[:200],
            "before": [sname] if sname else [],
            "after": [],
            "type": row.get("kind") or "other",
            "label": row.get("label") or "其他",
            "messageId": row.get("messageId") or "",
        })
    note = ""
    if pending_entries:
        try:
            overlay, note = await _gemini_overlay(pending_entries)
        except Exception as e:
            overlay, note = pending_entries, "文件分类模型失败：" + str(e)[:160]
        save_bag: dict[str, list] = {}
        for i, row in enumerate(pending_rows):
            it = overlay[i] if i < len(overlay) else None
            if not isinstance(it, dict):
                continue
            sanitize_classified_entry(it)
            kid = str(it.get("type") or row.get("kind") or "other")
            _write_kind(
                row,
                kid,
                it.get("label") or TYPE_LABEL.get(kid, "其他"),
                it.get("source") or "gemini",
                str(it.get("reason") or ""),
            )
            sid = str(row.get("sessionId") or "")
            if sid:
                save_bag.setdefault(sid, []).append({
                    "messageId": row.get("messageId") or "",
                    "filename": row.get("filename") or "",
                    "type": kid,
                    "label": row.get("label") or "",
                    "reason": row.get("classReason") or "",
                    "source": row.get("classSource") or "gemini",
                })
        for sid, items in save_bag.items():
            try:
                save_type_cache(sid, items)
            except Exception:
                pass
    opinions = []
    seen_op = set()
    for row in rows:
        if str(row.get("kind") or "") != "opinion":
            continue
        op_key = (row.get("sessionId") or "", row.get("messageId") or "", str(row.get("filename") or "").lower())
        if op_key in seen_op:
            continue
        seen_op.add(op_key)
        opinions.append({
            "kind": "file",
            "filename": row.get("filename") or "修改意见",
            "text": row.get("classReason") or "",
            "sender": row.get("sender") or "",
            "time": row.get("time") or "",
            "sessionId": row.get("sessionId") or "",
            "sessionName": row.get("sessionName") or "",
            "classSource": row.get("classSource") or "",
        })
    data["fileOpinions"] = opinions[:80]
    data["geminiNote"] = note or ("已识别修改意见 " + str(len(opinions)) + " 个文件" if opinions else "")
    return data


def overview(*, q: str = "", limit: int = 2000) -> dict:
    """查阅全部关注会话，从聊天/文件/AI 解读归纳编号。不读入库/任务库。"""
    qn = str(q or "").strip()
    try:
        cap = max(1, min(int(limit or 2000), 3000))
    except (TypeError, ValueError):
        cap = 2000
    allow = _watch_list()
    watched = _watched_sessions()
    watch_n = len({(w.get("sessionId") or w.get("sessionName") or "") for w in watched})
    if not allow:
        return {
            "ok": True,
            "overview": True,
            "count": 0,
            "items": [],
            "watchCount": 0,
            "note": "还没有关注会话，无法从对话归纳编号",
        }
    bag: dict[str, dict] = {}

    def slot(aid: str) -> dict | None:
        if not _is_aid(aid):
            return None
        cur = bag.get(aid)
        if not cur:
            cur = {
                "attachId": aid,
                "personName": "",
                "sessionCount": 0,
                "fileCount": 0,
                "versionCount": 0,
                "opinionCount": 0,
                "deliveryCount": 0,
                "taskCount": 0,
                "lastTime": "",
                "sessions": [],
                "_sess": set(),
                "_files": set(),
                "_deliv": set(),
                "_names": [],
            }
            bag[aid] = cur
        return cur

    def touch(cur: dict, *, filename: str = "", session_id: str = "", session_name: str = "",
              time: str = "", person: str = "", version: bool = False, opinions: int = 0,
              count_file: bool = True):
        if person and not cur["personName"]:
            cur["personName"] = person
        fname = str(filename or "").strip()
        if count_file and fname:
            cur["_files"].add(_file_key(fname))
        sid = str(session_id or "").strip()
        sname = str(session_name or "").strip()
        if sid or sname:
            key = sid or ("name:" + sname)
            if key not in cur["_sess"]:
                cur["_sess"].add(key)
                if sname:
                    cur["_names"].append(sname)
            if is_delivery_name(sname):
                cur["_deliv"].add(key)
        t = str(time or "")
        if t > str(cur.get("lastTime") or ""):
            cur["lastTime"] = t
        if version:
            cur["versionCount"] += 1
        if opinions:
            cur["opinionCount"] += int(opinions)

    def ingest_row(row: dict, *, version: bool = False, opinions: int = 0, count_file: bool = True):
        cur = slot(str(row.get("attachId") or ""))
        if not cur:
            return
        fname = str(row.get("filename") or "")
        person = str(row.get("personName") or "") or _person_from_filename(fname)
        kind = str(row.get("kind") or "")
        ops = opinions
        is_ver = version or kind == "app"
        if not ops and kind == "opinion":
            ops = 1
        touch(
            cur,
            filename=fname,
            session_id=str(row.get("sessionId") or ""),
            session_name=str(row.get("sessionName") or ""),
            time=str(row.get("time") or ""),
            person=person,
            version=is_ver,
            opinions=ops,
            count_file=count_file and kind != "chat",
        )

    for row in _from_summaries(""):
        ingest_row(row)
    for row in _from_ai_reads(""):
        ingest_row(row, version=True, opinions=len(row.get("opinions") or []), count_file=bool(row.get("filename")))
    try:
        chats = _from_synced("")
    except Exception:
        chats = []
    for row in chats:
        ingest_row(row, count_file=str(row.get("kind") or "") != "chat")

    seen_watch = {(w.get("sessionId") or w.get("sessionName") or "") for w in watched}
    for cur in bag.values():
        for n in cur.get("_names") or []:
            if n:
                seen_watch.add(n)
        for sid in cur.get("_sess") or []:
            if sid and not str(sid).startswith("name:"):
                seen_watch.add(sid)
    watch_n = max(watch_n, len({x for x in seen_watch if x}))

    items = []
    for aid, cur in bag.items():
        if qn and qn not in aid and qn not in str(cur.get("personName") or ""):
            continue
        names = []
        seen_n = set()
        for n in cur.pop("_names"):
            if n in seen_n:
                continue
            seen_n.add(n)
            names.append(n)
        cur["sessions"] = names[:8]
        cur["sessionCount"] = len(cur.pop("_sess"))
        cur["fileCount"] = len(cur.pop("_files"))
        cur["deliveryCount"] = len(cur.pop("_deliv"))
        items.append(cur)
    items.sort(key=lambda x: (str(x.get("lastTime") or ""), x.get("attachId") or ""), reverse=True)
    note = ""
    if not items:
        note = "关注会话里尚未归纳到人才编号"
    return {
        "ok": True,
        "overview": True,
        "count": len(items),
        "items": items[:cap],
        "watchCount": watch_n,
        "note": note,
    }
