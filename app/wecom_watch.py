"""聊天值班：Gemini 按固定题目认出修改意见后，再配申报书并提交到待确认。"""
from __future__ import annotations
import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from .config import DATA_DIR, atomic_replace, load_config, resolve_watch_llm
from . import wecom_client as W
from .wecom_cases import annotate_existing, cluster_messages, _msg_kind
from .wecom_locate import parse_time
from .wecom_split_llm import assist_split_with_gemini
from .wecom_watch_llm import detect_opinions, rule_opinion_ids

_STATE_PATH = DATA_DIR / "data" / "wecom_watch.json"
_lock = asyncio.Lock()
_running = False
_status = {
    "running": False,
    "lastRunAt": "",
    "lastError": "",
    "lastNote": "",
    "events": [],
    "pending": [],
    "groups": {},
}


def _watch_cfg() -> dict:
    w = ((load_config().get("wecomChat") or {}).get("watch")) or {}
    return w if isinstance(w, dict) else {}


def _now() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


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
    }


def start_watch(loop: asyncio.AbstractEventLoop):
    loop.create_task(_loop())


async def _loop():
    await asyncio.sleep(8)
    while True:
        cfg = _watch_cfg()
        interval = int(cfg.get("intervalSec") or 120)
        try:
            if cfg.get("enabled") and str(cfg.get("mode") or "") != "off":
                await scan_once()
        except Exception as e:
            _status["lastError"] = str(e)[:200]
            print("[wecom-watch] 循环异常：" + str(e)[:200], flush=True)
        await asyncio.sleep(max(30, interval))


async def scan_once(*, force: bool = False) -> dict:
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
            _status["lastError"] = detail
            return {"ok": False, "detail": detail}
        _running = True
        _status["running"] = True
        _status["lastRunAt"] = _now()
        try:
            return await _scan_groups(cfg, force=force, trigger="manual" if force else "auto")
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
            "createdCount": len(one.get("created") or []),
            "pendingCount": len(one.get("pending") or []),
            "skippedCount": len(one.get("skipped") or []),
        })

    state["lastRunAt"] = _now()
    state["lastError"] = ""
    _status["pending"] = _public_pending(state.get("pending") or [])
    if created_all:
        summary = "已提交 " + str(len(created_all)) + " 条待确认任务"
        _push_event(state, summary, kind="ok")
    elif pending_all:
        summary = "识别到意见，" + str(len(pending_all)) + " 条待人审"
        _push_event(state, summary, kind="info")
    else:
        summary = "本轮未发现可提交的修改意见"
        _push_event(state, summary, kind="info")
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
            "sender": op.get("sender") or "",
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
            "sender": app.get("sender") or "",
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


def _add_pending(state: dict, item: dict, case: dict | None = None, opinion_hints: list | None = None):
    cid = str(item.get("caseId") or "").strip()
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


def _drop_pending(state: dict, case_id: str) -> list:
    cid = str(case_id or "").strip()
    bag = [x for x in (state.get("pending") or []) if str(x.get("caseId") or "") != cid]
    state["pending"] = bag
    _status["pending"] = _public_pending(bag)
    return bag


def _public_pending(rows: list) -> list:
    out = []
    for x in (rows or [])[:30]:
        if not isinstance(x, dict):
            continue
        out.append({
            "caseId": x.get("caseId") or "",
            "sessionId": x.get("sessionId") or "",
            "sessionName": x.get("sessionName") or "",
            "filename": x.get("filename") or "",
            "reason": x.get("reason") or "",
            "at": x.get("at") or "",
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
    cases = cluster_messages(
        messages,
        session_id=sid,
        session_name=str(item.get("sessionName") or ""),
        window_hours=window_hours,
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
    stats = {"messageCount": len(messages), "freshCount": 0, "hasOpinion": False, "caseCount": 0, "readyCount": 0}

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

    rule_hits = rule_opinion_ids(fresh)
    maybe_text = []
    for m in fresh:
        if _msg_kind(m) in ("opinion", "text"):
            maybe_text.append(m)
            continue
        text = str(m.get("text") or m.get("snippet") or "").strip()
        if text and len(text) >= 8:
            maybe_text.append(m)
    if not rule_hits and not maybe_text:
        _advance_cursor(row, messages)
        return _ret([], [], [], status="skip", note="新消息中无可疑意见")

    detected = await detect_opinions(messages)
    stats["hasOpinion"] = bool(detected.get("hasOpinion"))
    if detected.get("error") and not detected.get("hasOpinion"):
        _push_event(state, session_name + "：" + detected["error"], kind="error")
        return _ret([], [], [], status="error", note=str(detected.get("error") or "")[:160])
    if not detected.get("hasOpinion"):
        _advance_cursor(row, messages)
        return _ret([], [], [], status="no_opinion", note="Gemini 未识别到修改意见")

    cases = cluster_messages(
        messages,
        session_id=session_id,
        session_name=session_name,
        window_hours=window_hours,
    )
    cases, gemini_note = await assist_split_with_gemini(
        messages,
        cases,
        session_id=session_id,
        session_name=session_name,
        window_hours=window_hours,
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
        if c.get("existing"):
            skipped.append({"id": c.get("id"), "filename": app.get("filename"), "detail": "已建过任务"})
            continue
        if c.get("similar"):
            skipped.append({"id": c.get("id"), "filename": app.get("filename"), "detail": "已有相近任务"})
            _add_pending(state, {
                "sessionId": session_id,
                "sessionName": session_name,
                "caseId": c.get("id"),
                "filename": app.get("filename"),
                "reason": "系统里已有较接近的任务，未自动提交",
            }, c, opinion_hints)
            pending.append(c)
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
    return _ret(created, pending, skipped + errors, status="done", note=note)
