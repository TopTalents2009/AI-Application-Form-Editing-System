"""聊天值班：Qwen 认出修改意见后，交给 Gemini 配申报书并提交到待确认。"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from .config import DATA_DIR, atomic_replace, load_config
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


def public_status() -> dict:
    cfg = _watch_cfg()
    groups = load_config().get("wecomChatGroups") or []
    st = _load_state()
    pending = list(st.get("pending") or _status.get("pending") or [])[:20]
    events = list(st.get("events") or _status.get("events") or [])[:12]
    return {
        "ok": True,
        "enabled": bool(cfg.get("enabled")),
        "mode": str(cfg.get("mode") or "off"),
        "model": str(cfg.get("model") or ""),
        "modelReady": bool(cfg.get("configured")),
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
        if not cfg.get("configured"):
            return {"ok": False, "detail": "未配置值班模型密钥"}
        groups = load_config().get("wecomChatGroups") or []
        if not groups:
            note = "请先在管理后台填写关注群，值班不会扫描全部会话"
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
            return await _scan_groups(cfg, force=force)
        finally:
            _running = False
            _status["running"] = False


async def _scan_groups(cfg: dict, *, force: bool = False) -> dict:
    from .main import runner
    state = _load_state()
    state.setdefault("groups", {})
    state.setdefault("events", [])
    state.setdefault("pending", [])
    try:
        listed = await W.list_merged_groups(kind="group", limit=400)
    except W.WecomError as e:
        _push_event(state, "列出群失败：" + e.message, kind="error")
        _save_state(state)
        return {"ok": False, "detail": e.message}

    items = listed.get("items") or []
    window_hours = int(cfg.get("windowHours") or 48)
    mode = str(cfg.get("mode") or "auto")
    owner = str(cfg.get("owner") or "wecom-watch")
    allow_none = bool(cfg.get("allowNoOpinion"))
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
            continue
        created_all.extend(one.get("created") or [])
        pending_all.extend(one.get("pending") or [])
        skipped_all.extend(one.get("skipped") or [])

    state["lastRunAt"] = _now()
    state["lastError"] = ""
    _status["pending"] = list(state.get("pending") or [])[:20]
    if created_all:
        _push_event(state, "已提交 " + str(len(created_all)) + " 条待确认任务", kind="ok")
    elif pending_all:
        _push_event(state, "识别到意见，" + str(len(pending_all)) + " 条待人审", kind="info")
    else:
        _push_event(state, "本轮未发现可提交的修改意见", kind="info")
    _save_state(state)
    return {
        "ok": True,
        "created": created_all,
        "pending": pending_all,
        "skipped": skipped_all,
        "groupCount": len(items),
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


def _add_pending(state: dict, item: dict):
    key = str(item.get("caseId") or "") + "|" + str(item.get("reason") or "")[:40]
    bag = list(state.get("pending") or [])
    seen = {str(x.get("caseId") or "") + "|" + str(x.get("reason") or "")[:40] for x in bag}
    if key in seen:
        return
    item = dict(item)
    item["at"] = _now()
    bag.insert(0, item)
    state["pending"] = bag[:30]


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
    if not messages:
        return {"created": [], "pending": [], "skipped": []}

    cursor = _cursor_of(row)
    if not cursor[0] and not cursor[1] and not force:
        _advance_cursor(row, messages)
        _push_event(state, session_name + " 已记下水位，之后的新意见才会自动提交", kind="info")
        return {"created": [], "pending": [], "skipped": []}
    fresh = _new_messages(messages, cursor)
    if not fresh:
        return {"created": [], "pending": [], "skipped": []}

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
        return {"created": [], "pending": [], "skipped": []}

    detected = await detect_opinions(messages)
    if detected.get("error") and not detected.get("hasOpinion"):
        _push_event(state, session_name + "：" + detected["error"], kind="error")
        return {"created": [], "pending": [], "skipped": []}
    if not detected.get("hasOpinion"):
        _advance_cursor(row, messages)
        return {"created": [], "pending": [], "skipped": []}

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
            })
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
            })
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
            })
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
            })
            pending.append(c)
            continue
        runnable.append(c)

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
            })
        if created:
            _push_event(
                state,
                session_name + " 提交 " + "、".join(x.get("filename") or x.get("id") for x in created[:4]),
                kind="ok",
            )
        if gemini_note:
            _push_event(state, session_name + "：" + gemini_note, kind="info")

    _advance_cursor(row, messages)
    return {"created": created, "pending": pending, "skipped": skipped + errors}
