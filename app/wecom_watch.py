"""聊天值班：聊天记录同步后做一次增量扫描。Gemini 认出修改意见后，再配申报书并提交到待确认。"""
from __future__ import annotations
import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from .config import DATA_DIR, atomic_replace, load_config, resolve_watch_llm
from . import wecom_client as W
from .party import sender_of
from .wecom_cases import annotate_existing, cluster_messages, _has_file, _msg_kind
from .wecom_locate import parse_time
from .wecom_split_llm import assist_split_with_gemini
from .wecom_watch_llm import detect_opinions, rule_opinion_ids

_STATE_PATH = DATA_DIR / "data" / "wecom_watch.json"
_lock = asyncio.Lock()
_running = False
_fail_streak = 0
_status = {
    "running": False,
    "lastRunAt": "",
    "lastError": "",
    "lastNote": "",
    "events": [],
    "pending": [],
    "groups": {},
}


def _note_watch_fault(detail: str) -> None:
    """连续两次值班/解析故障才告警，避免偶发超时刷屏。"""
    global _fail_streak
    _fail_streak += 1
    _status["lastError"] = str(detail or "")[:200]
    if _fail_streak < 2:
        return
    try:
        from . import alert as AL
        AL.ops_fault(kind="wecom_watch", title="企微值班异常", lines=[str(detail or "")[:200]])
    except Exception:
        pass


def _clear_watch_fault() -> None:
    global _fail_streak
    _fail_streak = 0


def _watch_cfg() -> dict:
    w = ((load_config().get("wecomChat") or {}).get("watch")) or {}
    return w if isinstance(w, dict) else {}


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


async def _context_kind_of(messages: list, session_id: str):
    """识别到文件后，用前后聊天（规则 + 模型）确认是申报书、修改意见还是其它材料。"""
    from .wecom_file_class import classify_session_files, make_cluster_kind_of
    empty = []
    if not any(isinstance(m, dict) and _has_file(m) for m in (messages or [])):
        return _msg_kind, "", empty
    try:
        got = await classify_session_files(messages, session_id=session_id, keep_entries=True)
        note = str(got.get("geminiNote") or "")
        entries = list(got.get("entries") or [])
        return make_cluster_kind_of(entries), note, entries
    except Exception as e:
        return _msg_kind, "文件分类失败：" + str(e)[:160], empty


async def _upload_fresh_talent_files(
    *,
    entries: list,
    fresh: list,
    session_id: str,
    session_name: str,
    cfg: dict,
    state: dict,
) -> dict:
    """值班扫到的新材料按人才编号入库。失败不影响拆解。"""
    empty = {"uploaded": 0, "failed": 0, "noId": 0, "note": ""}
    if not cfg.get("talentUploadEnabled"):
        return empty
    from .wecom_talent_upload import auto_upload_files, select_fresh_files, upload_configured
    if not upload_configured():
        return {**empty, "note": "未配置人才库上传接口"}
    fresh_ids = [m.get("message_id") for m in (fresh or []) if isinstance(m, dict)]
    files = select_fresh_files(entries, fresh_ids)
    if not files:
        return empty
    for f in files:
        f["sessionId"] = session_id
    try:
        max_n = max(1, min(int(cfg.get("talentUploadMax") or 10), 50))
    except (TypeError, ValueError):
        max_n = 10
    try:
        for f in files:
            if not f.get("sessionName"):
                f["sessionName"] = session_name
        _rows, summary = await auto_upload_files(files, session_id=session_id, max_n=max_n, source="watch")
    except Exception as e:
        note = "材料入库失败：" + str(e)[:120]
        _push_event(state, session_name + " " + note, kind="error")
        return {**empty, "failed": 1, "note": note}
    note = str(summary.get("note") or "")
    uploaded = int(summary.get("uploaded") or 0)
    failed = int(summary.get("failed") or 0)
    if uploaded:
        _push_event(state, session_name + " " + (note or ("材料入库 " + str(uploaded))), kind="ok")
    elif failed:
        _push_event(state, session_name + " " + (note or "材料入库失败"), kind="error")
    return {
        "uploaded": uploaded,
        "failed": failed,
        "noId": int(summary.get("noId") or 0),
        "note": note,
    }


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"groups": {}, "events": [], "pending": []}
    try:
        obj = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {"groups": {}, "events": [], "pending": []}
    except Exception:
        return {"groups": {}, "events": [], "pending": []}


def _save_state(state: dict):
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, _STATE_PATH)


def _push_event(state: dict, text: str, *, kind: str = "info"):
    row = {"at": _now(), "kind": kind, "text": str(text or "")[:240]}
    ev = list(state.get("events") or [])
    ev.insert(0, row)
    state["events"] = ev[:40]
    _status["events"] = state["events"]
    _status["lastNote"] = row["text"]
    if kind == "error":
        _status["lastError"] = row["text"]
    print("[wecom-watch] " + row["text"], flush=True)


def _msg_cursor(m: dict) -> tuple:
    dt = parse_time((m or {}).get("time_text") or (m or {}).get("time"))
    try:
        mid = int((m or {}).get("message_id") or 0)
    except (TypeError, ValueError):
        mid = 0
    stamp = dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
    return (stamp, mid)


def _cursor_of(row: dict) -> tuple:
    return (str(row.get("cursorTime") or ""), int(row.get("cursorId") or 0))


def _watch_model() -> tuple[bool, str]:
    try:
        prof = resolve_watch_llm()
    except ValueError:
        return False, ""
    return True, str(prof.get("id") or prof.get("model") or "")


def public_status() -> dict:
    cfg = _watch_cfg()
    groups = load_config().get("wecomChatGroups") or []
    st = _load_state()
    pending = _public_pending(st.get("pending") or _status.get("pending") or [])
    events = list(st.get("events") or _status.get("events") or [])[:12]
    ready, model = _watch_model()
    return {
        "ok": True,
        "enabled": bool(cfg.get("enabled")),
        "mode": str(cfg.get("mode") or "off"),
        "engine": "gemini",
        "model": model,
        "modelReady": ready,
        "intervalSec": int(cfg.get("intervalSec") or 120),
        "triggerMode": "sync",
        "waitingSync": bool(_status.get("waitingSync")),
        "windowHours": int(cfg.get("windowHours") or 48),
        "owner": str(cfg.get("owner") or ""),
        "groups": list(groups),
        "groupCount": len(groups),
        "needGroups": not bool(groups),
        "running": bool(_status.get("running") or _running),
        "lastRunAt": _status.get("lastRunAt") or st.get("lastRunAt") or "",
        "lastError": _status.get("lastError") or st.get("lastError") or "",
        "lastNote": _status.get("lastNote") or "",
        "pending": pending,
        "events": events,
        "talentUploadEnabled": bool(cfg.get("talentUploadEnabled", True)),
        "talentUploadMax": int(cfg.get("talentUploadMax") or 10),
    }


def start_watch(loop: asyncio.AbstractEventLoop):
    loop.create_task(_loop())


async def _loop():
    """只检查聊天是否有新同步；有变化才增量扫描，不再按固定间隔扫一遍。"""
    await asyncio.sleep(8)
    while True:
        cfg = _watch_cfg()
        try:
            if cfg.get("enabled") and str(cfg.get("mode") or "") != "off":
                await _scan_if_synced()
            else:
                _status["waitingSync"] = False
        except Exception as e:
            print("[wecom-watch] 循环异常：" + str(e)[:200], flush=True)
            _note_watch_fault(str(e))
        await asyncio.sleep(15)


def _sync_mark(items: list) -> str:
    """关注会话的同步水位：同步时间、最后消息时间、条数变了才算有新聊天记录。"""
    parts = []
    for g in items or []:
        if not isinstance(g, dict):
            continue
        sid = str(g.get("username") or g.get("session_id") or "").strip()
        if not sid:
            continue
        reps = []
        for r in g.get("replicas") or []:
            if not isinstance(r, dict):
                continue
            reps.append("|".join([
                str(r.get("source_id") or ""),
                str(r.get("synced_at") or ""),
                str(r.get("last_time") or ""),
                str(r.get("msg_count") or ""),
            ]))
        parts.append("|".join([
            sid,
            str(g.get("last_time") or ""),
            str(g.get("msg_count") or 0),
            ",".join(sorted(reps)),
        ]))
    return "\n".join(sorted(parts))


async def _scan_if_synced():
    if _running:
        return
    groups = load_config().get("wecomChatGroups") or []
    if not groups:
        _status["waitingSync"] = False
        _status["lastNote"] = "请先在管理后台填写关注会话，值班不会扫描全部会话"
        return
    try:
        listed = await W.list_merged_groups(kind="all", limit=1000, watch_only=True)
    except W.WecomError as e:
        _status["waitingSync"] = True
        _note_watch_fault(e.message)
        return
    items = listed.get("items") or []
    mark = _sync_mark(items)
    if not mark:
        _status["waitingSync"] = True
        return
    state = _load_state()
    prev = str(state.get("syncMark") or "")
    if prev and prev == mark:
        _status["waitingSync"] = True
        _clear_watch_fault()
        return
    _status["waitingSync"] = False
    result = await scan_once(trigger="sync")
    if not result.get("ok"):
        return
    _clear_watch_fault()
    state = _load_state()
    state["syncMark"] = mark
    _save_state(state)


async def scan_once(*, force: bool = False, trigger: str = "") -> dict:
    global _running
    async with _lock:
        cfg = _watch_cfg()
        if not cfg.get("enabled") and not force:
            return {"ok": False, "detail": "值班未开启"}
        if str(cfg.get("mode") or "") == "off" and not force:
            return {"ok": False, "detail": "值班模式为关闭"}
        ready, _model = _watch_model()
        if not ready:
            return {"ok": False, "detail": "未配置 Gemini，值班无法判断修改意见"}
        groups = load_config().get("wecomChatGroups") or []
        if not groups:
            note = "请先在管理后台填写关注会话，值班不会扫描全部会话"
            _status["lastNote"] = note
            return {"ok": False, "detail": note, "needGroups": True}
        health = await W.health()
        if not health.get("ok"):
            detail = "解析服务未就绪：" + str(health.get("error") or "")
            _note_watch_fault(detail)
            return {"ok": False, "detail": detail}
        _running = True
        _status["running"] = True
        _status["lastRunAt"] = _now()
        try:
            return await _scan_groups(cfg, force=force, trigger=trigger or ("manual" if force else "sync"))
        finally:
            _running = False
            _status["running"] = False


async def _scan_groups(cfg: dict, *, force: bool = False, trigger: str = "auto") -> dict:
    from .main import runner
    from .wecom_watch_store import save_scan_run

    t0 = time.monotonic()
    state = _load_state()
    state.setdefault("groups", {})
    state.setdefault("events", [])
    state.setdefault("pending", [])
    window_hours = int(cfg.get("windowHours") or 48)
    mode = str(cfg.get("mode") or "auto")
    owner = str(cfg.get("owner") or "wecom-watch")
    allow_none = bool(cfg.get("allowNoOpinion"))
    group_logs: list = []

    def _finish_log(**extra) -> dict:
        payload = {
            "at": _now(),
            "trigger": trigger,
            "windowHours": window_hours,
            "mode": mode,
            "owner": owner,
            "allowNoOpinion": allow_none,
            "force": bool(force),
            "groups": group_logs,
            "durationMs": int((time.monotonic() - t0) * 1000),
            **extra,
        }
        try:
            meta = save_scan_run(payload)
            payload["logId"] = meta.get("id") or ""
        except Exception:
            payload["logId"] = ""
        return payload

    try:
        allow = load_config().get("wecomChatGroups") or []
        listed = await W.list_merged_groups(kind="all", limit=1000, watch_only=bool(allow))
    except W.WecomError as e:
        _push_event(state, "列出会话失败：" + e.message, kind="error")
        _save_state(state)
        log = _finish_log(
            ok=False,
            error=e.message,
            groupCount=0,
            scannedCount=0,
            createdCount=0,
            pendingCount=0,
            skippedCount=0,
            summary="列出会话失败",
        )
        return {"ok": False, "detail": e.message, "logId": log.get("logId") or ""}

    items = listed.get("items") or []
    created_all, pending_all, skipped_all = [], [], []

    for g in items:
        if not isinstance(g, dict):
            continue
        sid = str(g.get("username") or g.get("session_id") or "").strip()
        name = str(g.get("display_name") or sid)
        if not sid:
            continue
        try:
            one = await _scan_group(
                runner, state, cfg, sid, name,
                window_hours=window_hours, mode=mode, owner=owner, allow_none=allow_none,
                force=force,
            )
        except Exception as e:
            _push_event(state, name + " 扫描失败：" + str(e)[:160], kind="error")
            group_logs.append({
                "sessionId": sid,
                "sessionName": name,
                "status": "error",
                "note": str(e)[:160],
            })
            continue
        created_all.extend(one.get("created") or [])
        pending_all.extend(one.get("pending") or [])
        skipped_all.extend(one.get("skipped") or [])
        st = one.get("stats") if isinstance(one.get("stats"), dict) else {}
        group_logs.append({
            "sessionId": sid,
            "sessionName": name,
            "status": one.get("status") or "done",
            "note": one.get("note") or "",
            "messageCount": int(st.get("messageCount") or 0),
            "freshCount": int(st.get("freshCount") or 0),
            "hasOpinion": bool(st.get("hasOpinion")),
            "caseCount": int(st.get("caseCount") or 0),
            "readyCount": int(st.get("readyCount") or 0),
            "fileClassNote": str(st.get("fileClassNote") or ""),
            "talentUploaded": int(st.get("talentUploaded") or 0),
            "talentUploadNote": str(st.get("talentUploadNote") or ""),
            "issueHits": int(st.get("issueHits") or 0),
            "createdCount": len(one.get("created") or []),
            "pendingCount": len(one.get("pending") or []),
            "pending": _pending_refs(one.get("pending") or []),
            "skippedCount": len(one.get("skipped") or []),
        })

    state["lastRunAt"] = _now()
    state["lastError"] = ""
    _status["pending"] = _public_pending(state.get("pending") or [])
    upload_n = sum(int(g.get("talentUploaded") or 0) for g in group_logs)
    if created_all:
        summary = "已提交 " + str(len(created_all)) + " 条待确认任务"
        _push_event(state, summary, kind="ok")
    elif pending_all:
        summary = "识别到意见，" + str(len(pending_all)) + " 条待人审"
        _push_event(state, summary, kind="info")
    else:
        summary = "本轮未发现可提交的修改意见"
        _push_event(state, summary, kind="info")
    if upload_n:
        extra = "材料入库 " + str(upload_n)
        summary = summary + "，" + extra
        _push_event(state, extra, kind="ok")
    _save_state(state)
    log = _finish_log(
        ok=True,
        groupCount=len(items),
        scannedCount=len(group_logs),
        createdCount=len(created_all),
        pendingCount=len(pending_all),
        skippedCount=len(skipped_all),
        summary=summary,
    )
    return {
        "ok": True,
        "created": created_all,
        "pending": pending_all,
        "skipped": skipped_all,
        "groupCount": len(items),
        "logId": log.get("logId") or "",
    }


def _new_messages(messages: list, cursor: tuple) -> list:
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if _msg_cursor(m) > cursor:
            out.append(m)
    return out


def _advance_cursor(row: dict, messages: list):
    best = _cursor_of(row)
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        cur = _msg_cursor(m)
        if cur > best:
            best = cur
    row["cursorTime"], row["cursorId"] = best[0], best[1]


def _case_snapshot(c: dict) -> dict:
    """待人审里留一份可再次上传的申报书和意见，不含大段聊天原文以外的对象。"""
    app = c.get("app") if isinstance(c.get("app"), dict) else {}
    ops = []
    for op in (c.get("opinions") or [])[:12]:
        if not isinstance(op, dict):
            continue
        ops.append({
            "role": op.get("role") or "opinion",
            "kind": op.get("kind") or "file",
            "filename": op.get("filename") or "",
            "time": op.get("time") or "",
            "sender": sender_of(op),
            "message_id": op.get("message_id") or 0,
            "copies": op.get("copies") or [],
            "text": str(op.get("text") or "")[:4000],
            "cached": bool(op.get("cached")),
        })
    return {
        "id": c.get("id"),
        "ready": True,
        "app": {
            "filename": app.get("filename") or "",
            "message_id": app.get("message_id") or 0,
            "time": app.get("time") or "",
            "sender": sender_of(app),
            "copies": app.get("copies") or [],
            "cached": bool(app.get("copies")),
        },
        "opinions": ops,
    }


def _opinion_labels(case: dict | None, hints: list | None = None) -> list:
    labels = []
    for op in ((case or {}).get("opinions") or []):
        if not isinstance(op, dict):
            continue
        if str(op.get("kind") or "") == "text":
            text = str(op.get("text") or "").strip().replace("\n", " ")
            label = text[:80]
        else:
            label = str(op.get("filename") or "").strip()
        if label and label not in labels:
            labels.append(label)
    for raw in hints or []:
        label = str(raw or "").strip().replace("\n", " ")[:80]
        if label and label not in labels:
            labels.append(label)
    return labels[:8]


def _pending_refs(cases: list) -> list:
    out = []
    for c in cases or []:
        if not isinstance(c, dict):
            continue
        out.append({
            "caseId": str(c.get("id") or ""),
            "filename": str((c.get("app") or {}).get("filename") or ""),
        })
    return out


def _live_pending_ids(state: dict) -> tuple[set, set]:
    ids, sessions = set(), set()
    for x in state.get("pending") or []:
        if not isinstance(x, dict):
            continue
        cid = str(x.get("caseId") or "").strip()
        sid = str(x.get("sessionId") or "").strip()
        if cid:
            ids.add(cid)
        if sid:
            sessions.add(sid)
    return ids, sessions


def _open_pending_count(group: dict, live_ids: set, live_sessions: set) -> int:
    """审核过（已上传或不上传）的不再算待审。"""
    rows = group.get("pending") if isinstance(group.get("pending"), list) else []
    if rows:
        n = 0
        for x in rows:
            if not isinstance(x, dict):
                continue
            cid = str(x.get("caseId") or "").strip()
            if cid and cid in live_ids:
                n += 1
        return n
    stored = int(group.get("pendingCount") or 0)
    if stored <= 0:
        return 0
    sid = str(group.get("sessionId") or "").strip()
    if sid and sid not in live_sessions:
        return 0
    return stored


def apply_review_to_log(log: dict) -> dict:
    """按当前待审队列改写扫描结果里的待审数。已处理的文件不再显示待审。"""
    if not isinstance(log, dict):
        return log
    state = _load_state()
    live_ids, live_sessions = _live_pending_ids(state)
    if not isinstance(log.get("groups"), list):
        return dict(log)
    groups = []
    total = 0
    changed = False
    for g in log.get("groups") or []:
        if not isinstance(g, dict):
            groups.append(g)
            continue
        row = dict(g)
        stored = int(row.get("pendingCount") or 0)
        open_n = _open_pending_count(row, live_ids, live_sessions)
        if open_n != stored:
            changed = True
            row["pendingCount"] = open_n
            if open_n == 0 and str(row.get("note") or "").startswith("待人审"):
                row["note"] = "待审已处理"
        total += open_n
        groups.append(row)
    out = dict(log)
    if groups:
        out["groups"] = groups
    if changed or int(log.get("pendingCount") or 0) != total:
        out["pendingCount"] = total
        summary = str(log.get("summary") or "")
        if total == 0 and ("待人审" in summary or "待审" in summary):
            out["summary"] = "待审已处理"
        elif total and total != int(log.get("pendingCount") or 0) and "待人审" in summary:
            out["summary"] = "识别到意见，" + str(total) + " 条待人审"
    return out


def apply_review_to_log_list(data: dict) -> dict:
    from .wecom_watch_store import get_scan_run
    if not isinstance(data, dict):
        return data
    items = []
    for meta in data.get("items") or []:
        if not isinstance(meta, dict):
            items.append(meta)
            continue
        full = get_scan_run(str(meta.get("id") or "")) or {}
        shown = apply_review_to_log(full) if full else meta
        row = dict(meta)
        row["pendingCount"] = int(shown.get("pendingCount") or 0)
        if shown.get("summary"):
            row["summary"] = shown.get("summary")
        items.append(row)
    out = dict(data)
    out["items"] = items
    return out


def _dismissed_ids(state: dict) -> set:
    out = set()
    for x in state.get("dismissed") or []:
        if isinstance(x, dict) and x.get("caseId"):
            out.add(str(x.get("caseId")))
    return out


def _remember_dismissed(state: dict, item: dict):
    cid = str(item.get("caseId") or "").strip()
    if not cid:
        return
    bag = [x for x in (state.get("dismissed") or []) if str((x or {}).get("caseId") or "") != cid]
    bag.insert(0, {
        "caseId": cid,
        "sessionId": item.get("sessionId") or "",
        "filename": item.get("filename") or "",
        "at": _now(),
    })
    state["dismissed"] = bag[:400]


def _norm_file(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


_COPY_PREFIX = re.compile(r"^(副本|拷贝|copy[_-]?)")
_COPY_SUFFIX = re.compile(r"(?:\(\d+\)|_v?\d+|[-_]\d+)$", re.I)


def _file_core(name: str) -> str:
    n = _norm_file(name)
    stem, _, ext = n.rpartition(".")
    if not stem:
        stem, ext = n, ""
    stem = _COPY_PREFIX.sub("", stem)
    stem = _COPY_SUFFIX.sub("", stem)
    return stem + (("." + ext) if ext else "")


def _opinion_key(op: dict) -> str:
    kind = str(op.get("kind") or "file")
    when = str(op.get("time") or "")[:16]
    if kind == "text":
        text = re.sub(r"\s+", "", str(op.get("text") or ""))[:80]
        return "text:" + when + ":" + text
    name = _norm_file(op.get("filename"))
    return "file:" + when + ":" + name


def _case_fingerprint(case: dict) -> dict:
    app = case.get("app") if isinstance(case.get("app"), dict) else {}
    keys = []
    for op in case.get("opinions") or []:
        if not isinstance(op, dict):
            continue
        k = _opinion_key(op)
        if k and k not in keys:
            keys.append(k)
    return {
        "filename": _norm_file(app.get("filename")),
        "appTime": str(app.get("time") or "")[:16],
        "opinionKeys": keys,
    }


def _same_file(case: dict, other_name: str) -> bool:
    a = _norm_file((case.get("app") or {}).get("filename") if isinstance(case.get("app"), dict) else "")
    b = _norm_file(other_name)
    if not a or not b:
        return False
    if a == b:
        return True
    return _file_core(a) == _file_core(b)


def _remember_uploaded(state: dict, case: dict, session_id: str):
    fp = _case_fingerprint(case or {})
    cid = str((case or {}).get("id") or "").strip()
    if not cid and not fp["filename"]:
        return
    bag = []
    for x in state.get("uploaded") or []:
        if not isinstance(x, dict):
            continue
        same_case = cid and str(x.get("caseId") or "") == cid
        same_file = (
            session_id
            and str(x.get("sessionId") or "") == session_id
            and fp["filename"]
            and x.get("filename") == fp["filename"]
        )
        if same_case or same_file:
            continue
        bag.append(x)
    bag.insert(0, {
        "caseId": cid,
        "sessionId": session_id,
        "filename": fp["filename"],
        "appTime": fp["appTime"],
        "opinionKeys": fp["opinionKeys"],
        "at": _now(),
    })
    state["uploaded"] = bag[:400]


def _uploaded_hit(state: dict, case: dict, session_id: str):
    fp = _case_fingerprint(case or {})
    cid = str((case or {}).get("id") or "")
    for x in state.get("uploaded") or []:
        if not isinstance(x, dict):
            continue
        if cid and str(x.get("caseId") or "") == cid:
            return x
        if (
            session_id
            and str(x.get("sessionId") or "") == session_id
            and fp["filename"]
            and x.get("filename") == fp["filename"]
        ):
            return x
    return None


def _has_new_material(prev: dict, case: dict) -> bool:
    fp = _case_fingerprint(case or {})
    old_time = str(prev.get("appTime") or "")
    if fp["appTime"] and old_time and fp["appTime"] > old_time:
        return True
    old = set(prev.get("opinionKeys") or [])
    return any(k not in old for k in fp["opinionKeys"])


def _repeat_without_new(state: dict, case: dict, session_id: str) -> bool:
    """同一份已上传申报书，且没有更新的申报书、也没有新的修改意见。"""
    prev = _uploaded_hit(state, case, session_id)
    if prev:
        return not _has_new_material(prev, case)
    if case.get("existing"):
        return True
    sim = case.get("similar") if isinstance(case.get("similar"), dict) else {}
    if sim and _same_file(case, sim.get("filename") or ""):
        return True
    return False


def _add_pending(state: dict, item: dict, case: dict | None = None, opinion_hints: list | None = None):
    cid = str(item.get("caseId") or "").strip()
    session_id = str(item.get("sessionId") or "").strip()
    if isinstance(case, dict) and _repeat_without_new(state, case, session_id):
        return
    if _watch_cfg().get("skipDismissed") and cid and cid in _dismissed_ids(state):
        return
    key = cid + "|" + str(item.get("reason") or "")[:40]
    bag = list(state.get("pending") or [])
    seen = {str(x.get("caseId") or "") + "|" + str(x.get("reason") or "")[:40] for x in bag}
    if key in seen:
        return
    item = dict(item)
    item["at"] = _now()
    item["opinions"] = _opinion_labels(case, opinion_hints)
    item["paired"] = bool(case and (case.get("opinions") or []))
    if isinstance(case, dict):
        item["case"] = _case_snapshot(case)
    bag.insert(0, item)
    state["pending"] = bag[:30]
    try:
        from . import alert as AL
        AL.watch_pending(item)
    except Exception:
        pass


def _drop_pending(state: dict, case_id: str) -> list:
    cid = str(case_id or "").strip()
    bag = [x for x in (state.get("pending") or []) if str(x.get("caseId") or "") != cid]
    state["pending"] = bag
    _status["pending"] = _public_pending(bag)
    return bag


def _app_origin(item: dict) -> dict:
    """申报书原消息：谁发的、何时、在哪台电脑上能找到。"""
    case = item.get("case") if isinstance(item.get("case"), dict) else {}
    app = case.get("app") if isinstance(case.get("app"), dict) else {}
    labels = []
    for c in app.get("copies") or []:
        if not isinstance(c, dict):
            continue
        lab = str(c.get("source_label") or c.get("source_id") or "").strip()
        if lab and lab not in labels:
            labels.append(lab)
    return {
        "sender": sender_of(app),
        "messageTime": str(app.get("time") or "").strip(),
        "sources": labels[:6],
    }


def _hide_tasked_pending(rows: list) -> list:
    """审核列表里去掉已经上传过、且不是「有新材料」的同一份申报书。"""
    try:
        from .main import runner
    except Exception:
        return list(rows or [])
    case_ids = set()
    names = set()
    for t in runner.list_meta() or []:
        wid = str(t.get("wecomCaseId") or "").strip()
        if wid:
            case_ids.add(wid)
        app = t.get("app") if isinstance(t.get("app"), dict) else {}
        fn = _norm_file(app.get("inputName") or app.get("name") or "")
        if fn:
            names.add(fn)
    out = []
    for x in rows or []:
        if not isinstance(x, dict):
            continue
        if str(x.get("reason") or "").startswith("已上传过，但有新的"):
            out.append(x)
            continue
        if str(x.get("caseId") or "") in case_ids:
            continue
        if _norm_file(x.get("filename")) in names:
            continue
        out.append(x)
    return out


def _public_pending(rows: list) -> list:
    out = []
    for x in _hide_tasked_pending(rows)[:30]:
        if not isinstance(x, dict):
            continue
        origin = _app_origin(x)
        out.append({
            "caseId": x.get("caseId") or "",
            "sessionId": x.get("sessionId") or "",
            "sessionName": x.get("sessionName") or "",
            "filename": x.get("filename") or "",
            "reason": x.get("reason") or "",
            "at": x.get("at") or "",
            "sender": origin["sender"],
            "messageTime": origin["messageTime"],
            "sources": origin["sources"],
            "opinions": [str(n) for n in (x.get("opinions") or []) if str(n or "").strip()][:8],
            "paired": bool(x.get("paired")),
        })
    return out


def dismiss_pending(case_id: str) -> dict:
    state = _load_state()
    cid = str(case_id or "").strip()
    if not cid:
        return {"ok": False, "detail": "缺少待审记录"}
    item = next((x for x in (state.get("pending") or []) if str(x.get("caseId") or "") == cid), None)
    if item:
        _remember_dismissed(state, item)
    bag = _drop_pending(state, cid)
    _save_state(state)
    return {"ok": True, "pending": _public_pending(bag)}


async def submit_pending(case_id: str, *, owner: str = "") -> dict:
    """人工确认后上传。已有相近任务也允许强制提交。"""
    from .main import runner
    from .wecom_submit import submit_cases

    cid = str(case_id or "").strip()
    state = _load_state()
    item = next((x for x in (state.get("pending") or []) if str(x.get("caseId") or "") == cid), None)
    if not item:
        return {"ok": False, "detail": "这条待审已不在列表中"}
    case = item.get("case") if isinstance(item.get("case"), dict) else None
    if not case:
        case = await _rebuild_pending_case(item)
    if not case:
        return {"ok": False, "detail": "找不到这份申报书的原消息，请重新扫描后再上传"}
    got = await submit_cases(
        runner,
        [case],
        session_id=str(item.get("sessionId") or ""),
        session_name=str(item.get("sessionName") or ""),
        owner=owner or str(_watch_cfg().get("owner") or ""),
        force=True,
        source_tag="wecom-watch",
    )
    if not got.get("created"):
        err = (got.get("errors") or [{}])[0]
        return {"ok": False, "detail": err.get("detail") or "上传失败", "errors": got.get("errors") or []}
    if isinstance(case, dict):
        _remember_uploaded(state, case, str(item.get("sessionId") or ""))
    bag = _drop_pending(state, cid)
    _save_state(state)
    created = got.get("created") or []
    _push_event(state, "已按待审上传 " + str(created[0].get("filename") or ""), kind="ok")
    _save_state(state)
    return {
        "ok": True,
        "created": created,
        "pending": _public_pending(bag),
        "detail": "已提交到待确认任务",
    }


async def _rebuild_pending_case(item: dict) -> dict | None:
    """旧的待审记录没有快照时，按会话再拆一次，用 caseId 对上。"""
    sid = str(item.get("sessionId") or "").strip()
    cid = str(item.get("caseId") or "").strip()
    if not sid or not cid:
        return None
    cfg = _watch_cfg()
    window_hours = int(cfg.get("windowHours") or 48)
    end = datetime.now()
    start = end - timedelta(hours=window_hours)
    data = await W.list_merged_messages(sid, full=True)
    messages = []
    for m in data.get("items") or []:
        if not isinstance(m, dict):
            continue
        dt = parse_time(m.get("time_text") or m.get("time"))
        if dt and (dt < start or dt > end):
            continue
        messages.append(m)
    kind_of, _note, _entries = await _context_kind_of(messages, sid)
    cases = cluster_messages(
        messages,
        session_id=sid,
        session_name=str(item.get("sessionName") or ""),
        window_hours=window_hours,
        kind_of=kind_of,
    )
    hit = next((c for c in cases if str(c.get("id") or "") == cid), None)
    if not hit:
        return None
    snap = _case_snapshot(hit)
    snap["id"] = cid
    return snap


async def _scan_group(
    runner,
    state: dict,
    cfg: dict,
    session_id: str,
    session_name: str,
    *,
    window_hours: int,
    mode: str,
    owner: str,
    allow_none: bool,
    force: bool = False,
) -> dict:
    from .wecom_submit import submit_cases

    row = state["groups"].setdefault(session_id, {"name": session_name, "cursorTime": "", "cursorId": 0})
    row["name"] = session_name
    end = datetime.now()
    start = end - timedelta(hours=window_hours)
    data = await W.list_merged_messages(
        session_id,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        full=True,
    )
    messages = data.get("items") or []
    stats = {
        "messageCount": len(messages),
        "freshCount": 0,
        "hasOpinion": False,
        "caseCount": 0,
        "readyCount": 0,
        "fileClassNote": "",
        "issueHits": 0,
        "talentUploaded": 0,
        "talentUploadNote": "",
    }

    def _ret(created, pending, skipped, *, status: str = "done", note: str = ""):
        return {
            "created": created,
            "pending": pending,
            "skipped": skipped,
            "status": status,
            "note": note,
            "stats": stats,
        }

    if not messages:
        return _ret([], [], [], status="empty", note="日期窗内无消息")

    cursor = _cursor_of(row)
    if not cursor[0] and not cursor[1] and not force:
        _advance_cursor(row, messages)
        note = "已记下水位，之后的新意见才会自动提交"
        _push_event(state, session_name + " " + note, kind="info")
        return _ret([], [], [], status="baseline", note=note)
    fresh = _new_messages(messages, cursor)
    stats["freshCount"] = len(fresh)
    if not fresh:
        return _ret([], [], [], status="idle", note="无新消息")

    try:
        from . import alert as AL
        stats["issueHits"] = int(AL.notify_group_issues(
            fresh,
            session_id=session_id,
            session_name=session_name,
            leader_messages=messages,
        ) or 0)
    except Exception:
        stats["issueHits"] = 0

    kind_of, class_note, file_entries = await _context_kind_of(messages, session_id)
    stats["fileClassNote"] = class_note
    up = await _upload_fresh_talent_files(
        entries=file_entries,
        fresh=fresh,
        session_id=session_id,
        session_name=session_name,
        cfg=cfg,
        state=state,
    )
    stats["talentUploaded"] = int(up.get("uploaded") or 0)
    stats["talentUploadNote"] = str(up.get("note") or "")

    rule_hits = rule_opinion_ids(fresh, kind_of=kind_of)
    maybe_text = []
    for m in fresh:
        if kind_of(m) in ("opinion", "text"):
            maybe_text.append(m)
            continue
        text = str(m.get("text") or m.get("snippet") or "").strip()
        if text and len(text) >= 8:
            maybe_text.append(m)
    if not rule_hits and not maybe_text:
        _advance_cursor(row, messages)
        note = "新消息中无可疑意见"
        if stats.get("talentUploaded"):
            note += "；" + (stats.get("talentUploadNote") or ("材料入库 " + str(stats["talentUploaded"])))
        elif stats.get("talentUploadNote") and "未配置" in str(stats.get("talentUploadNote") or ""):
            note += "；" + stats["talentUploadNote"]
        return _ret([], [], [], status="skip", note=note)

    detected = await detect_opinions(messages, kind_of=kind_of)
    stats["hasOpinion"] = bool(detected.get("hasOpinion"))
    if detected.get("error") and not detected.get("hasOpinion"):
        _push_event(state, session_name + "：" + detected["error"], kind="error")
        return _ret([], [], [], status="error", note=str(detected.get("error") or "")[:160])
    if not detected.get("hasOpinion"):
        _advance_cursor(row, messages)
        note = "Gemini 未识别到修改意见"
        if stats.get("talentUploaded"):
            note += "；" + (stats.get("talentUploadNote") or ("材料入库 " + str(stats["talentUploaded"])))
        return _ret([], [], [], status="no_opinion", note=note)

    cases = cluster_messages(
        messages,
        session_id=session_id,
        session_name=session_name,
        window_hours=window_hours,
        kind_of=kind_of,
    )
    cases, gemini_note = await assist_split_with_gemini(
        messages,
        cases,
        session_id=session_id,
        session_name=session_name,
        window_hours=window_hours,
        kind_of=kind_of,
    )
    annotate_existing(cases, runner)
    stats["caseCount"] = len(cases or [])

    opinion_hints = []
    for row in detected.get("opinions") or []:
        msg = row.get("message") if isinstance(row, dict) else None
        if not isinstance(msg, dict):
            continue
        name = str(msg.get("attachment_name") or "").strip()
        text = str(msg.get("text") or "").strip().replace("\n", " ")
        label = name or text[:80]
        if label and label not in opinion_hints:
            opinion_hints.append(label)
    confs = [str(x.get("confidence") or "medium") for x in (detected.get("opinions") or [])]
    qwen_high = "high" in confs or (confs and all(c == "high" for c in confs))
    qwen_medium = "medium" in confs or bool(detected.get("fallback"))

    runnable, pending, skipped = [], [], []
    for c in cases:
        app = c.get("app") or {}
        ops = c.get("opinions") or []
        if _repeat_without_new(state, c, session_id):
            if not _uploaded_hit(state, c, session_id):
                _remember_uploaded(state, c, session_id)
            skipped.append({
                "id": c.get("id"),
                "filename": app.get("filename"),
                "detail": "已上传且没有新申报书或修改意见",
            })
            _drop_pending(state, str(c.get("id") or ""))
            fn = _norm_file(app.get("filename"))
            if fn:
                state["pending"] = [
                    x for x in (state.get("pending") or [])
                    if not (
                        str(x.get("sessionId") or "") == session_id
                        and _norm_file(x.get("filename")) == fn
                    )
                ]
                _status["pending"] = _public_pending(state.get("pending") or [])
            continue
        prev = _uploaded_hit(state, c, session_id)
        if prev and _has_new_material(prev, c):
            _add_pending(state, {
                "sessionId": session_id,
                "sessionName": session_name,
                "caseId": c.get("id"),
                "filename": app.get("filename"),
                "reason": "已上传过，但有新的申报书或修改意见",
            }, c, opinion_hints)
            pending.append(c)
            continue
        if c.get("existing"):
            skipped.append({"id": c.get("id"), "filename": app.get("filename"), "detail": "已建过任务"})
            continue
        if c.get("similar") and _same_file(c, (c.get("similar") or {}).get("filename") or ""):
            skipped.append({"id": c.get("id"), "filename": app.get("filename"), "detail": "已有相同申报书的任务"})
            continue
        if not ops and not allow_none:
            skipped.append({"id": c.get("id"), "filename": app.get("filename"), "detail": "有申报书但未配上修改意见"})
            _add_pending(state, {
                "sessionId": session_id,
                "sessionName": session_name,
                "caseId": c.get("id"),
                "filename": app.get("filename"),
                "reason": "认出修改意见，但未配到这份申报书",
            }, c, opinion_hints)
            pending.append(c)
            continue
        if not c.get("ready"):
            skipped.append({"id": c.get("id"), "filename": app.get("filename"), "detail": "申报书未缓存"})
            _add_pending(state, {
                "sessionId": session_id,
                "sessionName": session_name,
                "caseId": c.get("id"),
                "filename": app.get("filename"),
                "reason": "申报书未在各电脑缓存",
            }, c, opinion_hints)
            pending.append(c)
            continue
        auto_ok = mode == "auto" and (qwen_high or qwen_medium)
        if not auto_ok:
            _add_pending(state, {
                "sessionId": session_id,
                "sessionName": session_name,
                "caseId": c.get("id"),
                "filename": app.get("filename"),
                "reason": "待人审拆解（" + (detected.get("reason") or mode) + "）",
            }, c, opinion_hints)
            pending.append(c)
            continue
        runnable.append(c)

    stats["readyCount"] = len(runnable)

    created = []
    errors = []
    if runnable:
        got = await submit_cases(
            runner,
            runnable,
            session_id=session_id,
            session_name=session_name,
            owner=owner,
            source_tag="wecom-watch",
        )
        created = got.get("created") or []
        errors = got.get("errors") or []
        for row_err in errors:
            _add_pending(state, {
                "sessionId": session_id,
                "sessionName": session_name,
                "caseId": row_err.get("id"),
                "filename": row_err.get("filename"),
                "reason": row_err.get("detail") or "提交失败",
            }, None, opinion_hints)
        if created:
            created_ids = {str(x.get("caseId") or "") for x in created}
            for done in runnable:
                if str(done.get("id") or "") in created_ids:
                    _remember_uploaded(state, done, session_id)
            _push_event(
                state,
                session_name + " 提交 " + "、".join(x.get("filename") or x.get("id") for x in created[:4]),
                kind="ok",
            )
        if gemini_note:
            _push_event(state, session_name + "：" + gemini_note, kind="info")

    _advance_cursor(row, messages)
    note = ""
    if created:
        note = "提交 " + str(len(created)) + " 条"
    elif pending:
        note = "待人审 " + str(len(pending)) + " 条"
    elif skipped or errors:
        note = "跳过 " + str(len(skipped) + len(errors)) + " 条"
    if stats.get("talentUploaded"):
        extra = stats.get("talentUploadNote") or ("材料入库 " + str(stats["talentUploaded"]))
        note = (note + "；" + extra) if note else extra
    return _ret(created, pending, skipped + errors, status="done", note=note)
