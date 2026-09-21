"""从企业微信群消息拆出申报书，并把附近的修改意见（群文本或意见文档）挂到同一案。"""
from __future__ import annotations
import hashlib
import re
from datetime import datetime, timedelta
from .pdf_app import ALLOWED_APP_EXT, ext_of
from .opinion_extract import ALLOWED_OPINION_EXT
from . import matcher as M
from .wecom_locate import extract_keys, locate_tasks, parse_time

_APP_NAME = re.compile(r"申报书|申请表|application\s*form", re.I)
_OPINION_NAME = re.compile(r"意见|反馈|审核|批注|辅导|会议|纪要|修改说明|评审|对各区")
_DONE_NAME = re.compile(r"修改后|备份|对照表|遗留事项")
_NOT_FORM = re.compile(
    r"需要提供|提供的资料|资料清单|材料清单|填表须知|填表说明|附件清单|"
    r"申报须知|通知|指南|模板|模版|企业需要|应提供|需提供|"
    r"意向书|意向协议|意向合同|聘用|劳动合同|合同范本|协议书|协议|合同|"
    r"承诺书|承诺函|承诺|唯一申报|"
    r"推荐信|简历|护照|学历|身份证|户口|证件照|营业执照|"
    r"供应统计|引进名单|汇总表|摸排表|走访表|人才统计",
)
_OPINION_DOC = {".docx", ".docm", ".wps", ".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".md"}
_OPINION_TEXT = re.compile(
    r"请改|请把|请补|请核|缺[少了]|补充|修改|护照|学历|论文|工作计划|"
    r"附件|对照|申报书|人才编号|单位名称|英文名|按意见|按审核",
)
_CHATTER = re.compile(r"^(收到|好的|谢谢|嗯|ok|OK|对|是|好)$")


def strip_cache_prefix(name: str, message_id=None) -> str:
    """企业微信缓存常把 message_id 接到原名前：69230_人才引进意向协议书-杜垚.pdf。"""
    n = str(name or "").replace("\\", "/").split("/")[-1].strip()
    mid = str(message_id or "").strip()
    if mid.isdigit() and n.startswith(mid + "_"):
        rest = n[len(mid) + 1 :]
        if rest:
            return rest
    return n


def _fname(m: dict) -> str:
    return str((m or {}).get("attachment_name") or (m or {}).get("filename") or "").strip()


def _names_of(m: dict) -> list:
    out, seen = [], set()
    for raw in (
        (m or {}).get("attachment_name"),
        (m or {}).get("filename"),
        (m or {}).get("cache_name"),
    ):
        n = str(raw or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _is_not_form(name: str) -> bool:
    n = str(name or "").strip()
    if not n or _DONE_NAME.search(n):
        return False
    return bool(_NOT_FORM.search(n) and not _APP_NAME.search(n) and not _OPINION_NAME.search(n))


def _has_file(m: dict) -> bool:
    if not isinstance(m, dict):
        return False
    if m.get("has_attachment") and _fname(m):
        return True
    copies = m.get("copies") if isinstance(m.get("copies"), list) else []
    return bool(_fname(m) and copies)


def _copies(m: dict) -> list:
    raw = m.get("copies") if isinstance(m, dict) and isinstance(m.get("copies"), list) else []
    out = []
    for c in raw:
        if isinstance(c, dict) and c.get("source_id") and c.get("message_id"):
            out.append(c)
    return out


def _person_app_stem(name: str) -> bool:
    """文件名几乎只有人名：张三.pdf / SANIYA ARFIN.pdf。带承诺、护照等附加字的不算。"""
    n = str(name or "").strip()
    ext = ext_of(n)
    if ext not in ALLOWED_APP_EXT:
        return False
    stem = n[: -len(ext)] if ext and n.lower().endswith(ext) else n
    stem = re.sub(r"[_（）()\[\]【】]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem or _DONE_NAME.search(stem) or _OPINION_NAME.search(stem) or _PERSON_SKIP.search(stem):
        return False
    if _NOT_FORM.search(stem) and not _APP_NAME.search(stem):
        return False
    full, toks = M.accept_person_name(stem)
    if not (full or toks):
        return False
    rest = stem
    for t in toks:
        if t:
            rest = re.sub(re.escape(t), " ", rest, flags=re.I)
    if full:
        rest = re.sub(re.escape(full), " ", rest, flags=re.I)
    rest = re.sub(r"申报书|申请表|application\s*form", " ", rest, flags=re.I)
    rest = re.sub(r"[A-Za-z0-9\s\-_.·•]+", "", rest)
    return not rest.strip()


_PERSON_SKIP = re.compile(r"签章|盖章|单位|扫描|截图|附件|材料|清单|通知|说明|推荐|承诺|破格|协议|合同")
_TAIL_SKIP = re.compile(r"签章|盖章|单位|扫描|截图|附件|材料|清单|通知|说明|推荐|承诺|破格|协议|合同|青年|人才|创新|项目|公司")
_FORM_PDF_HINT = re.compile(r"有限公司|股份有限|实验室|青年人才|申报书|申请表")


def _tail_person(name: str) -> str:
    """从「宁波+公司+杜垚」这类文件名里取出末尾人名。"""
    stem = str(name or "")
    ext = ext_of(stem)
    if ext and stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    parts = re.split(r"[\s_\-–—+＋]+", stem)
    for p in reversed(parts):
        p = re.sub(r"[()（）\[\]【】]", "", str(p or "")).strip()
        if not p or _TAIL_SKIP.search(p):
            continue
        full, toks = M.accept_person_name(p)
        if (full or toks) and 2 <= len(p) <= 6:
            return p
    return ""


def _company_person_form(name: str) -> bool:
    """地区+企业+人名 的申报书导出 PDF，如 宁波+某某有限公司+杜垚.pdf。"""
    n = str(name or "").strip()
    if ext_of(n) not in ALLOWED_APP_EXT:
        return False
    if _DONE_NAME.search(n) or _is_not_form(n):
        return False
    if not _FORM_PDF_HINT.search(n):
        return False
    return bool(_tail_person(n))


def _classify_one(name: str) -> str:
    """申报书 → app；意见文档 → opinion；资料清单、意向书模板等既不当申报书也不当意见。"""
    n = str(name or "").strip()
    ext = ext_of(n)
    if not n or _DONE_NAME.search(n):
        return "ignore"
    if _is_not_form(n):
        return "ignore"
    if _APP_NAME.search(n) and ext in ALLOWED_APP_EXT:
        return "app"
    if _company_person_form(n):
        return "app"
    if _person_app_stem(n):
        return "app"
    if _OPINION_NAME.search(n):
        if ext in ALLOWED_OPINION_EXT or ext in ALLOWED_APP_EXT:
            return "opinion"
        return "ignore"
    if ext == ".pdf":
        return "ignore"
    if ext in _OPINION_DOC:
        return "opinion"
    return "ignore"


def classify_file(*names, message_id=None) -> str:
    """多个文件名（聊天显示名 + 缓存原名）一起判。意向协议等否定名压过「杜垚.pdf」这种人名短名。"""
    bag, seen = [], set()
    for raw in names:
        n = str(raw or "").strip()
        if not n:
            continue
        for cand in (n, strip_cache_prefix(n, message_id)):
            key = cand.lower()
            if cand and key not in seen:
                seen.add(key)
                bag.append(cand)
    if not bag:
        return "ignore"
    explicit_app = False
    person = False
    not_form = False
    opinion = False
    for n in bag:
        k = _classify_one(n)
        if _is_not_form(n):
            not_form = True
        if k == "app" and _APP_NAME.search(n):
            explicit_app = True
        elif k == "app":
            person = True
        if k == "opinion":
            opinion = True
    if explicit_app:
        return "app"
    if not_form:
        return "ignore"
    if person:
        return "app"
    if opinion:
        return "opinion"
    return "ignore"


def resolve_app_upload(chat_name: str, cache_name: str, message_id=None) -> dict:
    """下载到缓存文件后复核：聊天短名像申报书、原名却是意向协议时拒绝建任务。"""
    chat = str(chat_name or "").strip()
    cache = str(cache_name or "").strip()
    stripped = strip_cache_prefix(cache, message_id) if cache else ""
    kind = classify_file(chat, cache, stripped, message_id=message_id)
    shown = stripped or cache or chat or "文件"
    if kind != "app":
        return {
            "ok": False,
            "kind": kind,
            "filename": shown,
            "detail": "缓存文件实际是「" + shown + "」，不是申报书（意向协议、护照、简历等），未创建任务",
        }
    store = chat or stripped or cache
    if stripped and _classify_one(stripped) == "app":
        store = stripped
    elif chat and _classify_one(chat) == "app":
        store = chat
    else:
        store = stripped or cache or chat
    return {"ok": True, "kind": "app", "filename": store}


def split_scan_stats(messages: list) -> dict:
    """预览用：两千条消息里实际扫到多少文件、几份申报书。"""
    files = [m for m in (messages or []) if isinstance(m, dict) and _has_file(m)]
    apps, opinions, ignored = [], [], []
    for m in files:
        fn = _fname(m)
        kind = classify_file(*_names_of(m), message_id=m.get("message_id"))
        if kind == "app":
            apps.append(fn)
        elif kind == "opinion":
            opinions.append(fn)
        else:
            ignored.append(fn)
    return {
        "fileCount": len(files),
        "appFileCount": len(apps),
        "opinionFileCount": len(opinions),
        "ignoredFileCount": len(ignored),
        "ignoredSample": ignored[:8],
    }


def is_shared_opinion(name: str) -> bool:
    n = str(name or "")
    return bool(re.search(r"对各区|审核反馈|智能制造办", n) or (
        _OPINION_NAME.search(n) and ext_of(n) in {".xlsx", ".xlsm", ".xls", ".csv"}
    ))


def is_opinion_text(text: str) -> bool:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) < 8 or len(s) > 4000:
        return False
    if _CHATTER.match(s):
        return False
    if re.fullmatch(r"https?://\S+", s, re.I):
        return False
    return bool(_OPINION_TEXT.search(s))


def _msg_kind(m: dict) -> str:
    if _has_file(m):
        return classify_file(*_names_of(m), message_id=m.get("message_id"))
    if is_opinion_text(m.get("text") or m.get("snippet") or ""):
        return "text"
    return "other"


def _case_id(session_id: str, m: dict) -> str:
    raw = "|".join([
        str(session_id or ""),
        str(m.get("message_id") or ""),
        _fname(m),
        str(m.get("time_text") or m.get("time") or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:12]


def _file_ref(m: dict, role: str) -> dict:
    keys = extract_keys({
        "filename": _fname(m),
        "text": m.get("text") or "",
        "time_text": m.get("time_text") or m.get("time") or "",
        "sender": m.get("sender") or "",
        "session_name": m.get("session_name") or "",
    })
    return {
        "role": role,
        "kind": "file",
        "filename": _fname(m),
        "time": str(m.get("time_text") or m.get("time") or ""),
        "sender": str(m.get("sender") or ""),
        "message_id": m.get("message_id") or 0,
        "copies": _copies(m),
        "names": keys.get("names") or [],
        "nums": keys.get("nums") or [],
        "cached": bool(_copies(m)),
    }


def _text_ref(m: dict) -> dict:
    text = str(m.get("text") or m.get("snippet") or "").strip()
    return {
        "role": "opinion",
        "kind": "text",
        "filename": "群聊修改意见.txt",
        "time": str(m.get("time_text") or m.get("time") or ""),
        "sender": str(m.get("sender") or "群消息"),
        "message_id": m.get("message_id") or 0,
        "copies": [],
        "text": text[:4000],
        "cached": True,
        "names": [],
        "nums": [],
    }


def _attach(case: dict, ref: dict) -> None:
    key = (ref.get("kind"), ref.get("filename"), ref.get("message_id"), (ref.get("text") or "")[:80])
    seen = {(x.get("kind"), x.get("filename"), x.get("message_id"), (x.get("text") or "")[:80]) for x in case["opinions"]}
    if key in seen:
        return
    case["opinions"].append(ref)


def _merge_texts(case: dict) -> None:
    texts = [o for o in case["opinions"] if o.get("kind") == "text"]
    files = [o for o in case["opinions"] if o.get("kind") != "text"]
    if not texts:
        return
    parts = []
    for o in texts:
        head = "发送人：" + str(o.get("sender") or "") + "　时间：" + str(o.get("time") or "")
        parts.append(head + "\n" + str(o.get("text") or "").strip())
    files.append({
        "role": "opinion",
        "kind": "text",
        "filename": "群聊修改意见.txt",
        "time": texts[0].get("time") or "",
        "sender": texts[0].get("sender") or "",
        "message_id": texts[0].get("message_id") or 0,
        "copies": [],
        "text": "\n\n".join(parts)[:12000],
        "cached": True,
        "names": [],
        "nums": [],
    })
    case["opinions"] = files


def cluster_messages(messages: list, *, session_id: str = "", session_name: str = "", window_hours: int = 48, kind_of=None) -> list:
    """申报书一案；附近的意见文档和群聊正文挂上去。"""
    try:
        window_hours = int(window_hours or 48)
    except (TypeError, ValueError):
        window_hours = 48
    window_hours = max(6, min(window_hours, 168))
    span = timedelta(hours=window_hours)
    rows = []
    for i, m in enumerate(messages or []):
        if not isinstance(m, dict):
            continue
        dt = parse_time(m.get("time_text") or m.get("time")) or datetime.min
        rows.append((dt, i, m))
    rows.sort(key=lambda x: (x[0], x[1]))

    cases = []
    current = None
    pending: list[tuple[datetime, dict]] = []
    shared: list[tuple[datetime, dict]] = []

    def in_window(a: datetime, b: datetime) -> bool:
        if a is datetime.min or b is datetime.min:
            return True
        return abs(a - b) <= span

    def new_case(dt: datetime, m: dict) -> dict:
        app = _file_ref(m, "app")
        keys = extract_keys({
            "filename": app["filename"],
            "text": m.get("text") or "",
            "time_text": app["time"],
            "sender": app["sender"],
            "session_name": session_name,
        })
        return {
            "id": _case_id(session_id, m),
            "sessionId": session_id,
            "sessionName": session_name,
            "app": app,
            "opinions": [],
            "names": keys.get("names") or [],
            "nums": keys.get("nums") or [],
            "ready": bool(app.get("cached")),
            "warnings": [] if app.get("cached") else ["申报书文件未在各电脑缓存，无法自动上传"],
            "_dt": dt,
        }

    def attach_pending(case: dict, dt: datetime) -> None:
        keep = []
        for pdt, pref in pending:
            if in_window(dt, pdt):
                _attach(case, pref)
            else:
                keep.append((pdt, pref))
        pending[:] = keep[-20:]

    def attach_shared(case: dict, dt: datetime) -> None:
        for sdt, sm in shared:
            if in_window(dt, sdt):
                _attach(case, _file_ref(sm, "opinion"))

    for dt, _i, m in rows:
        kind = kind_of(m) if callable(kind_of) else _msg_kind(m)
        if kind not in ("app", "opinion", "text", "ignore", "other"):
            kind = _msg_kind(m)
        if kind == "app":
            case = new_case(dt, m)
            attach_pending(case, dt)
            attach_shared(case, dt)
            cases.append(case)
            current = case
            continue
        if kind == "opinion":
            ref = _file_ref(m, "opinion")
            if is_shared_opinion(_fname(m)):
                shared.append((dt, m))
                for c in cases:
                    if in_window(c.get("_dt") or dt, dt):
                        _attach(c, ref)
                if current:
                    _attach(current, ref)
            elif current and in_window(current.get("_dt") or dt, dt):
                _attach(current, ref)
            else:
                pending.append((dt, ref))
            continue
        if kind == "text":
            ref = _text_ref(m)
            if current and in_window(current.get("_dt") or dt, dt):
                _attach(current, ref)
            else:
                pending.append((dt, ref))

    for c in cases:
        _refresh_case(c)
    return cases


def _refresh_case(c: dict) -> dict:
    c.pop("_dt", None)
    _merge_texts(c)
    ops = (c.get("opinions") or [])[:12]
    c["opinions"] = ops
    texts = [o for o in ops if o.get("kind") == "text"]
    files = [o for o in ops if o.get("kind") != "text"]
    c["warnings"] = [w for w in (c.get("warnings") or []) if "意见文档未缓存" not in w and "附近没有识别到修改意见" not in w]
    missing = [o["filename"] for o in files if not o.get("cached")]
    if missing:
        c["warnings"].append("意见文档未缓存：" + "、".join(missing[:4]))
    if not files and not texts:
        c["warnings"].append("附近没有识别到修改意见（群文本或意见文档），将尝试从申报书标注栏提取")
    app_fn = str((c.get("app") or {}).get("filename") or "")
    if app_fn and _person_app_stem(app_fn) and not _APP_NAME.search(app_fn):
        short = "文件名几乎只有人名，上传时会按企业微信缓存原名复核；若实际是意向协议、护照等则不会建任务"
        if short not in c["warnings"]:
            c["warnings"].append(short)
    c["opinionCount"] = len(ops)
    c["opinionFiles"] = [o["filename"] for o in files]
    c["opinionTexts"] = len(texts)
    return c


def annotate_existing(cases: list, runner) -> list:
    """标出已经建过或定位到过的任务，避免重复上传。"""
    seen = {}
    if runner:
        for t in runner.list_meta() or []:
            w = t.get("wecom") if isinstance(t.get("wecom"), dict) else {}
            wid = str(w.get("caseId") or t.get("wecomCaseId") or "")
            if wid:
                seen[wid] = t
    for c in cases:
        hit = seen.get(c.get("id") or "")
        if hit:
            c["existing"] = {
                "id": hit.get("id"),
                "status": hit.get("status"),
                "url": "/t/" + str(hit.get("id") or ""),
            }
            c["warnings"] = list(c.get("warnings") or []) + ["已从该群文件建过任务，仍可重新上传"]
            continue
        if not runner:
            continue
        loc = locate_tasks(runner, {
            "filename": (c.get("app") or {}).get("filename") or "",
            "time_text": (c.get("app") or {}).get("time") or "",
            "session_name": c.get("sessionName") or "",
            "windowDays": 7,
        })
        top = (loc.get("items") or [{}])[0] if loc.get("items") else {}
        if top.get("confidence") == "high" and top.get("id"):
            c["similar"] = {
                "id": top.get("id"),
                "status": top.get("status"),
                "url": top.get("url") or ("/t/" + str(top.get("id"))),
                "score": top.get("score"),
            }
            c["warnings"] = list(c.get("warnings") or []) + ["系统里已有较接近的修改任务，仍可再传一条"]
    return cases


def opinion_txt_bytes(item: dict) -> bytes:
    body = str(item.get("text") or "").strip()
    if "发送人：" in body[:20]:
        return body.encode("utf-8")
    sender = str(item.get("sender") or "")
    when = str(item.get("time") or "")
    return ("发送人：" + sender + "\n时间：" + when + "\n\n" + body).encode("utf-8")
