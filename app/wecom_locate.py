"""从群聊消息抽取键，对申报书修改任务打分定位。"""
from __future__ import annotations
import re
from datetime import datetime, timedelta
from . import matcher as M
from .llm import created_key
from .pdf_app import is_backup_output, is_edited_output
from .pool import _norm_name, talent_attach_id

_FILE_NAME = re.compile(
    r"([^\n[\]/\\]+?\.(?:zip|rar|7z|pdf|xlsx?|docx?|pptx?|png|jpe?g|gif|webp|bmp|mp4|txt|md))",
    re.I,
)
_LATIN_NAME = re.compile(r"\b[A-Z][A-Za-z'.\-]+(?:\s+[A-Z][A-Za-z'.\-]+){0,4}\b")
_CN_NAME = re.compile(r"[\u4e00-\u9fa5]{2,6}")
_DATE_SEP = re.compile(r"[./\-]")


def _stem(name: str) -> str:
    n = str(name or "").replace("\\", "/").split("/")[-1].strip()
    i = n.rfind(".")
    return n[:i] if i > 0 else n


def _tokens(s: str) -> list:
    parts = re.split(r"[\s_\-+＋,，.．/\\（）()]+", str(s or ""))
    out = []
    for p in parts:
        p = p.strip()
        if len(p) < 2:
            continue
        if p.lower() in ("申报书", "修改后", "备份", "对照表", "docx", "pdf", "xlsx", "word"):
            continue
        out.append(p)
    return out


def parse_time(raw) -> datetime | None:
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace("年", "-").replace("月", "-").replace("日", " ").replace("T", " ")
    m = re.search(
        r"(?:(\d{4})[./\-])?(\d{1,2})[./\-](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        s,
    )
    if not m:
        return None
    year = int(m.group(1) or 0)
    if not year:
        now = datetime.now()
        year = now.year
    try:
        return datetime(
            year,
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4) or 0),
            int(m.group(5) or 0),
            int(m.group(6) or 0),
        )
    except ValueError:
        return None


def _task_time(t: dict) -> tuple[datetime | None, str]:
    for key in ("appliedAt", "finishedAt", "createdAt"):
        dt = parse_time(t.get(key))
        if dt:
            return dt, key
    ck = created_key(t.get("appliedAt") or t.get("finishedAt") or t.get("createdAt"))
    if ck[0]:
        return datetime(*ck), "createdAt"
    return None, ""


_EXT_TAIL = re.compile(
    r"\.(?:zip|rar|7z|pdf|xlsx?|docx?|pptx?|png|jpe?g|gif|webp|bmp|mp4|txt|md)$",
    re.I,
)


def extract_keys(body: dict) -> dict:
    body = body if isinstance(body, dict) else {}
    text = str(body.get("text") or body.get("snippet") or "")
    fname = str(body.get("filename") or body.get("attachment_name") or "").strip()
    if not fname:
        hit = _FILE_NAME.search(text)
        if hit:
            fname = str(hit.group(1) or "").strip()
    group = str(body.get("session_name") or body.get("group") or "")
    blob = " ".join(x for x in (_stem(fname), text) if x)
    names, seen = [], set()

    def add_name(raw: str) -> None:
        raw = _EXT_TAIL.sub("", str(raw or "").strip())
        if not raw:
            return
        full, toks = M.accept_person_name(raw)
        label = raw if (full or toks) else ""
        if not label:
            return
        k = _norm_name(label)
        if k and k not in seen and len(k) >= 2:
            seen.add(k)
            names.append(label)

    add_name(_stem(fname))
    for m in _LATIN_NAME.finditer(blob):
        add_name(m.group(0))
    for m in _CN_NAME.finditer(blob):
        add_name(m.group(0))
    nums = []
    for src in (fname, text):
        for n in M.extract_book_nums(src):
            if n not in nums:
                nums.append(n)
    return {
        "filename": fname,
        "names": names[:8],
        "nums": nums[:6],
        "time": str(body.get("time") or body.get("time_text") or "").strip(),
        "sender": str(body.get("sender") or "").strip(),
        "group": str(body.get("group") or body.get("session_name") or "").strip(),
        "text": text[:400],
    }


def identity_of(t: dict) -> dict:
    app = t.get("app") if isinstance(t.get("app"), dict) else {}
    person = str(app.get("personName") or t.get("personName") or "").strip()
    aid = str(app.get("attachId") or t.get("attachId") or app.get("no") or "").strip()
    hit = t.get("poolHit") if isinstance(t.get("poolHit"), dict) else {}
    tlabel = str(hit.get("talent") or "")
    if tlabel:
        parts = tlabel.split(None, 1)
        head = parts[0] if parts else ""
        if head and re.fullmatch(r"\d{4,6}", head) and not aid:
            aid = head
        if len(parts) > 1 and not person:
            person = parts[1].strip()
    if not person:
        stem = _stem(app.get("name") or "")
        full, toks = M.accept_person_name(stem)
        if toks:
            person = " ".join(toks) if all(re.fullmatch(r"[a-z]+", x) for x in toks) else (toks[0])
        elif full:
            person = full
        else:
            for p in reversed(re.split(r"[\s_\-–—+＋]+", stem)):
                p = re.sub(r"[()（）\[\]【】]", "", str(p or "")).strip()
                if not p or re.search(r"签章|协议|合同|说明|承诺|人才|青年|公司", p):
                    continue
                f, tk = M.accept_person_name(p)
                if (f or tk) and 2 <= len(p) <= 6:
                    person = p
                    break
    return {
        "personName": person,
        "attachId": aid,
        "appName": str(app.get("name") or ""),
        "inputName": str(app.get("inputName") or app.get("name") or ""),
    }


def _name_hit(keys_names: list, task_person: str, app_name: str) -> tuple[bool, str]:
    cands = [task_person, _stem(app_name), app_name]
    kn = [_norm_name(x) for x in keys_names if _norm_name(x)]
    tn = [_norm_name(x) for x in cands if _norm_name(x)]
    if not kn or not tn:
        return False, ""
    for a in kn:
        for b in tn:
            if not a or not b:
                continue
            need = 2 if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", a or b or "") else 4
            if a == b or (len(a) >= need and a in b) or (len(b) >= need and b in a):
                return True, keys_names[kn.index(a)] if a in kn else task_person
    kt = set()
    for n in keys_names:
        _, toks = M.accept_person_name(n)
        kt.update(toks)
        kt.add(_norm_name(n))
    tt = set()
    for n in cands:
        _, toks = M.accept_person_name(_stem(n))
        tt.update(toks)
        tt.add(_norm_name(n))
    inter = {x for x in kt if x and len(x) >= 4} & {x for x in tt if x and len(x) >= 4}
    if inter:
        return True, next(iter(inter))
    return False, ""


def _file_overlap(fname: str, app_name: str) -> tuple[int, str]:
    def keep(tok: str) -> bool:
        n = _norm_name(tok)
        if not n:
            return False
        if re.fullmatch(r"[\u4e00-\u9fa5]{2,}", n):
            return True
        return len(n) >= 3
    a = {_norm_name(x) for x in _tokens(fname) if keep(x)}
    b = {_norm_name(x) for x in _tokens(app_name) if keep(x)}
    if not a or not b:
        return 0, ""
    hit = a & b
    if not hit:
        return 0, ""
    return min(25, 8 * len(hit)), "、".join(sorted(hit)[:4])


def _num_hit(nums: list, aid: str, app_no: str) -> tuple[bool, str]:
    want = {str(x) for x in nums if x}
    have = {str(x) for x in (aid, app_no) if x}
    inter = want & have
    if inter:
        return True, next(iter(inter))
    for n in want:
        for h in have:
            if n and h and (n in h or h in n) and min(len(n), len(h)) >= 4:
                return True, n
    return False, ""


def score_task(t: dict, keys: dict, *, window_days: int = 7, msg_dt: datetime | None = None) -> dict | None:
    ident = identity_of(t)
    app = t.get("app") if isinstance(t.get("app"), dict) else {}
    reasons = []
    score = 0
    name_ok, name_v = _name_hit(keys.get("names") or [], ident["personName"], ident["appName"])
    if name_ok:
        score += 40
        reasons.append("姓名命中 " + name_v)
    num_ok, num_v = _num_hit(keys.get("nums") or [], ident["attachId"], str(app.get("no") or ""))
    if num_ok:
        score += 35
        reasons.append("编号命中 " + num_v)
    fscore, fhit = _file_overlap(keys.get("filename") or "", ident["appName"] or ident["inputName"])
    if fscore:
        score += fscore
        reasons.append("文件名接近 " + fhit)
    time_ok = False
    tdt, tsrc = _task_time(t)
    if msg_dt and tdt:
        delta = abs((msg_dt - tdt).total_seconds())
        days = delta / 86400.0
        if days <= window_days:
            time_ok = True
            prox = max(0, int(round(20 * (1 - days / max(window_days, 0.01)))))
            score += prox
            if days < 1:
                reasons.append("同一天（" + tsrc + "）")
            else:
                reasons.append("时间差 " + str(round(days, 1)) + " 天（" + tsrc + "）")
        else:
            score -= 8
    group = str(keys.get("group") or "")
    if group and ident["appName"] and _norm_name(group) and _file_overlap(group, ident["appName"])[0]:
        score += 5
        reasons.append("群名与申报书名有交集")
    dls = t.get("deliverables") or []
    has_edited = any(is_edited_output(o.get("name") if isinstance(o, dict) else "") for o in dls)
    st = str(t.get("status") or "")
    if t.get("appliedAt") and has_edited:
        score += 10
        reasons.append("已确认写入")
    elif st == "planned":
        score -= 4
        reasons.append("仅有计划未写入")
    elif st == "failed":
        score -= 8
        reasons.append("任务失败")
    if score < 12 or (not name_ok and not num_ok and not fscore):
        return None
    if name_ok and time_ok:
        conf = "high"
    elif (name_ok or num_ok) and (time_ok or fscore):
        conf = "high" if score >= 60 else "medium"
    elif name_ok or num_ok or fscore:
        conf = "medium"
    else:
        conf = "low"
    files = []
    for o in dls:
        if not isinstance(o, dict):
            continue
        n = str(o.get("name") or "")
        kind = "other"
        if is_edited_output(n):
            kind = "edited"
        elif is_backup_output(n):
            kind = "backup"
        elif "对照表" in n:
            kind = "report"
        files.append({"name": n, "size": o.get("size") or 0, "kind": kind})
    vers = t.get("versions") if isinstance(t.get("versions"), list) else []
    return {
        "id": t.get("id"),
        "score": int(score),
        "confidence": conf,
        "reasons": reasons,
        "status": st,
        "appName": ident["appName"],
        "personName": ident["personName"],
        "attachId": ident["attachId"],
        "createdAt": t.get("createdAt") or "",
        "appliedAt": t.get("appliedAt") or "",
        "finishedAt": t.get("finishedAt") or "",
        "owner": t.get("owner") or "",
        "appliedBy": t.get("appliedBy") or "",
        "url": "/t/" + str(t.get("id") or ""),
        "deliverables": files,
        "versions": vers[-5:],
        "nameHit": name_ok,
        "timeHit": time_ok,
    }


def locate_tasks(runner, body: dict) -> dict:
    keys = extract_keys(body if isinstance(body, dict) else {})
    try:
        window = int((body or {}).get("windowDays") or 7)
    except (TypeError, ValueError):
        window = 7
    window = max(1, min(window, 30))
    msg_dt = parse_time(keys.get("time"))
    items = []
    for t in runner.list_meta() if runner else []:
        row = score_task(t, keys, window_days=window, msg_dt=msg_dt)
        if row:
            full = runner.get(row["id"]) if hasattr(runner, "get") else None
            if isinstance(full, dict) and isinstance(full.get("versions"), list):
                row["versions"] = full.get("versions")[-5:]
            items.append(row)
    items.sort(key=lambda x: (x.get("score") or 0, x.get("appliedAt") or x.get("createdAt") or ""), reverse=True)
    top = items[:5]
    return {
        "ok": True,
        "keys": keys,
        "windowDays": window,
        "count": len(top),
        "items": top,
        "hint": "" if top else "没有足够接近的修改任务。可核对姓名、文件名或放大时间窗。",
    }


def pick_copy_locate(copy: dict | None, *, fallback_session_id: str = "", fallback_session_name: str = "") -> dict:
    """从 copies 条目选出定位用的 session_id + message_id。"""
    c = copy if isinstance(copy, dict) else {}
    sid = str(c.get("session_id") or c.get("sessionId") or fallback_session_id or "").strip()
    sname = str(c.get("session_name") or c.get("sessionName") or fallback_session_name or "").strip()
    mid = str(c.get("message_id") or c.get("messageId") or "").strip()
    return {
        "sessionId": sid,
        "sessionName": sname,
        "messageId": mid,
        "time": str(c.get("time") or c.get("time_text") or "").strip(),
    }


def fill_app_identity(t: dict, snap: dict | None = None) -> None:
    """把封面/库命中的姓名与人才编号写进任务 app，供列表与定位使用。"""
    if not isinstance(t, dict):
        return
    app = t.setdefault("app", {})
    if not isinstance(app, dict):
        return
    app["inputName"] = str(app.get("name") or app.get("inputName") or "")
    snap = snap if isinstance(snap, dict) else {}
    talent = snap.get("talent") if isinstance(snap.get("talent"), dict) else {}
    keys = snap.get("keys") if isinstance(snap.get("keys"), dict) else {}
    names = []
    for n in [talent.get("name"), talent.get("real_name"), talent.get("realName")] + list(keys.get("names") or []):
        s = str(n or "").strip()
        if s and s not in names:
            names.append(s)
    if not names:
        full, toks = M.accept_person_name(_stem(app.get("name") or ""))
        if toks:
            names.append(" ".join(x.title() if x.islower() else x for x in toks))
        elif full:
            names.append(full)
    if names and not app.get("personName"):
        app["personName"] = names[0]
    aid = talent_attach_id(talent) or str((keys.get("attachIds") or [None])[0] or "") or str(app.get("no") or "")
    if aid and not app.get("attachId"):
        app["attachId"] = aid
    if not app.get("no") and aid:
        app["no"] = aid
