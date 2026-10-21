"""AI 解读发现文件后，用前后聊天上下文区分人才附件类别。"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from .attachments import KINDS, classify_talent_filename
from .config import DATA_DIR, LLM_TIMEOUT_CLASSIFY, atomic_replace, resolve_gemini
from .llm import LlmError, chat, extract_json_lenient
from .pdf_app import ALLOWED_APP_EXT, ext_of
from .party import sender_of
from .wecom_cases import _copies, _fname, _has_file, _msg_kind

FILE_TYPES = [
    ("app", "申报书"),
    ("opinion", "修改意见"),
] + [(k["id"], k["label"]) for k in KINDS] + [("other", "其他")]
TYPE_IDS = {k for k, _ in FILE_TYPES}
TYPE_LABEL = {k: lab for k, lab in FILE_TYPES}
LABEL_TO_ID = {lab: k for k, lab in FILE_TYPES}
CAT_ORDER = [lab for _, lab in FILE_TYPES]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
NON_IMAGE_EXTS = {
    ".pdf", ".doc", ".docx", ".docm", ".dot", ".dotx", ".wps", ".wpt",
    ".xls", ".xlsx", ".xlsm", ".xlsb", ".et", ".ett",
    ".ppt", ".pptx", ".dps", ".dpt",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".txt", ".md", ".csv", ".rtf", ".ofd",
    ".mp3", ".mp4", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".amr", ".wma", ".webm",
}
IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def is_image_message(m: dict | None = None, *, filename: str = "", media_url: str = "") -> bool:
    """只有图片才预览。WPS/PDF/Office 等即使带封面 media_url 也不算图片。"""
    row = m if isinstance(m, dict) else {}
    name = str(filename or _fname(row) or "")
    base = file_basename(name) or name
    ext = Path(base).suffix.lower()
    if ext in NON_IMAGE_EXTS:
        return False
    if ext in IMAGE_EXTS:
        return True
    try:
        t = int(row.get("msg_type") or 0)
    except (TypeError, ValueError):
        t = 0
    if t in (4, 15):
        return True
    label = str(row.get("msg_type_label") or "").strip()
    if label in ("图片", "image", "Image", "photo", "Photo"):
        return True
    stem = Path(base).stem.strip() if base else ""
    if stem in ("图片", "image", "Image", "photo", "Photo") or is_screenshot_name(base):
        return True
    media = str(media_url or row.get("media_url") or "").strip()
    if "qlogo.cn" in media.lower() or "/mmhead/" in media.lower():
        media = ""
    # 企业微信原图常叫「图片」且 has_attachment=True，不能因此当成文档封面。
    # 有明确非图片扩展名的已在上面排除；无扩展名但带图片地址的按图片预览。
    return bool(media and not ext)


def image_media_type(filename: str = "", content_type: str = "", content: bytes | None = None) -> str:
    """给内联预览用的图片 MIME；不是图片则空串。"""
    c = str(content_type or "").strip().lower()
    if c.startswith("image/") and "octet-stream" not in c:
        return c
    ext = Path(file_basename(filename) or str(filename or "")).suffix.lower()
    if ext in IMAGE_MIME:
        return IMAGE_MIME[ext]
    head = content[:16] if content else b""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return ""

_RADIUS = 4
_MAX_LLM_FILES = 60
_CACHE_DIR = DATA_DIR / "data" / "wecom_file_types"
_SUMMARY_DIR = DATA_DIR / "data" / "wecom_file_summaries"
_SUMMARY_CACHE: dict[str, dict] = {}
_SUMMARY_CACHE_MAX = 24
_SUMMARY_EPHEMERAL = ("upload", "autoUpload", "fromCache", "incremental", "mode", "newFileCount")

_EXPLICIT_APP = re.compile(r"申报书|申请表|application\s*form", re.I)
_SCREENSHOT_NAME = re.compile(
    r"(企业微信截图|微信截图|屏幕截图|screen\s*shot|screenshot|截图_\d{6,})",
    re.I,
)
_SHOT_FILE = re.compile(
    r"^(企业微信截图|微信截图|屏幕截图)_.+\.(png|jpe?g|gif|webp|bmp)$",
    re.I,
)
_COPY_SFX = re.compile(r"\(\d+\)(?=\.[^.]+$)")
_SHOT_ID = re.compile(r"(?:企业微信截图|微信截图|屏幕截图)_(\d+)", re.I)
_GLOB_META = re.compile(r"[*?\[\]]")


def file_basename(name: str) -> str:
    s = str(name or "").replace("\\", "/").strip()
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    return s


def _safe_file_part(name: str) -> str:
    s = file_basename(name)
    if not s or s in (".", "..") or _GLOB_META.search(s):
        return ""
    return s[:240]


def _path_in_dir(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return None
    try:
        resolved.relative_to(base)
    except ValueError:
        return None
    return resolved


def _temp_dirs() -> list[Path]:
    import os
    import tempfile
    out: list[Path] = []
    seen: set[str] = set()
    for raw in (
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        tempfile.gettempdir(),
        str(Path(os.environ.get("LOCALAPPDATA") or "") / "Temp") if os.environ.get("LOCALAPPDATA") else "",
    ):
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            p = Path(s).resolve()
        except OSError:
            continue
        key = str(p).lower()
        if key in seen or not p.is_dir():
            continue
        seen.add(key)
        out.append(p)
    return out


def screenshot_name_variants(name: str) -> list[str]:
    base = _safe_file_part(name)
    if not base:
        return []
    out = [base]
    stripped = _safe_file_part(_COPY_SFX.sub("", base))
    if stripped and stripped not in out:
        out.append(stripped)
    return out


def resolve_wecom_temp_image(name: str) -> Path | None:
    """企业微信截图常落在本机 Temp，解析服务缓存里没有。只允许 Temp 下的截图文件。"""
    raw = str(name or "").strip().strip('"')
    if not raw:
        return None
    variants = screenshot_name_variants(raw)
    if not variants:
        return None
    looks_shot = any(_SHOT_FILE.match(v) or is_screenshot_name(v) for v in variants)
    if not looks_shot:
        return None
    temps = _temp_dirs()
    candidates: list[Path] = []
    try:
        given = Path(raw)
    except (OSError, ValueError):
        given = None
    if given is not None and given.is_absolute():
        candidates.append(given)
        alt = _COPY_SFX.sub("", given.name)
        if alt and alt != given.name:
            candidates.append(given.with_name(alt))
    for folder in temps:
        for v in variants:
            if Path(v).suffix.lower() not in IMAGE_EXTS:
                continue
            candidates.append(folder / v)

    def _ok(path: Path) -> Path | None:
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if not resolved.is_file():
            return None
        try:
            if resolved.stat().st_size < 32:
                return None
        except OSError:
            return None
        parent = resolved.parent
        if not any(parent == t or t in parent.parents for t in temps):
            return None
        return resolved

    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        hit = _ok(cand)
        if hit:
            return hit
    return None


def resolve_wecom_cached_file(source_id: str = "", message_id=None, name: str = "") -> Path | None:
    """解析服务已同步到本机的 files/{message_id}_文件名，预览不必再等远端回传。"""
    from . import wecom_local as L
    root = L.data_root()
    if not root:
        return None
    try:
        mid = int(message_id or 0)
    except (TypeError, ValueError):
        mid = 0
    variants = screenshot_name_variants(name)
    folders: list[Path] = []
    seen: set[str] = set()

    def add_src(src: Path) -> None:
        folder = src / "files"
        try:
            resolved_folder = folder.resolve() if folder.exists() else None
            base = root.resolve()
        except OSError:
            return
        if resolved_folder is None or not resolved_folder.is_dir():
            return
        try:
            resolved_folder.relative_to(base)
        except ValueError:
            return
        key = str(resolved_folder).lower()
        if key in seen:
            return
        seen.add(key)
        folders.append(resolved_folder)

    sid = str(source_id or "").strip()
    if sid and sid not in ("*", "all", "local") and "/" not in sid and "\\" not in sid and ".." not in sid:
        add_src(root / sid)
    try:
        for src in root.iterdir():
            if src.is_dir():
                add_src(src)
    except OSError:
        pass

    def accept(path: Path, folder: Path) -> bool:
        resolved = _path_in_dir(path, folder)
        if resolved is None:
            return False
        try:
            if not resolved.is_file() or resolved.stat().st_size < 32:
                return False
        except OSError:
            return False
        ext = resolved.suffix.lower()
        return ext in IMAGE_EXTS or is_screenshot_name(resolved.name)

    if mid > 0:
        prefix = str(mid) + "_"
        for folder in folders:
            for v in variants:
                cand = folder / (prefix + v)
                if accept(cand, folder):
                    return cand
            try:
                for p in folder.glob(prefix + "*"):
                    if accept(p, folder):
                        return p
            except OSError:
                continue
    for v in variants:
        if not v:
            continue
        for folder in folders:
            try:
                for p in folder.glob("*_" + v):
                    if accept(p, folder):
                        return p
            except OSError:
                continue
    shot_id = ""
    for v in variants:
        hit = _SHOT_ID.search(v or "")
        if hit:
            shot_id = hit.group(1)
            break
    if shot_id:
        for folder in folders:
            try:
                for p in folder.iterdir():
                    hit2 = _SHOT_ID.search(p.name)
                    if hit2 and hit2.group(1) == shot_id and accept(p, folder):
                        return p
            except OSError:
                continue
    return None


def pick_cached_copy(filename: str, copies: list | None = None, message_id=None) -> tuple[str, int]:
    """选一个本机 files/ 里真正有字节的电脑副本，避免用未缓存的 message_id 去预览。"""
    tried: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def add(sid, mid) -> None:
        try:
            n = int(mid or 0)
        except (TypeError, ValueError):
            n = 0
        key = (str(sid or ""), n)
        if key in seen:
            return
        seen.add(key)
        tried.append(key)

    add("", message_id)
    for c in copies or []:
        if isinstance(c, dict):
            add(c.get("source_id"), c.get("message_id"))
    fname = str(filename or "")
    for sid, mid in tried:
        path = resolve_wecom_cached_file(sid, mid, fname)
        if path is None:
            continue
        use_sid, use_mid = sid, mid
        head = path.name.split("_", 1)[0]
        if head.isdigit():
            use_mid = int(head)
        if not use_sid:
            try:
                use_sid = path.parent.parent.name
            except Exception:
                use_sid = ""
        return use_sid, use_mid
    path = resolve_wecom_cached_file("", 0, fname) if fname else None
    if path is not None:
        mid = 0
        head = path.name.split("_", 1)[0]
        if head.isdigit():
            mid = int(head)
        try:
            sid = path.parent.parent.name
        except Exception:
            sid = ""
        return sid, mid
    return "", 0


def stamp_cached_file_rows(groups: list) -> list:
    """文件汇总行改用本机已有缓存的 messageId，预览才能打中 files/{mid}_文件名。"""
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        files = []
        for f in g.get("files") or []:
            if not isinstance(f, dict):
                files.append(f)
                continue
            row = dict(f)
            copies = list(row.get("copies") or [])
            sid, mid = pick_cached_copy(str(row.get("filename") or ""), copies, row.get("messageId"))
            if mid:
                row["messageId"] = mid
            if sid:
                row["cacheSourceId"] = sid
                head = [c for c in copies if isinstance(c, dict) and str(c.get("source_id") or "") == sid]
                rest = [c for c in copies if not (isinstance(c, dict) and str(c.get("source_id") or "") == sid)]
                row["copies"] = head + rest
            files.append(row)
        ng = dict(g)
        ng["files"] = files
        out.append(ng)
    return out


def is_screenshot_name(name: str) -> bool:
    s = str(name or "")
    base = file_basename(s)
    if _SCREENSHOT_NAME.search(s) or _SCREENSHOT_NAME.search(base):
        return True
    low = s.replace("/", "\\").lower()
    if "\\temp\\" in low and ext_of(base) in IMAGE_EXTS:
        return True
    return False


def can_be_app_file(name: str) -> bool:
    """申报书只认 Word/Excel/PDF，截图和图片不能当申报书版本。"""
    if is_screenshot_name(name):
        return False
    base = file_basename(name)
    ext = ext_of(base) or ext_of(name)
    if ext in IMAGE_EXTS:
        return False
    return ext in ALLOWED_APP_EXT


_STRONG_OPINION_NAME = re.compile(r"修改意见|审核意见|辅导意见|反馈意见|批注|修改说明")
_WEAK_OPINION_NAME = re.compile(r"会议|纪要|评审|辅导|对各区")
_AMBIGUOUS_NAME = re.compile(
    r"^(?:新)?(?:文件|附件|扫描件|材料|文档|doc|file)?[\s_\-]*$", re.I
)

_CUE = [
    ("opinion", re.compile(r"(修改意见|审核意见|辅导意见|评审意见|反馈意见|这[是份]意见|按(?:此|该|以上)?意见改|请按(?:此|该)?意见)")),
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
    ("opinion", re.compile(r"(修改意见|审核意见|辅导意见|反馈意见|这[是份]意见)")),
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
- 「会议纪要、评审表、辅导安排、统计名单、智能表格/文档」不是修改意见，除非聊天明确说这是修改意见
- 「这是护照/学历扫描件」「请查收申报书」「收到」不是修改意见；要有请改/请补/缺某字段才算意见
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
    """按 messageId|filename 合并写入，避免日期窗子集覆盖掉其它文件的分类。"""
    sid = str(session_id or "").strip()
    if not sid:
        return
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    merged = load_type_cache(sid)
    for it in items or []:
        if not isinstance(it, dict):
            continue
        fname = str(it.get("filename") or "")
        kid = _norm_type(it.get("type") or it.get("label") or "") or "other"
        merged[cache_key(it.get("messageId"), fname)] = {
            "messageId": it.get("messageId"),
            "filename": fname,
            "type": kid,
            "label": TYPE_LABEL.get(kid, str(it.get("label") or "其他")),
            "reason": str(it.get("reason") or "")[:160],
            "source": str(it.get("source") or "filename"),
        }
    payload = {
        "session_id": sid,
        "items": [
            {
                "messageId": row.get("messageId"),
                "filename": row.get("filename") or "",
                "type": row.get("type") or "other",
                "label": row.get("label") or TYPE_LABEL.get(row.get("type") or "", "其他"),
                "reason": row.get("reason") or "",
                "source": row.get("source") or "filename",
            }
            for row in merged.values()
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
        mid = it.get("messageId")
        fname = str(it.get("filename") or "")
        key = cache_key(mid, fname)
        out[key] = {
            "messageId": mid,
            "filename": fname,
            "type": kid,
            "label": TYPE_LABEL[kid],
            "reason": str(it.get("reason") or "")[:160],
            "source": str(it.get("source") or "gemini"),
        }
    return out


def _entry_can_be_app(it: dict | None) -> bool:
    row = it if isinstance(it, dict) else {}
    name = str(row.get("filename") or "")
    if row.get("isImage") or is_screenshot_name(name) or not can_be_app_file(name):
        return False
    return True


def sanitize_classified_entry(it: dict | None) -> dict | None:
    if not isinstance(it, dict):
        return it
    if str(it.get("type") or "") == "app" and not _entry_can_be_app(it):
        it["type"] = "other"
        it["label"] = "其他"
        it["reason"] = "截图或图片不能作为申报书"
    return it


def apply_type_cache(session_id: str, items: list) -> list:
    cached = load_type_cache(session_id)
    if not cached:
        return [sanitize_classified_entry(dict(it)) if isinstance(it, dict) else it for it in (items or [])]
    out = []
    for it in items or []:
        row = dict(it)
        hit = cached.get(cache_key(row.get("messageId"), row.get("filename") or ""))
        if hit and not (str(hit.get("type") or "") == "app" and not _entry_can_be_app(row)):
            row["type"] = hit["type"]
            row["label"] = hit["label"]
            row["reason"] = hit.get("reason") or row.get("reason") or ""
            row["source"] = hit.get("source") or "gemini"
        sanitize_classified_entry(row)
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
    who = sender_of(m, "未知")
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
    sender = sender_of(m)
    if 0 <= i + 1 < n:
        nxt = messages[i + 1]
        if sender_of(nxt) == sender and not _has_file(nxt):
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
        # 邻条附件名不含本文件类型；全量扫描时后面的「申报书.pdf」不应改写当前文件
        if "[文件 " in str(line or ""):
            continue
        kid, lab = _cue_hit(line, soft=True)
        if kid:
            return kid, lab, "near"
    blob = str((ctx or {}).get("blob") or "")
    blob_text = "\n".join(
        ln for ln in blob.splitlines() if "[文件 " not in ln
    ) or blob
    kid, lab = _cue_hit(blob_text, soft=False)
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
    if not can_be_app_file(name):
        if fid == "app":
            fid, flab = "other", "其他"
        if cue_id == "app":
            cue_id, cue_lab, where = "", "", ""
    if cue_id == "app":
        if where != "caption":
            cue_id, cue_lab, where = "", "", ""
        else:
            strong, _ = _cue_hit(caption, soft=False)
            if strong != "app":
                cue_id, cue_lab, where = "", "", ""

    if _EXPLICIT_APP.search(name) and can_be_app_file(name):
        return {"type": "app", "label": "申报书", "reason": "文件名含申报书/申请表", "source": "filename"}
    if _STRONG_OPINION_NAME.search(name) and where != "caption":
        return {"type": "opinion", "label": "修改意见", "reason": "文件名含修改/审核意见", "source": "filename"}
    if cue_id and (where == "caption" or _name_ambiguous(name, fid) or fid == "other"):
        why = "文件配文：" + caption[:80] if where == "caption" and caption else ("前后聊天提到" + cue_lab)
        src = "caption" if where == "caption" else "context"
        return {"type": cue_id, "label": cue_lab, "reason": why, "source": src}
    if fid != "other":
        return {"type": fid, "label": flab, "reason": "文件名关键词", "source": "filename"}
    return {"type": "other", "label": "其他", "reason": "文件名与聊天均无明确材料类型", "source": "filename"}


def list_file_entries(messages: list) -> list:
    out = []
    for i, m in enumerate(messages or []):
        if not isinstance(m, dict):
            continue
        media = str(m.get("media_url") or "").strip()
        if not _has_file(m) and not media:
            continue
        fname = _fname(m)
        if not fname:
            if not media:
                continue
            fname = "图片"
        ctx = gather_context(messages, i)
        hit = classify_name_with_context(fname, ctx)
        out.append({
            "id": len(out) + 1,
            "index": i,
            "messageId": m.get("message_id") or 0,
            "filename": fname,
            "sender": sender_of(m),
            "time": str(m.get("time_text") or m.get("time") or ""),
            "caption": ctx.get("caption") or "",
            "before": ctx.get("before") or [],
            "after": ctx.get("after") or [],
            "type": hit["type"],
            "label": hit["label"],
            "reason": hit["reason"],
            "source": hit["source"],
            "mediaUrl": media,
            "copies": _copies(m),
            "isImage": is_image_message(m, filename=fname, media_url=media),
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
            "sender": sender_of(it),
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
        if kid == "app" and not can_be_app_file(name):
            continue
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
        if entry.get("isImage") or not can_be_app_file(name):
            return "ignore"
        if _EXPLICIT_APP.search(name) or fb == "app" or src == "caption":
            return "app"
        return "ignore"
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
            "sender": sender_of(it),
            "time": it.get("time") or "",
            "type": it.get("type") or "other",
            "label": it.get("label") or "其他",
            "reason": it.get("reason") or "",
            "source": it.get("source") or "filename",
            "mediaUrl": str(it.get("mediaUrl") or it.get("media_url") or ""),
            "copies": list(it.get("copies") or [])[:8],
            "isImage": bool(it.get("isImage") or is_image_message(
                filename=str(it.get("filename") or ""),
                media_url=str(it.get("mediaUrl") or it.get("media_url") or ""),
            )),
        })
    return out


def session_data_stamp(session_id: str, *, start_date: str = "", end_date: str = "") -> str:
    """用各电脑消息文件的 mtime/size 做轻量签名；文件未变则可复用汇总结果。"""
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    parts = [sid, str(start_date or "")[:10], str(end_date or "")[:10]]
    try:
        from . import wecom_local as L
    except Exception:
        return "|".join(parts)
    if not L.available():
        return "|".join(parts)
    try:
        srcs = L.list_sources()
    except Exception:
        return "|".join(parts)
    for src in srcs or []:
        if not isinstance(src, dict):
            continue
        source_id = str(src.get("id") or "")
        path = L._msg_path(source_id, sid) if source_id else None
        if not path:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        parts.append(source_id + ":" + str(st.st_mtime_ns) + ":" + str(st.st_size))
    return "|".join(parts)


def _id_rank(message_id) -> tuple:
    s = str(message_id or "")
    try:
        return (int(s), s)
    except ValueError:
        return (0, s)


def msg_rank(message: dict) -> tuple:
    row = message if isinstance(message, dict) else {}
    return (str(row.get("time_text") or row.get("time") or ""),) + _id_rank(row.get("message_id") or row.get("messageId"))


def after_cursor(message: dict, last_time: str, last_id: str) -> bool:
    if not last_time and not last_id:
        return True
    return msg_rank(message) > (str(last_time or ""),) + _id_rank(last_id)


def cursor_of(messages: list) -> tuple[str, str]:
    newest = None
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if newest is None or msg_rank(m) > msg_rank(newest):
            newest = m
    if not newest:
        return "", ""
    return str(newest.get("time_text") or newest.get("time") or ""), str(newest.get("message_id") or newest.get("messageId") or "")


def incremental_work_window(messages: list, last_time: str, last_id: str, radius: int = _RADIUS) -> tuple[int, list]:
    """第一条新消息的下标；没有新消息时等于 len(messages)。返回 (first_new, 含上下文的工作窗口)。"""
    rows = [m for m in (messages or []) if isinstance(m, dict)]
    first_new = len(rows)
    for i, m in enumerate(rows):
        if after_cursor(m, last_time, last_id):
            first_new = i
            break
    ctx_start = max(0, first_new - max(0, int(radius or 0)))
    return first_new, rows[ctx_start:]


def file_row_key(it: dict) -> tuple:
    row = it if isinstance(it, dict) else {}
    return (
        str(row.get("filename") or "").strip(),
        str(row.get("time") or ""),
        sender_of(row),
        str(row.get("messageId") or row.get("message_id") or 0),
    )


def build_file_groups(entries: list, messages: list, *, session_id: str = "", session_name: str = "") -> tuple[list, int]:
    """把分类条目收成页面用的按类分组；copies 取自对应消息。"""
    groups: dict = {}
    order: list = []
    seen = set()
    sid = str(session_id or "").strip()
    sess_name = str(session_name or "")
    for it in entries or []:
        if not isinstance(it, dict):
            continue
        fname = str(it.get("filename") or "").strip()
        mid = it.get("messageId") or 0
        key = file_row_key(it)
        if not fname or key in seen:
            continue
        seen.add(key)
        cid = str(it.get("type") or LABEL_TO_ID.get(it.get("label") or "") or "other")
        label = str(it.get("label") or "其他")
        if cid not in groups:
            groups[cid] = {"id": cid, "label": label, "files": []}
            order.append(cid)
        idx = it.get("index")
        m = messages[idx] if isinstance(idx, int) and 0 <= idx < len(messages) else {}
        raw_copies = []
        if isinstance(m, dict) and m.get("copies"):
            raw_copies = m.get("copies") or []
        elif it.get("copies"):
            raw_copies = it.get("copies") or []
        copies = []
        for c in raw_copies:
            if not isinstance(c, dict):
                continue
            copies.append({
                "source_id": str(c.get("source_id") or ""),
                "source_label": str(c.get("source_label") or c.get("label") or ""),
                "message_id": c.get("message_id") or mid,
                "session_id": str(c.get("session_id") or sid),
            })
        media = str((m or {}).get("media_url") or it.get("mediaUrl") or "")
        groups[cid]["files"].append({
            "filename": fname,
            "sender": sender_of(it),
            "time": str(it.get("time") or ""),
            "messageId": mid,
            "copies": copies,
            "sessionId": sid,
            "sessionName": sess_name,
            "reason": str(it.get("reason") or ""),
            "source": str(it.get("source") or ""),
            "mediaUrl": media,
            "isImage": bool(it.get("isImage") or is_image_message(
                m if isinstance(m, dict) else None,
                filename=fname,
                media_url=media,
            )),
        })
    rank = {lab: i for i, lab in enumerate(CAT_ORDER)}
    order = sorted(order, key=lambda cid: rank.get((groups[cid] or {}).get("label") or "", 99))
    return [groups[k] for k in order], len(seen)


def refresh_group_image_flags(groups: list) -> list:
    """旧缓存可能缺 isImage / mediaUrl，按现规则补一遍，避免汇总里图片格子出不来。"""
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        files = []
        for f in g.get("files") or []:
            if not isinstance(f, dict):
                continue
            row = dict(f)
            media = str(row.get("mediaUrl") or row.get("media_url") or "")
            if media and not row.get("mediaUrl"):
                row["mediaUrl"] = media
            row["isImage"] = bool(row.get("isImage") or is_image_message(
                filename=str(row.get("filename") or ""),
                media_url=media,
            ))
            files.append(row)
        ng = dict(g)
        ng["files"] = files
        out.append(ng)
    return out


def merge_file_groups(old_groups: list, extra_groups: list) -> list:
    """把增量扫到的新文件并进已有分类，按 messageId|文件名去重。"""
    by_id: dict = {}
    order: list = []
    seen = set()
    for g in list(old_groups or []) + list(extra_groups or []):
        if not isinstance(g, dict):
            continue
        cid = str(g.get("id") or LABEL_TO_ID.get(g.get("label") or "") or "other")
        if cid not in by_id:
            by_id[cid] = {"id": cid, "label": str(g.get("label") or "其他"), "files": []}
            order.append(cid)
        for f in g.get("files") or []:
            if not isinstance(f, dict):
                continue
            key = file_row_key(f)
            if not key[0] or key in seen:
                continue
            seen.add(key)
            by_id[cid]["files"].append(dict(f))
    rank = {lab: i for i, lab in enumerate(CAT_ORDER)}
    order = sorted(order, key=lambda cid: rank.get((by_id[cid] or {}).get("label") or "", 99))
    return [by_id[k] for k in order]


def counts_from_groups(groups: list) -> dict:
    items = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        lab = str(g.get("label") or "其他")
        for _ in g.get("files") or []:
            items.append({"label": lab})
    return counts_of(items)


def _summary_key(session_id: str, start_date: str = "", end_date: str = "") -> str:
    return "\t".join([
        str(session_id or "").strip(),
        str(start_date or "")[:10],
        str(end_date or "")[:10],
    ])


def _summary_path(session_id: str, start_date: str = "", end_date: str = "") -> Path:
    sid = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id or "").strip())[:120] or "unknown"
    a = re.sub(r"[^0-9]", "", str(start_date or "")[:10])
    b = re.sub(r"[^0-9]", "", str(end_date or "")[:10])
    name = sid if not (a or b) else sid + "_" + a + "_" + b
    return _SUMMARY_DIR / (name + ".json")


def _strip_summary_result(payload: dict) -> dict:
    out = dict(payload or {})
    for key in _SUMMARY_EPHEMERAL:
        out.pop(key, None)
    return out


def load_summary_state(session_id: str, start_date: str = "", end_date: str = "") -> dict | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    key = _summary_key(sid, start_date, end_date)
    hit = _SUMMARY_CACHE.get(key)
    if isinstance(hit, dict) and hit.get("result"):
        return dict(hit)
    path = _summary_path(sid, start_date, end_date)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("result"), dict):
        return None
    state = {
        "session_id": sid,
        "start_date": str(start_date or "")[:10],
        "end_date": str(end_date or "")[:10],
        "stamp": str(data.get("stamp") or ""),
        "last_time": str(data.get("last_time") or ""),
        "last_message_id": str(data.get("last_message_id") or ""),
        "full_done": bool(data.get("full_done")),
        "result": dict(data.get("result") or {}),
    }
    _SUMMARY_CACHE[key] = state
    return dict(state)


def save_summary_state(
    session_id: str,
    *,
    start_date: str = "",
    end_date: str = "",
    stamp: str = "",
    result: dict | None = None,
    messages: list | None = None,
    last_time: str = "",
    last_message_id: str = "",
    full_done: bool = True,
) -> None:
    sid = str(session_id or "").strip()
    if not sid or not isinstance(result, dict):
        return
    cur_time, cur_id = cursor_of(messages or [])
    state = {
        "session_id": sid,
        "start_date": str(start_date or "")[:10],
        "end_date": str(end_date or "")[:10],
        "stamp": str(stamp or ""),
        "last_time": str(cur_time or last_time or ""),
        "last_message_id": str(cur_id or last_message_id or ""),
        "full_done": bool(full_done),
        "result": _strip_summary_result(result),
    }
    key = _summary_key(sid, start_date, end_date)
    _SUMMARY_CACHE[key] = state
    if len(_SUMMARY_CACHE) > _SUMMARY_CACHE_MAX:
        for old in list(_SUMMARY_CACHE.keys())[: max(1, len(_SUMMARY_CACHE) - _SUMMARY_CACHE_MAX)]:
            _SUMMARY_CACHE.pop(old, None)
    _SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    path = _summary_path(sid, start_date, end_date)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, path)


def get_summary_cache(session_id: str, stamp: str, start_date: str = "", end_date: str = "") -> dict | None:
    state = load_summary_state(session_id, start_date, end_date)
    if not state or not stamp or str(state.get("stamp") or "") != str(stamp):
        return None
    payload = state.get("result")
    return dict(payload) if isinstance(payload, dict) else None


def put_summary_cache(
    session_id: str,
    stamp: str,
    payload: dict,
    *,
    start_date: str = "",
    end_date: str = "",
    messages: list | None = None,
    last_time: str = "",
    last_message_id: str = "",
    full_done: bool = True,
) -> None:
    save_summary_state(
        session_id,
        start_date=start_date,
        end_date=end_date,
        stamp=stamp,
        result=payload,
        messages=messages,
        last_time=last_time,
        last_message_id=last_message_id,
        full_done=full_done,
    )


async def classify_session_files(
    messages: list,
    *,
    session_id: str = "",
    use_gemini: bool = True,
    keep_entries: bool = False,
    progress=None,
) -> dict:
    async def _p(phase: str, percent: int, label: str, **extra) -> None:
        if not progress:
            return
        ev = {"phase": phase, "percent": max(0, min(100, int(percent))), "label": str(label or "")}
        ev.update(extra)
        out = progress(ev)
        if asyncio.iscoroutine(out):
            await out

    entries = list_file_entries(messages)
    await _p("scan", 32, "发现 " + str(len(entries)) + " 个文件", files=len(entries))
    note = ""
    used = False
    cached = load_type_cache(session_id) if session_id else {}
    covered = 0
    pending = []
    if entries:
        for it in entries:
            hit = cached.get(cache_key(it.get("messageId"), it.get("filename") or "")) if cached else None
            if hit and not (str(hit.get("type") or "") == "app" and not _entry_can_be_app(it)):
                it["type"] = hit["type"]
                it["label"] = hit["label"]
                it["reason"] = hit.get("reason") or it.get("reason") or ""
                it["source"] = hit.get("source") or "gemini"
                covered += 1
            else:
                pending.append(it)
        for it in entries:
            sanitize_classified_entry(it)
    if use_gemini and pending:
        await _p(
            "classify", 40,
            "正在用模型复核分类（" + str(len(pending)) + " 个待确认）",
            files=len(pending),
        )
        pending, note = await _gemini_overlay(pending)
        for it in entries:
            sanitize_classified_entry(it)
        used = any(it.get("source") == "gemini" for it in pending)
        if session_id:
            save_type_cache(session_id, entries)
        if covered and note:
            note = "新文件已复核；其余沿用缓存。 " + note
        elif covered and not note:
            note = "新文件已分类；其余沿用缓存"
        await _p("classify", 72, "文件分类完成", files=len(entries))
    elif covered:
        note = "已按聊天上下文分类"
        used = any(it.get("source") == "gemini" for it in entries)
        if session_id and pending:
            save_type_cache(session_id, entries)
        await _p("classify", 70, "沿用已分类缓存", files=len(entries))
    elif session_id and entries:
        save_type_cache(session_id, entries)
        await _p("classify", 70, "已按规则分类", files=len(entries))
    else:
        await _p("classify", 70, "没有待分类文件" if not entries else "已按规则分类", files=len(entries))
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
