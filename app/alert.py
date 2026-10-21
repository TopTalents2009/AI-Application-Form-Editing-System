"""重要事件告警：过滤、去重、路由组长，再走企微群消息推送。"""
from __future__ import annotations
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from .config import DATA_DIR, atomic_replace, load_config
from . import wecom_notify as N
from .party import sender_of

_MOBILE_RE = re.compile(r"(?<!\d)(1\d{10})(?!\d)")
# 「申报一组唐文秀」「申报书三组组员-…」「匹配三组长-…」→ 组键
_TEAM_RE = re.compile(r"(申报书?[一二三四五六七八九十百零〇\d]+组|匹配[一二三四五六七八九十百零〇\d]+组)")

_LOCK = threading.Lock()
_STORE = DATA_DIR / "data" / "alert_log.json"
_LEVEL_RANK = {"P0": 0, "P1": 1, "P2": 2}


def cfg() -> dict:
    blob = load_config().get("alert") or {}
    return blob if isinstance(blob, dict) else {}


def _due_broadcast_status() -> dict:
    ready = N.configured()
    try:
        from . import due_broadcast as DB
        st = DB.public_status()
        if not isinstance(st, dict):
            st = {}
        st["webhookReady"] = ready
        return st
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)[:160],
            "webhookReady": ready,
            "enabled": False,
            "times": ["09:30"],
        }


def public_status() -> dict:
    c = cfg()
    logs = _load().get("logs") or []
    return {
        "ok": True,
        "enabled": bool(c.get("enabled")),
        "configured": N.configured(),
        "minLevel": str(c.get("minLevel") or "P1"),
        "hasWebhook": N.configured(),
        "webhookHint": _mask_webhook(str(c.get("webhook") or "")),
        "publicBaseUrl": str(c.get("publicBaseUrl") or ""),
        "leaders": list(c.get("leaders") or []),
        "leaderCount": len(c.get("leaders") or []),
        "dedupeMinutes": int(c.get("dedupeMinutes") or 15),
        "logs": logs[:80],
        "dueBroadcast": _due_broadcast_status(),
        "issueWatch": dict(c.get("issueWatch") or {}),
    }


def list_logs(status: str = "") -> list:
    logs = list(_load().get("logs") or [])
    st = str(status or "").strip().lower()
    if st in ("ok", "success"):
        logs = [x for x in logs if x.get("ok")]
    elif st in ("fail", "bad", "error"):
        logs = [x for x in logs if not x.get("ok")]
    return logs[:80]


def clear_logs() -> None:
    with _LOCK:
        data = _load()
        data["logs"] = []
        _save(data)


def _mask_webhook(url: str) -> str:
    s = str(url or "")
    i = s.find("key=")
    if i < 0:
        return "已配置" if s else ""
    key = s[i + 4:].strip()
    if len(key) <= 6:
        return "已配置"
    return "…" + key[-4:]


def emit(
    *,
    level: str,
    kind: str,
    title: str,
    lines: list | None = None,
    url: str = "",
    department: str = "",
    dedupe_key: str = "",
    force: bool = False,
    mentioned_mobile: list | None = None,
) -> dict:
    """发一条告警。force=True 跳过去重（试发）。"""
    c = cfg()
    lv = str(level or "P1").upper()
    if lv not in _LEVEL_RANK:
        lv = "P1"
    if not force and not c.get("enabled"):
        return {"ok": False, "skipped": "disabled"}
    if not N.configured():
        return {"ok": False, "skipped": "no_webhook"}
    min_lv = str(c.get("minLevel") or "P1").upper()
    if min_lv not in _LEVEL_RANK:
        min_lv = "P1"
    if not force and _LEVEL_RANK[lv] > _LEVEL_RANK[min_lv]:
        return {"ok": False, "skipped": "below_min_level"}
    key = str(dedupe_key or (kind + "|" + title)).strip()[:180]
    if not force and _recent(key, int(c.get("dedupeMinutes") or 15)):
        return {"ok": False, "skipped": "deduped"}
    mobiles = []
    for x in mentioned_mobile or []:
        m = str(x or "").strip()
        if m and m not in mobiles:
            mobiles.append(m)
    # 有手机号时优先走 mentioned_mobile_list；否则按部门匹配 userid
    mentioned = [] if mobiles else _mentioned(department)
    body = _format(lv, title, lines or [], url)
    try:
        N.send_text(body, mentioned=mentioned, mentioned_mobile=mobiles)
    except N.NotifyError as e:
        _append_log({
            "ok": False, "level": lv, "kind": kind, "title": title,
            "body": body[:240], "mentioned": mentioned, "mentionedMobile": mobiles,
            "error": e.message,
        })
        return {"ok": False, "error": e.message}
    _remember(key)
    _append_log({
        "ok": True, "level": lv, "kind": kind, "title": title,
        "body": body[:240], "mentioned": mentioned, "mentionedMobile": mobiles,
        "error": "",
    })
    return {"ok": True, "mentioned": mentioned, "mentionedMobile": mobiles}


def ops_fault(*, kind: str, title: str, lines: list | None = None) -> dict:
    """解析服务 / 值班循环等运维故障。走现有 Webhook，15 分钟去重。"""
    k = str(kind or "ops").strip() or "ops"
    t = str(title or "系统运维异常").strip() or "系统运维异常"
    href = "/wecom" if "watch" in k or "parser" in k else "/system"
    return emit(
        level="P1",
        kind=k,
        title=t,
        lines=[str(x)[:200] for x in (lines or []) if str(x).strip()][:6],
        url=_admin_url(href),
        department="",
        dedupe_key="ops|" + k + "|" + t[:60],
    )


def task_failed(task: dict) -> dict:
    t = task if isinstance(task, dict) else {}
    err = str(t.get("error") or "").strip()
    if "服务重启导致中断" in err:
        return {"ok": False, "skipped": "restart"}
    app = t.get("app") if isinstance(t.get("app"), dict) else {}
    name = str(app.get("personName") or t.get("personName") or app.get("name") or "").strip()
    no = str(app.get("attachId") or app.get("no") or t.get("attachId") or "").strip()
    owner = str(t.get("owner") or "").strip()
    tid = str(t.get("id") or "").strip()
    title = "任务失败"
    if no or name:
        title = "任务失败 " + " ".join(x for x in (no, name) if x)
    lines = []
    if owner:
        lines.append("提交人：" + owner)
    if err:
        lines.append("原因：" + err.replace("\n", " ")[:240])
    return emit(
        level="P0",
        kind="task_failed",
        title=title,
        lines=lines,
        url=_task_url(tid),
        department=_owner_dept(owner),
        dedupe_key="task_failed|" + tid + "|" + err[:80],
    )


def feedback_new(item: dict) -> dict:
    it = item if isinstance(item, dict) else {}
    who = str(it.get("realName") or it.get("real_name") or it.get("username") or "").strip()
    dept = str(it.get("department") or "").strip()
    text = str(it.get("content") or "").replace("\n", " ").strip()
    fid = str(it.get("id") or "")
    lines = []
    if who:
        lines.append("提交人：" + who + (("（" + dept + "）") if dept else ""))
    if text:
        lines.append(text[:200])
    nfile = len(it.get("files") or [])
    if nfile:
        lines.append("截图 " + str(nfile) + " 张")
    return emit(
        level="P1",
        kind="feedback",
        title="新用户反馈" + ((" #" + fid) if fid else ""),
        lines=lines,
        url=_admin_url("/feedback"),
        department=dept,
        dedupe_key="feedback|" + fid,
    )


def watch_pending(item: dict) -> dict:
    it = item if isinstance(item, dict) else {}
    sess = str(it.get("sessionName") or "").strip()
    fn = str(it.get("filename") or "").strip()
    reason = str(it.get("reason") or "").strip()
    lines = []
    if sess:
        lines.append("会话：" + sess)
    if fn:
        lines.append("申报书：" + fn)
    if reason:
        lines.append(reason)
    return emit(
        level="P1",
        kind="watch_pending",
        title="值班待审申报书",
        lines=lines,
        url=_admin_url("/wecom"),
        department="",
        dedupe_key="watch|" + str(it.get("caseId") or "") + "|" + reason[:40],
    )


def leaders_from_messages(messages: list | None) -> list[str]:
    """会话里昵称含「组长」的发送者，去重保序。"""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        who = sender_of(m)
        if "组长" not in who:
            continue
        if who not in out:
            out.append(who)
    return out


def team_key_from_name(name: str) -> str:
    """从会话名/昵称提取组键，如「申报一组唐文秀」→「申报一组」。"""
    s = str(name or "").strip()
    if not s:
        return ""
    m = _TEAM_RE.search(s)
    return m.group(1) if m else ""


def mobiles_from_leader_names(names: list | None) -> list[str]:
    """从组长昵称提取手机号，如「…小唐18198156910」→ 18198156910。"""
    out = []
    for name in names or []:
        for m in _MOBILE_RE.findall(str(name or "")):
            if m not in out:
                out.append(m)
    return out


def _watched_group_names(groups: dict | None = None) -> list[str]:
    """已关注会话显示名。groups 可注入便于测试。"""
    if groups is None:
        try:
            from . import wecom_watch as WW
            state = WW._load_state()
            groups = state.get("groups") if isinstance(state, dict) else {}
        except Exception:
            groups = {}
    out = []
    if not isinstance(groups, dict):
        return out
    for sid, row in groups.items():
        if isinstance(row, dict):
            name = str(row.get("name") or "").strip()
        else:
            name = str(row or "").strip()
        if not name:
            name = str(sid or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def leaders_from_watched_sessions(
    session_name: str = "",
    *,
    session_id: str = "",
    groups: dict | None = None,
) -> list[str]:
    """按组键在已关注会话名里找含「组长」的会话（如申报一组 → 申报一组组长-小唐…）。"""
    team = team_key_from_name(session_name) or team_key_from_name(session_id)
    if not team:
        return []
    out = []
    for name in _watched_group_names(groups):
        if "组长" not in name:
            continue
        if team_key_from_name(name) != team:
            continue
        if name not in out:
            out.append(name)
    return out


def resolve_group_leaders(
    messages: list | None = None,
    *,
    session_name: str = "",
    session_id: str = "",
    groups: dict | None = None,
) -> list[str]:
    """本群发言组长 + 同组已关注会话名中的组长，去重保序。"""
    out = []
    sess = str(session_name or "").strip()
    if sess and "组长" in sess and sess not in out:
        out.append(sess)
    for who in leaders_from_messages(messages):
        if who not in out:
            out.append(who)
    for who in leaders_from_watched_sessions(sess, session_id=session_id, groups=groups):
        if who not in out:
            out.append(who)
    return out


def notify_group_issues(
    messages: list,
    *,
    session_id: str = "",
    session_name: str = "",
    leader_messages: list | None = None,
) -> int:
    """规则3：关注群新消息命中重要问题关键词时 @ 组长。

    关键词扫 messages；组长优先取本群发言昵称，再按组键（如申报一组）
    从已关注会话名里匹配含「组长」的会话，提取手机号走 mentioned_mobile_list。
    """
    from .issue_watch import scan_messages
    iw = cfg().get("issueWatch") if isinstance(cfg().get("issueWatch"), dict) else {}
    if not iw.get("enabled"):
        return 0
    hits = scan_messages(messages, iw.get("keywords"))
    if not hits:
        return 0
    kws, lines = [], []
    mids = []
    for h in hits[:8]:
        for k in h.get("keywords") or []:
            if k not in kws:
                kws.append(k)
        who = sender_of(h, "群成员")
        when = str(h.get("time") or "").strip()
        body = str(h.get("text") or "").replace("\n", " ").strip()[:80]
        lines.append(who + ((" " + when) if when else "") + "：" + body)
        mid = str(h.get("messageId") or "")
        if mid:
            mids.append(mid)
    more = len(hits) - min(len(hits), 8)
    if more > 0:
        lines.append("另有 " + str(more) + " 条未列出")
    lines.insert(0, "命中：" + "、".join(kws[:12]))
    sess = str(session_name or session_id or "").strip()
    leaders = resolve_group_leaders(
        leader_messages if leader_messages is not None else messages,
        session_name=session_name or sess,
        session_id=session_id,
    )
    mobiles = mobiles_from_leader_names(leaders)
    if leaders:
        lines.insert(0, " ".join("@" + n for n in leaders[:8]))
    title = "群内重要问题"
    if sess:
        title = title + " · " + sess[:40]
    lv = str(iw.get("level") or "P0").upper()
    if lv not in _LEVEL_RANK:
        lv = "P0"
    got = emit(
        level=lv,
        kind="group_issue",
        title=title,
        lines=lines,
        url=_admin_url("/wecom"),
        department="",
        dedupe_key="group_issue|" + str(session_id or sess) + "|" + ",".join(mids)[:80],
        mentioned_mobile=mobiles,
    )
    return len(hits) if got.get("ok") or got.get("skipped") == "deduped" else 0


def test_send() -> dict:
    return emit(
        level="P1",
        kind="test",
        title="申报书系统告警测试",
        lines=["这是一条测试消息。收到即表示群消息推送已接通。"],
        force=True,
        dedupe_key="test|" + str(int(time.time())),
    )


def _format(level: str, title: str, lines: list, url: str) -> str:
    out = ["【" + level + "】" + str(title or "告警")]
    for ln in lines or []:
        s = str(ln or "").strip()
        if s:
            out.append(s)
    if url:
        out.append("处理：" + url)
    return "\n".join(out)[:2000]


def _mentioned(department: str) -> list:
    dept = str(department or "").strip()
    hit, default = [], []
    for row in cfg().get("leaders") or []:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("userid") or row.get("mentioned") or "").strip()
        if not uid:
            continue
        d = str(row.get("department") or "").strip()
        if d in ("*", "default", ""):
            default.append(uid)
        elif dept and (d == dept or d in dept or dept in d):
            hit.append(uid)
    return hit or default


def _task_url(tid: str) -> str:
    if not tid:
        return ""
    base = str(cfg().get("publicBaseUrl") or "").strip().rstrip("/")
    path = "/t/" + tid
    return (base + path) if base else path


def _admin_url(hash_part: str) -> str:
    base = str(cfg().get("publicBaseUrl") or "").strip().rstrip("/")
    part = str(hash_part or "/overview")
    mapping = {"#sec-feedback": "/feedback", "#sec-wecom": "/wecom"}
    if part in mapping:
        part = mapping[part]
    if not part.startswith("/"):
        part = "/" + part
    if not part.startswith("/admin"):
        part = "/admin" + part
    return (base + part) if base else part


def _owner_dept(owner: str) -> str:
    name = str(owner or "").strip()
    if not name:
        return ""
    try:
        from . import db
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT department FROM users WHERE username=%s LIMIT 1", (name,))
                row = cur.fetchone() or {}
            return str(row.get("department") or "")
        finally:
            conn.close()
    except Exception:
        return ""


def _load() -> dict:
    if not _STORE.exists():
        return {"sent": [], "logs": []}
    try:
        obj = json.loads(_STORE.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {"sent": [], "logs": []}
    except Exception:
        return {"sent": [], "logs": []}


def _save(obj: dict):
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, _STORE)


def _recent(key: str, minutes: int) -> bool:
    if minutes <= 0:
        return False
    cut = time.time() - minutes * 60
    with _LOCK:
        data = _load()
        sent = [x for x in (data.get("sent") or []) if float(x.get("at") or 0) >= cut]
        data["sent"] = sent[-200:]
        _save(data)
        return any(str(x.get("key") or "") == key for x in sent)


def _remember(key: str):
    with _LOCK:
        data = _load()
        sent = list(data.get("sent") or [])
        sent.append({"key": key, "at": time.time()})
        data["sent"] = sent[-200:]
        _save(data)


def _append_log(row: dict):
    item = dict(row)
    item["at"] = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    with _LOCK:
        data = _load()
        logs = list(data.get("logs") or [])
        logs.insert(0, item)
        data["logs"] = logs[:80]
        _save(data)
