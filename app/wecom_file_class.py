"""AI 解读发现文件后，用前后聊天上下文区分人才附件类别。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .attachments import KINDS, classify_talent_filename
from .config import DATA_DIR, LLM_TIMEOUT_CLASSIFY, atomic_replace, resolve_gemini
from .llm import LlmError, chat, extract_json_lenient
from .wecom_cases import _fname, _has_file, _msg_kind

FILE_TYPES = [
    ("app", "申报书"),
    ("opinion", "修改意见"),
] + [(k["id"], k["label"]) for k in KINDS] + [("other", "其他")]
TYPE_IDS = {k for k, _ in FILE_TYPES}
TYPE_LABEL = {k: lab for k, lab in FILE_TYPES}
LABEL_TO_ID = {lab: k for k, lab in FILE_TYPES}
CAT_ORDER = [lab for _, lab in FILE_TYPES]

_RADIUS = 4
_MAX_LLM_FILES = 60
_CACHE_DIR = DATA_DIR / "data" / "wecom_file_types"

_EXPLICIT_APP = re.compile(r"申报书|申请表|application\s*form", re.I)
_STRONG_OPINION_NAME = re.compile(r"修改意见|审核意见|辅导意见|反馈意见|批注|修改说明")
_WEAK_OPINION_NAME = re.compile(r"会议|纪要|评审|辅导|对各区")
_AMBIGUOUS_NAME = re.compile(
    r"^(?:新)?(?:文件|附件|扫描件|材料|文档|doc|file)?[\s_\-]*$", re.I
)

_CUE = [
    ("opinion", re.compile(r"(修改意见|审核意见|辅导意见|评审意见|这[是份]意见|按(?:此|该|以上)?意见改)")),
    ("app", re.compile(r"(这[是份](?:新)?申报书|新申报书|更新(?:一版)?申报书|^申报书$|申请表)")),
    ("passport", re.compile(r"(护照扫描|护照复印|这[是份]护照|护照照片|passport)", re.I)),
    ("id_doc", re.compile(r"(身份证|永居证|身份证明)")),
    ("education", re.compile(r"(学历证明|学位证|毕业证|学历材料|diploma)", re.I)),
    ("work_proof", re.compile(r"(工作证明|在职证明|任职证明|海外工作证明|工作经历证明)")),
    ("intent", re.compile(r"(意向协议|意向书|引进协议)")),
    ("equity", re.compile(r"(股权证明)")),
    ("paper", re.compile(r"(论文全文|论文\s*PDF|代表性论著)", re.I)),
    ("photo", re.compile(r"(证件照|一寸照|白底照)")),
    ("sign", re.compile(r"(电子签|签字扫描|签名扫描)")),
    ("project", re.compile(r"(项目证明|立项批文|立项证明|科研项目证明)")),
    ("conversion", re.compile(r"(成果转化证明|成果转化)")),
]
_CUE_SOFT = [
    ("opinion", re.compile(r"(修改意见|审核意见|辅导意见|这[是份]意见)")),
    ("passport", re.compile(r"(护照(?!号)|passport)", re.I)),
    ("id_doc", re.compile(r"(身份证|永居证|身份证明)")),
    ("education", re.compile(r"(学历|学位证|毕业证|diploma)", re.I)),
    ("work_proof", re.compile(r"(工作证明|在职证明|任职证明|工作经历)")),
    ("intent", re.compile(r"(意向协议|意向书|引进协议)")),
    ("equity", re.compile(r"(股权证明|股权)")),
    ("paper", re.compile(r"(论文全文|论文)", re.I)),
    ("photo", re.compile(r"(证件照|一寸照|白底照)")),
    ("sign", re.compile(r"(电子签|签字扫描|签名)")),
    ("project", re.compile(r"(项目证明|立项批文|立项证明)")),
    ("conversion", re.compile(r"(成果转化)")),
    ("app", re.compile(r"(申报书|申请表)")),
]

_PROMPT = """你是人才申报材料分类助手。根据「文件名」和该文件前后几条企业微信聊天，判断文件类型。

只允许下面这些类型（type 必须是 id）：
""" + "\n".join(f"- {k} = {lab}" for k, lab in FILE_TYPES) + """

规则：
- 只依据输入，禁止编造文件名或类型
- 文件名含「申报书/申请表」时优先申报书，除非聊天明确说这是模板/清单/协议
- 人名.pdf / 公司名.docx 这种短名，必须看前后聊天：有人说护照/意见/学历/工作证明就按聊天判
- 「会议纪要、评审表、辅导安排、统计名单」不是修改意见，除非聊天明确说这是修改意见
- 含「项目」但不含立项/项目证明时，不要判成项目证明
- 拿不准就 other
- id 必须来自输入

只输出 JSON：
{"items":[{"id":1,"type":"passport","reason":"前一条说这是护照扫描件"}]}

输入：
"""


def _cache_path(session_id: str) -> Path:
    sid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "").strip())[:120] or "unknown"
    return _CACHE_DIR / (sid + ".json")


def cache_key(message_id, filename: str) -> str:
    return str(message_id or "") + "|" + str(filename or "")


def save_type_cache(session_id: str, items: list) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": sid,
        "items": [
            {
                "messageId": it.get("messageId"),
                "filename": it.get("filename") or "",
                "type": it.get("type") or "other",
                "label": it.get("label") or TYPE_LABEL.get(it.get("type") or "", "其他"),
                "reason": it.get("reason") or "",
                "source": it.get("source") or "filename",
            }
            for it in (items or [])
            if isinstance(it, dict)
        ],
    }
    path = _cache_path(sid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, path)


def load_type_cache(session_id: str) -> dict:
    path = _cache_path(session_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for it in (data.get("items") or []) if isinstance(data, dict) else []:
        if not isinstance(it, dict):
            continue
        kid = _norm_type(it.get("type") or it.get("label") or "")
        if not kid:
            continue
        key = cache_key(it.get("messageId"), it.get("filename") or "")
        out[key] = {
            "type": kid,
            "label": TYPE_LABEL[kid],
            "reason": str(it.get("reason") or "")[:160],
            "source": str(it.get("source") or "gemini"),
        }
    return out


def apply_type_cache(session_id: str, items: list) -> list:
    cached = load_type_cache(session_id)
    if not cached:
        return items
    out = []
    for it in items or []:
        row = dict(it)
        hit = cached.get(cache_key(row.get("messageId"), row.get("filename") or ""))
        if hit:
            row["type"] = hit["type"]
            row["label"] = hit["label"]
            row["reason"] = hit.get("reason") or row.get("reason") or ""
            row["source"] = hit.get("source") or "gemini"
        out.append(row)
    return out


def _norm_type(raw) -> str:
    s = str(raw or "").strip()
    if s in TYPE_IDS:
        return s
    if s in LABEL_TO_ID:
        return LABEL_TO_ID[s]
    low = s.lower()
    for kid, lab in FILE_TYPES:
        if low == kid.lower() or s == lab:
            return kid
    return ""


def _line(m: dict) -> str:
    who = str(m.get("sender") or "未知").strip()
    when = str(m.get("time_text") or m.get("time") or "").strip()
    fn = _fname(m)
    text = re.sub(r"\s+", " ", str(m.get("text") or m.get("snippet") or "")).strip()[:180]
    bits = [when, who + "："]
    if fn:
        bits.append("[文件 " + fn + "]")
    if text and text not in (fn,):
        bits.append(text)
    return " ".join(x for x in bits if x).strip()


def gather_context(messages: list, index: int, radius: int = _RADIUS) -> dict:
    n = len(messages or [])
    i = int(index)
    before, after = [], []
    for j in range(max(0, i - radius), i):
        before.append(_line(messages[j]))
    for j in range(i + 1, min(n, i + 1 + radius)):
        after.append(_line(messages[j]))
    m = messages[i] if 0 <= i < n else {}
    caption = re.sub(r"\s+", " ", str(m.get("text") or m.get("snippet") or "")).strip()[:240]
    sender = str(m.get("sender") or "")
    if 0 <= i + 1 < n:
        nxt = messages[i + 1]
        if str(nxt.get("sender") or "") == sender and not _has_file(nxt):
            extra = re.sub(r"\s+", " ", str(nxt.get("text") or nxt.get("snippet") or "")).strip()[:180]
            if extra and extra not in caption:
                caption = (caption + " " + extra).strip()[:300]
    blob = "\n".join([x for x in before + ([caption] if caption else []) + after if x])
    return {"caption": caption, "before": before, "after": after, "blob": blob}


def _cue_hit(text: str, *, soft: bool = False) -> tuple[str, str]:
    s = str(text or "")
    if not s.strip():
        return "", ""
    for kid, pat in (_CUE_SOFT if soft else _CUE):
        if pat.search(s):
            return kid, TYPE_LABEL[kid]
    return "", ""


def _cue_from_context(ctx: dict) -> tuple[str, str, str]:
    caption = str((ctx or {}).get("caption") or "")
    cap_id, cap_lab = _cue_hit(caption, soft=True)
    if cap_id:
        return cap_id, cap_lab, "caption"
    after = list((ctx or {}).get("after") or [])
    before = list((ctx or {}).get("before") or [])
    for line in after[:2] + list(reversed(before[-2:])):
        kid, lab = _cue_hit(line, soft=True)
        if kid:
            return kid, lab, "near"
    kid, lab = _cue_hit(str((ctx or {}).get("blob") or ""), soft=False)
    if kid:
        return kid, lab, "blob"
    return "", "", ""


def _filename_kind(name: str) -> tuple[str, str, str]:
    """只靠文件名：强关键词用 KINDS.keys，不用「项目」「证件」这种短目录别名。"""
    name = str(name or "").strip()
    if not name:
        return "other", "其他", "filename"
    if _EXPLICIT_APP.search(name):
        return "app", "申报书", "filename"
    if _STRONG_OPINION_NAME.search(name):
        return "opinion", "修改意见", "filename"
    hit_id, hit_label, hit_len = "", "", 0
    low = name.lower()
    for item in KINDS:
        for kw in item["keys"]:
            token = str(kw or "").strip()
            if len(token) < 2 or token.lower() not in low:
                continue
            if len(token) > hit_len:
                hit_id, hit_label, hit_len = item["id"], item["label"], len(token)
    if hit_id:
        return hit_id, hit_label, "filename"
    cid, _lab = classify_talent_filename(name)
    if cid == "opinion" and _STRONG_OPINION_NAME.search(name):
        return "opinion", "修改意见", "filename"
    if cid == "app" and _EXPLICIT_APP.search(name):
        return "app", "申报书", "filename"
    return "other", "其他", "filename"


def _name_ambiguous(name: str, fname_id: str) -> bool:
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", str(name or "")).strip()
    if fname_id in ("other", "") or _AMBIGUOUS_NAME.match(stem):
        return True
    if fname_id == "app" and not _EXPLICIT_APP.search(name):
        return True
    if fname_id == "opinion" and not _STRONG_OPINION_NAME.search(name):
        return True
    return False


def classify_name_with_context(filename: str, context: dict | str = "") -> dict:
    name = str(filename or "").strip()
    ctx = context if isinstance(context, dict) else {"caption": str(context or ""), "blob": str(context or ""), "before": [], "after": []}
    caption = str(ctx.get("caption") or "")
    fid, flab, _ = _filename_kind(name)
    cue_id, cue_lab, where = _cue_from_context(ctx)

    if _EXPLICIT_APP.search(name):
        return {"type": "app", "label": "申报书", "reason": "文件名含申报书/申请表", "source": "filename"}
    if _STRONG_OPINION_NAME.search(name) and where != "caption":
        return {"type": "opinion", "label": "修改意见", "reason": "文件名含修改/审核意见", "source": "filename"}
    if cue_id and (where == "caption" or _name_ambiguous(name, fid) or fid == "other"):
        why = "文件配文：" + caption[:80] if where == "caption" and caption else ("前后聊天提到" + cue_lab)
        return {"type": cue_id, "label": cue_lab, "reason": why, "source": "context"}
    if fid != "other":
        return {"type": fid, "label": flab, "reason": "文件名关键词", "source": "filename"}
    return {"type": "other", "label": "其他", "reason": "文件名与聊天均无明确材料类型", "source": "filename"}


def list_file_entries(messages: list) -> list:
    out = []
    for i, m in enumerate(messages or []):
        if not isinstance(m, dict) or not _has_file(m):
            continue
        fname = _fname(m)
        if not fname:
            continue
        ctx = gather_context(messages, i)
        hit = classify_name_with_context(fname, ctx)
        out.append({
            "id": len(out) + 1,
            "index": i,
            "messageId": m.get("message_id") or 0,
            "filename": fname,
            "sender": str(m.get("sender") or ""),
            "time": str(m.get("time_text") or m.get("time") or ""),
            "caption": ctx.get("caption") or "",
            "before": ctx.get("before") or [],
            "after": ctx.get("after") or [],
            "type": hit["type"],
            "label": hit["label"],
            "reason": hit["reason"],
            "source": hit["source"],
        })
    return out


def counts_of(items: list) -> dict:
    counts = {}
    for it in items or []:
        lab = str(it.get("label") or "其他")
        counts[lab] = counts.get(lab, 0) + 1
    return {k: counts[k] for k in CAT_ORDER if counts.get(k)}


def _slim_for_llm(entries: list) -> list:
    slim = []
    for it in entries[:_MAX_LLM_FILES]:
        slim.append({
            "id": it["id"],
            "filename": it.get("filename") or "",
            "sender": it.get("sender") or "",
            "time": it.get("time") or "",
            "caption": (it.get("caption") or "")[:200],
            "before": (it.get("before") or [])[-3:],
            "after": (it.get("after") or [])[:3],
            "rule": it.get("type") or "other",
        })
    return slim


async def _gemini_overlay(entries: list) -> tuple[list, str]:
    if not entries:
        return entries, ""
    try:
        prof = resolve_gemini()
    except ValueError as e:
        return entries, str(e)
    slim = _slim_for_llm(entries)
    try:
        resp = await chat(
            [{"role": "user", "content": _PROMPT + json.dumps(slim, ensure_ascii=False)}],
            json_mode=True,
            timeout_s=min(float(prof.get("timeoutSec") or 120), LLM_TIMEOUT_CLASSIFY),
            profile=prof,
            retries=1,
            apply_profile_timeout=False,
        )
        data = extract_json_lenient(resp.get("content") or "")
    except (LlmError, ValueError, TypeError, json.JSONDecodeError) as e:
        return entries, "文件分类模型失败：" + str(e)[:160]
    if not isinstance(data, dict):
        return entries, "文件分类返回无法解析"
    by_id = {int(it["id"]): it for it in entries if it.get("id")}
    n = 0
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        try:
            nid = int(raw.get("id"))
        except (TypeError, ValueError):
            continue
        row = by_id.get(nid)
        if not row:
            continue
        kid = _norm_type(raw.get("type") or raw.get("label") or "")
        if not kid:
            continue
        name = str(row.get("filename") or "")
        if _EXPLICIT_APP.search(name) and kid not in ("app", "other"):
            continue
        row["type"] = kid
        row["label"] = TYPE_LABEL[kid]
        reason = str(raw.get("reason") or "").strip()[:120]
        row["reason"] = reason or ("模型根据聊天上下文判为" + TYPE_LABEL[kid])
        row["source"] = "gemini"
        n += 1
    note = "已按聊天上下文复核 " + str(n) + " 个文件类型" if n else "模型未改写文件类型"
    return entries, note


def cluster_kind_for_file(entry: dict, fallback: str = "ignore") -> str:
    """把材料分类结果收成拆案用的 app / opinion / ignore。"""
    t = str((entry or {}).get("type") or "").strip()
    src = str((entry or {}).get("source") or "").strip()
    name = str((entry or {}).get("filename") or "")
    fb = str(fallback or "ignore")
    if t == "app":
        return "app"
    if t == "opinion":
        return "opinion"
    if t and t not in ("other",):
        return "ignore"
    if src in ("gemini", "context"):
        return "ignore"
    if _WEAK_OPINION_NAME.search(name) and not _STRONG_OPINION_NAME.search(name):
        return "ignore"
    if fb in ("app", "opinion", "text", "ignore", "other"):
        return fb
    return "ignore"


def make_cluster_kind_of(entries: list):
    """值班拆案：有聊天确认时覆盖文件名初判，否则沿用原规则。"""
    by_mid = {}
    for it in entries or []:
        if not isinstance(it, dict):
            continue
        mid = it.get("messageId")
        if mid in (None, "", 0):
            continue
        by_mid[str(mid)] = it

    def kind_of(m):
        fallback = _msg_kind(m)
        if not isinstance(m, dict):
            return fallback
        mid = m.get("message_id")
        hit = by_mid.get(str(mid)) if mid not in (None, "", 0) else None
        if not hit:
            return fallback
        return cluster_kind_for_file(hit, fallback)

    return kind_of


def slim_items(items: list, limit: int = 80) -> list:
    out = []
    for it in (items or [])[:limit]:
        out.append({
            "messageId": it.get("messageId") or 0,
            "filename": it.get("filename") or "",
            "sender": it.get("sender") or "",
            "time": it.get("time") or "",
            "type": it.get("type") or "other",
            "label": it.get("label") or "其他",
            "reason": it.get("reason") or "",
            "source": it.get("source") or "filename",
        })
    return out


async def classify_session_files(
    messages: list,
    *,
    session_id: str = "",
    use_gemini: bool = True,
    keep_entries: bool = False,
) -> dict:
    entries = list_file_entries(messages)
    note = ""
    used = False
    cached = load_type_cache(session_id) if session_id else {}
    covered = 0
    if cached and entries:
        for it in entries:
            hit = cached.get(cache_key(it.get("messageId"), it.get("filename") or ""))
            if not hit:
                continue
            it["type"] = hit["type"]
            it["label"] = hit["label"]
            it["reason"] = hit.get("reason") or it.get("reason") or ""
            it["source"] = hit.get("source") or "gemini"
            covered += 1
    need_gemini = bool(use_gemini and entries and covered < len(entries))
    if need_gemini:
        entries, note = await _gemini_overlay(entries)
        used = any(it.get("source") == "gemini" for it in entries)
        if session_id:
            save_type_cache(session_id, entries)
    elif covered:
        note = "已按聊天上下文分类"
        used = any(it.get("source") == "gemini" for it in entries)
    out = {
        "items": slim_items(entries),
        "counts": counts_of(entries),
        "fileCount": len(entries),
        "geminiNote": note,
        "usedGemini": used,
    }
    if keep_entries:
        out["entries"] = entries
    return out
