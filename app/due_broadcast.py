"""预计交付日期定时播报：走现有 alert.webhook，后台可配时刻。"""
from __future__ import annotations
import asyncio
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA_DIR, TASKS_DIR, atomic_replace, load_config
from . import wecom_notify as N

_LOCK = threading.Lock()
_STORE = DATA_DIR / "data" / "due_broadcast.json"
_status = {
    "running": False,
    "lastRunAt": "",
    "lastNote": "",
    "lastError": "",
}


def cfg() -> dict:
    raw = ((load_config().get("alert") or {}).get("dueBroadcast")) or {}
    return raw if isinstance(raw, dict) else {}


def parse_dt(s: str):
    t = str(s or "").strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            cut = t[:19] if fmt.endswith("%S") and len(t) >= 19 else t[:10]
            return datetime.strptime(cut, fmt)
        except ValueError:
            continue
    return None


def add_workdays(dt: datetime, n: int) -> datetime:
    d = dt
    left = max(0, int(n))
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def _label(t: dict) -> str:
    app = t.get("app") if isinstance(t.get("app"), dict) else {}
    name = str(t.get("personName") or app.get("personName") or "").strip()
    no = str(t.get("attachId") or app.get("attachId") or app.get("no") or "").strip()
    fn = str(app.get("name") or app.get("inputName") or "").strip()
    bits = [x for x in (no, name) if x]
    if not bits and fn:
        bits.append(fn)
    wecom = t.get("wecom") if isinstance(t.get("wecom"), dict) else {}
    sess = str(wecom.get("sessionName") or t.get("wecomSession") or "").strip()
    head = " ".join(bits) or str(t.get("id") or "任务")
    return head + ((" · " + sess) if sess else "")


def _wecom_of(t: dict) -> str:
    wecom = t.get("wecom") if isinstance(t.get("wecom"), dict) else {}
    return str(wecom.get("sessionName") or wecom.get("sessionId") or t.get("wecomSession") or "").strip()


def load_tasks() -> list:
    out = []
    root = Path(TASKS_DIR)
    if not root.exists():
        return out
    for d in root.iterdir():
        mp = d / "meta.json"
        if not mp.is_file():
            continue
        try:
            t = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(t, dict):
            out.append(t)
    return out


def collect(c: dict | None = None) -> dict:
    c = c if isinstance(c, dict) else cfg()
    sla = int(c.get("slaWorkdays") or 1)
    recent = int(c.get("recentDays") or 0)
    wecom_only = bool(c.get("wecomOnly"))
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    pending, done_today = [], []
    for t in load_tasks():
        if wecom_only and not _wecom_of(t):
            continue
        created = parse_dt(t.get("createdAt") or "") or datetime.min
        if recent > 0 and created != datetime.min and created < today - timedelta(days=recent):
            continue
        applied = parse_dt(t.get("appliedAt") or "")
        st = str(t.get("status") or "")
        if applied:
            if applied.date() == today.date():
                done_today.append(t)
            continue
        if st in ("done", "failed"):
            continue
        due = add_workdays(created if created != datetime.min else datetime.now(), sla)
        overdue = due.date() < today.date()
        pending.append({"due": due, "overdue": overdue, "task": t, "label": _label(t)})
    pending.sort(key=lambda x: (not x["overdue"], x["due"], str((x["task"] or {}).get("createdAt") or "")))
    return {"pending": pending, "doneToday": done_today, "today": today, "sla": sla}


def build_message(*, test: bool = False, c: dict | None = None) -> dict:
    c = c if isinstance(c, dict) else cfg()
    bag = collect(c)
    pending = bag["pending"]
    done_today = bag["doneToday"]
    today = bag["today"]
    sla = bag["sla"]
    cap = int(c.get("maxLines") or 20)
    title = "【交付播报·试发】" if test else "【交付播报】"
    lines = [
        title + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "规则：创建时间 + " + str(sla) + " 个工作日",
        "",
        "待交付 " + str(len(pending)) + " 份"
        + (("，今日已交 " + str(len(done_today)) + " 份") if done_today else ""),
    ]
    shown = pending[:cap]
    if not shown:
        lines.append("（当前没有待交付任务）")
    for row in shown:
        due = row["due"]
        tag = "已逾期" if row["overdue"] else ("今日" if due.date() == today.date() else due.strftime("%m-%d"))
        lines.append("· " + str(row["label"] or "")[:48] + "  预计 " + tag)
    more = len(pending) - len(shown)
    if more > 0:
        lines.append("… 另有 " + str(more) + " 份未列出")
    text = "\n".join(lines)[:2000]
    return {
        "text": text,
        "pending": len(pending),
        "doneToday": len(done_today),
        "shown": len(shown),
        "empty": not pending,
    }


def public_status() -> dict:
    ready = N.configured()
    c = cfg()
    st = _load()
    preview = {}
    try:
        preview = build_message()
    except Exception as e:
        preview = {"text": "", "error": str(e)[:160], "pending": 0, "doneToday": 0, "shown": 0, "empty": True}
    logs = st.get("logs") if isinstance(st.get("logs"), list) else []
    return {
        "ok": True,
        "enabled": bool(c.get("enabled")),
        "times": list(c.get("times") or ["09:30"]),
        "slaWorkdays": int(c.get("slaWorkdays") or 1),
        "skipWeekend": bool(c.get("skipWeekend", True)),
        "recentDays": int(c.get("recentDays") or 0),
        "maxLines": int(c.get("maxLines") or 20),
        "sendIfEmpty": bool(c.get("sendIfEmpty")),
        "wecomOnly": bool(c.get("wecomOnly")),
        "webhookReady": ready,
        "lastRunAt": _status.get("lastRunAt") or st.get("lastRunAt") or "",
        "lastNote": _status.get("lastNote") or st.get("lastNote") or "",
        "lastError": _status.get("lastError") or st.get("lastError") or "",
        "preview": preview.get("text") or "",
        "pending": int(preview.get("pending") or 0),
        "doneToday": int(preview.get("doneToday") or 0),
        "logs": logs[:40],
    }


def send_now(*, test: bool = False, reason: str = "manual") -> dict:
    c = cfg()
    if not N.configured():
        return {"ok": False, "error": "未配置 alert.webhook"}
    msg = build_message(test=test, c=c)
    if msg.get("empty") and not test and not c.get("sendIfEmpty"):
        note = "待交付为空，未发送"
        _note(note, ok=True, skipped=True)
        return {"ok": True, "skipped": "empty", "detail": note, "preview": msg.get("text") or ""}
    try:
        N.send_text(msg["text"])
    except N.NotifyError as e:
        _note(str(e.message), ok=False)
        return {"ok": False, "error": e.message, "preview": msg.get("text") or ""}
    note = ("试发" if test else "已播报") + " 待交付 " + str(msg.get("pending") or 0) + " 份"
    _note(note, ok=True, reason=reason, body=msg.get("text") or "")
    return {"ok": True, "detail": note, "pending": msg.get("pending"), "preview": msg.get("text") or ""}


def start_due_broadcast(loop: asyncio.AbstractEventLoop):
    loop.create_task(_loop())


async def _loop():
    await asyncio.sleep(6)
    while True:
        try:
            await asyncio.to_thread(_tick)
        except Exception as e:
            _status["lastError"] = str(e)[:160]
            print("[due-broadcast] 循环异常：" + str(e)[:200], flush=True)
        await asyncio.sleep(20)


def _tick():
    c = cfg()
    if not c.get("enabled"):
        return
    if not N.configured():
        return
    now = datetime.now()
    if c.get("skipWeekend") and now.weekday() >= 5:
        return
    slot = now.strftime("%H:%M")
    times = [str(x) for x in (c.get("times") or []) if str(x)]
    if slot not in times:
        return
    key = now.strftime("%Y-%m-%d") + "|" + slot
    st = _load()
    sent = st.get("sent") if isinstance(st.get("sent"), dict) else {}
    if sent.get(key):
        return
    got = send_now(test=False, reason="schedule:" + slot)
    if not got.get("ok") and not got.get("skipped"):
        _status["lastError"] = str(got.get("error") or "发送失败")[:160]
        return
    sent[key] = now.strftime("%Y-%m-%d %H:%M:%S")
    if len(sent) > 60:
        for old in sorted(sent.keys())[:-40]:
            sent.pop(old, None)
    with _LOCK:
        data = _load()
        data["sent"] = sent
        _save(data)


def _note(text: str, *, ok: bool, skipped: bool = False, reason: str = "", body: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _status["lastRunAt"] = now
    _status["lastNote"] = text
    _status["lastError"] = "" if ok else text
    _append_log({
        "ok": bool(ok),
        "skipped": bool(skipped),
        "at": now,
        "note": text,
        "reason": reason,
        "body": (body or "")[:240],
    })
    with _LOCK:
        data = _load()
        data["lastRunAt"] = now
        data["lastNote"] = text
        _save(data)


def _load() -> dict:
    if not _STORE.exists():
        return {"sent": {}, "logs": [], "lastRunAt": "", "lastNote": ""}
    try:
        obj = json.loads(_STORE.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {"sent": {}, "logs": []}
    except Exception:
        return {"sent": {}, "logs": []}


def _save(data: dict):
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(_STORE) + ".tmp"
    Path(tmp).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    atomic_replace(tmp, _STORE)


def _append_log(row: dict):
    with _LOCK:
        data = _load()
        logs = list(data.get("logs") or [])
        logs.insert(0, row)
        data["logs"] = logs[:80]
        _save(data)
