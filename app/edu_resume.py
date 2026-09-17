"""修改意见指出教育信息缺失时：从人才库记录 / 原始简历抽取教育经历。"""
from __future__ import annotations
import json, re
from pathlib import Path

from . import attachments as ATT
from .opinion_extract import IMAGE_EXT, WORD_EXT, ensure_txt as extract_to_txt, image_to_text

MISSING_RE = re.compile(
    r"(教育(经历|信息|背景|栏|表格?)?|学历(信息|经历|栏)?|院校|学位|毕业院校)"
    r".{0,16}(缺失|缺少|未填|未写|空白|空缺|不完整|未提供|请补充|须补充|需补充|完善|补齐|补全|补写)"
    r"|(缺|缺少|缺失|未填|未写|空白|空缺|不完整).{0,16}"
    r"(教育(经历|信息|背景|栏)?|学历|院校|学位|毕业院校)"
    r"|补充教育经历|完善教育经历|教育经历表(格)?(为空|空白|未填)",
    re.I,
)
RESUME_RE = re.compile(r"简历|个人履历|resume|\bcv\b|curriculum", re.I)
RESUME_SKIP_RE = re.compile(r"学位证|毕业证|学历证明|diploma|transcript|成绩单", re.I)
EDU_HEAD_RE = re.compile(
    r"(教育经历|教育背景|主要学历|学历与学位|学历学位|Educational\s+Background|Education)\s*[:：]?",
    re.I,
)
EDU_END_RE = re.compile(
    r"\n\s*(工作经历|工作背景|职业经历|任职经历|Work\s+Experience|Employment|项目经历|科研项目|代表性成果)\b",
    re.I,
)
LIST_KEYS = (
    "教育经历", "主要学历", "学历经历", "教育背景", "学历",
    "educations", "education", "educational_background", "edu",
)
SCHOOL_KEYS = ("院校", "学校", "毕业院校", "university", "school", "college", "institution")
MAJOR_KEYS = ("专业", "major", "field", "discipline")
DEGREE_KEYS = ("学位", "学历", "degree")
START_KEYS = ("开始时间", "入学时间", "起", "start", "from")
END_KEYS = ("结束时间", "毕业时间", "止", "end", "to")
COUNTRY_KEYS = ("国家", "地区", "country", "region")
EDU_OCR_PROMPT = (
    "这是申报人简历中的一页。请原样抄录教育经历相关文字（起止时间、国家、院校、专业、学位）。"
    "表格用制表符分列。不要翻译、不要总结。"
)


def education_missing(texts) -> bool:
    blob = "\n".join(str(x or "") for x in (texts or []) if str(x or "").strip())
    return bool(blob and MISSING_RE.search(blob))


def extract_from_payload(talent: dict | None) -> list[dict]:
    payload = (talent or {}).get("payload") if isinstance(talent, dict) else None
    rows = []
    _walk_edu(payload, rows)
    return _dedupe_rows(rows)


def _walk_edu(obj, acc: list, depth: int = 0):
    if depth > 8 or obj is None:
        return
    if isinstance(obj, list):
        for x in obj[:40]:
            _walk_edu(x, acc, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    for lk in LIST_KEYS:
        val = obj.get(lk)
        if isinstance(val, list):
            for it in val[:20]:
                row = _row_from(it) if isinstance(it, dict) else _row_from_text(str(it or ""))
                if row:
                    acc.append(row)
        elif isinstance(val, dict):
            row = _row_from(val)
            if row:
                acc.append(row)
        elif isinstance(val, str) and val.strip() and val.strip() not in ("***",) and lk in ("教育经历", "主要学历", "教育背景"):
            row = _row_from_text(val)
            if row:
                acc.append(row)
    row = _row_from(obj)
    if row and (row.get("school") or row.get("degree")):
        acc.append(row)
    for k, v in obj.items():
        if k in LIST_KEYS:
            continue
        if isinstance(v, (dict, list)):
            _walk_edu(v, acc, depth + 1)


def _pick(obj: dict, keys) -> str:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip() not in ("", "***"):
            return str(v).strip()
    return ""


def _row_from(obj: dict) -> dict | None:
    school = _pick(obj, SCHOOL_KEYS)
    major = _pick(obj, MAJOR_KEYS)
    degree = _pick(obj, DEGREE_KEYS)
    start = _pick(obj, START_KEYS)
    end = _pick(obj, END_KEYS)
    country = _pick(obj, COUNTRY_KEYS)
    if not school and not degree:
        return None
    if school and len(school) > 160:
        return None
    if not school and not (start or end or major):
        return None
    return {"start": start, "end": end, "country": country, "school": school, "major": major, "degree": degree}


def _row_from_text(s: str) -> dict | None:
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    if 4 <= len(t) <= 240 and re.search(r"大学|学院|University|College|学士|硕士|博士|本科", t, re.I):
        return {"start": "", "end": "", "country": "", "school": t, "major": "", "degree": ""}
    return None


def _dedupe_rows(rows: list) -> list:
    seen, out = set(), []
    for r in rows or []:
        k = "|".join(str(r.get(x) or "") for x in ("start", "end", "school", "major", "degree"))
        k = re.sub(r"\s+", "", k).lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out[:12]


def _fmt_row(r: dict) -> str:
    time = "–".join(x for x in (str(r.get("start") or "").strip(), str(r.get("end") or "").strip()) if x)
    bits = [time, r.get("country"), r.get("school"), r.get("major"), r.get("degree")]
    return " | ".join(str(x).strip() for x in bits if str(x or "").strip())


def slice_education_text(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    m = EDU_HEAD_RE.search(s)
    if m:
        rest = s[m.start():]
        end = EDU_END_RE.search(rest[12:])
        chunk = rest[: end.start() + 12] if end else rest[:4500]
        return chunk.strip()[:4500]
    lines = []
    for ln in s.splitlines():
        if re.search(r"大学|学院|University|College|学士|硕士|博士|本科|Ph\.?D|Bachelor|Master", ln, re.I):
            if not re.search(r"填表须知|不得超过|限\s*\d+\s*字", ln):
                lines.append(ln.strip())
    return "\n".join(lines[:40]).strip()[:4500]


def pick_resume_files(files: list) -> list:
    ranked = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        blob = " ".join(str(f.get(k) or "") for k in ("filename", "title", "path", "kind_raw"))
        if RESUME_SKIP_RE.search(blob) and not RESUME_RE.search(blob):
            continue
        if not RESUME_RE.search(blob):
            continue
        if not f.get("url"):
            continue
        score = 2 if re.search(r"原始简历|个人简历", blob) else 1
        ranked.append((score, f))
    ranked.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, f in ranked:
        u = str(f.get("url") or "")
        if u in seen:
            continue
        seen.add(u)
        out.append(f)
    return out[:3]


def format_edu_prompt(info: dict) -> str:
    if not info or not (info.get("rows") or info.get("excerpt")):
        return ""
    lines = ["## 原始简历·教育信息（人才库）", "来源：" + str(info.get("source") or "人才库")]
    lines.append("修改意见指出教育信息缺失时，必须用下列已核实学历补申报书教育栏（院校/专业/学位/起止时间）。禁止编造下列未出现的学校或时间。")
    rows = info.get("rows") or []
    if rows:
        lines.append("结构化经历：")
        for i, r in enumerate(rows, 1):
            lines.append(str(i) + ". " + _fmt_row(r))
    excerpt = str(info.get("excerpt") or "").strip()
    if excerpt:
        lines.append("简历摘录：")
        lines.append(excerpt[:4000])
    return "\n".join(lines)


def save_snapshot(task_dir: str | Path, info: dict) -> None:
    d = Path(task_dir) / "work" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    slim = {k: info.get(k) for k in ("needed", "source", "rows", "excerpt", "notes", "leftover")}
    (d / "edu_resume.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")


async def enrich_education(snap: dict, texts, *, app_no: str = "", task_dir: str | Path | None = None) -> dict:
    info = {
        "needed": False, "rows": [], "excerpt": "", "source": "",
        "prompt": "", "notes": [], "leftover": "",
    }
    if not education_missing(texts):
        return info
    info["needed"] = True
    talent = (snap or {}).get("talent") or {}
    rows = extract_from_payload(talent)
    if rows:
        info["rows"] = rows
        info["source"] = "人才库记录"
        info["notes"].append("人才库记录含教育经历 " + str(len(rows)) + " 条")
    if not rows:
        pack_files, pack_notes = await ATT._talent_pack_files(snap, app_no)
        info["notes"].extend(pack_notes)
        resumes = pick_resume_files(pack_files)
        if not resumes:
            info["notes"].append("人才库附件包未找到原始简历文件")
        for f in resumes:
            try:
                text = await _read_resume(f, task_dir)
            except Exception as e:
                info["notes"].append("读取简历「" + str(f.get("filename") or "") + "」失败：" + str(e)[:120])
                continue
            excerpt = slice_education_text(text)
            if not excerpt:
                info["notes"].append("简历「" + str(f.get("filename") or "") + "」未抽出教育经历文字")
                continue
            info["excerpt"] = excerpt
            info["source"] = "人才库原始简历「" + str(f.get("filename") or "resume") + "」"
            info["notes"].append("已从" + info["source"] + "读取教育信息")
            parsed = _rows_from_excerpt(excerpt)
            if parsed:
                info["rows"] = parsed
            break
    info["prompt"] = format_edu_prompt(info)
    if info["needed"] and not info["rows"] and not info["excerpt"]:
        info["leftover"] = "【教育信息】修改意见指出教育经历缺失，人才库未检索到原始简历或教育字段，禁止编造"
    return info


def _rows_from_excerpt(excerpt: str) -> list[dict]:
    out = []
    for ln in str(excerpt or "").splitlines():
        row = _row_from_text(ln)
        if row:
            out.append(row)
    return _dedupe_rows(out)


async def _read_resume(file_item: dict, task_dir: str | Path | None) -> str:
    content, fn, _ctype, _st = await ATT.fetch_upstream({
        "source": "pool",
        "url": str(file_item.get("url") or ""),
        "filename": str(file_item.get("filename") or "resume"),
    })
    if not content:
        return ""
    root = Path(task_dir) / "work" / "tmp" / "resume" if task_dir else Path(".")
    root.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[<>:"|?*\\/]+', "_", str(fn or file_item.get("filename") or "resume")).strip() or "resume"
    path = root / name[:120]
    path.write_bytes(content)
    ext = path.suffix.lower()
    if ext in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if ext in WORD_EXT:
        dst = path.with_suffix(".txt")
        await extract_to_txt(path, dst)
        return dst.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        return await _pdf_text(path)
    if ext in IMAGE_EXT:
        return await image_to_text(path)
    return ""


async def _pdf_text(path: Path) -> str:
    from .pdf_app import _pdf_text_stats, rasterize_pdf_pages
    text, cjk, _, _n = _pdf_text_stats(path)
    if cjk >= 60:
        return text
    from .opinion_extract import ocr_image_bytes
    try:
        pages = rasterize_pdf_pages(path, dpi=110)[:3]
    except Exception:
        return text or ""
    parts = [text] if text.strip() else []
    for i, data, mime, _embed in pages:
        try:
            parts.append(await ocr_image_bytes(data, mime, path.name + "-p" + str(i), prompt=EDU_OCR_PROMPT))
        except Exception:
            break
    return "\n".join(p for p in parts if str(p or "").strip())
