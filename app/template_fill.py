# -*- coding: utf-8 -*-
"""按项目根目录 QM.docx / HJ.docx 表格模板填入解析结果（仅填 OCR/解析中存在的字段）。"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from docx import Document

try:
    from .form_kind import ROOT_DIR
    from .hj_reference import (
        SCHOOL_COUNTRY_HINTS,
        cn_list_join,
        en_list_join,
        fix_entity_name,
        fix_ocr_english,
        format_bilingual_pair,
        highest_degree_from_edu_row,
        is_academic_title,
        normalize_hj_cn_field,
        normalize_hj_en_field,
        split_bilingual,
        split_certificate_name,
    )
except ImportError:
    ROOT_DIR = Path(__file__).resolve().parent.parent
    SCHOOL_COUNTRY_HINTS = ()
    fix_ocr_english = lambda s: str(s or "").strip()
    highest_degree_from_edu_row = lambda row: ("", "")
    format_bilingual_pair = lambda cn, en: (str(cn or ""), str(en or ""))
    split_bilingual = lambda s: (str(s or "").strip(), "")
    cn_list_join = lambda *parts: "；".join(p for p in parts if p)
    en_list_join = lambda *parts: "; ".join(p for p in parts if p)
    fix_entity_name = lambda s: str(s or "").strip()
    is_academic_title = lambda s: False
    normalize_hj_cn_field = lambda s: str(s or "").strip()
    normalize_hj_en_field = lambda s: str(s or "").strip()
    split_certificate_name = lambda s: (str(s or "").strip(), "")

_LABEL_HINTS = (
    "姓名", "证件", "性别", "出生", "国籍", "民族", "手机", "电话", "邮箱", "电子邮件",
    "毕业", "学位", "回国", "职称", "单位", "职务", "地址", "省份", "城市", "实验室",
    "联系人", "填表", "项目类别", "gender", "birth", "nationality", "mobile", "email",
    "degree", "employer", "position", "province", "city", "applicant", "certificate",
    "序号", "起止", "时间", "院校", "专业", "教育", "工作", "经历", "成果", "论文",
    "知识产权", "奖励", "荣誉", "设想", "承诺", "推荐理由", "支持条件",
)

_EDU_TAB = re.compile(
    r"(?:^|[^\d])(\d{6})\s*-\s*(\d{6})\t+([^\t]+)\t+([^\t]+)\t+([^\t]+)\t+((?:学士|硕士|博士)[^\n]*)",
    re.I,
)
_WORK_TAB = re.compile(
    r"(?:^|[^\d])(\d{6})\s*-\s*(\d{6})\t+([^\t]+)\t+([^\t]+)\t+([^\t]+)\t+(全职|兼职)",
    re.I,
)
_PAPER_LINE = re.compile(
    r"^\d+\t(\d{4}-\d{2})\t(.+?)\t(.+?)\t(\S+)\t(.+)$"
)
_PAGE_MARK = re.compile(r"^【第\d+页】|^第\s*\d+\s*页|^\d{1,3}/\d{1,3}$")
_DATE_ONLY = re.compile(r"^(\d{6})\s*-\s*(\d{6})$")
_VERTICAL_NOISE = re.compile(
    r"^(时间|Time|国家|Country|院校|University|专业|Major|学位|Degree|单位|Employer|职务|Position|"
    r"任职情况|Work Performance|Educational Background|All Work Experience|教育经历|全部工作经历|"
    r"破格申报|Exceptional Application).*$",
    re.I,
)
_COUNTRY_NAME = re.compile(
    r"^(印度|韩国|加拿大|美国|英国|德国|法国|日本|澳大利亚|巴西|意大利|中国|西班牙|荷兰|瑞士|瑞典|"
    r"丹麦|挪威|芬兰|波兰|新加坡|马来西亚|泰国|埃及|黎巴嫩|沙特阿拉伯|墨西哥|以色列|比利时|"
    r"奥地利|新西兰|爱尔兰|葡萄牙|希腊|土耳其|俄罗斯|乌克兰|伊朗|南非|阿根廷|智利|中国台湾|中国香港)$"
)
_EN_COUNTRY_MAP = {
    "canada": "加拿大", "india": "印度", "korea": "韩国", "southkorea": "韩国",
    "uk": "英国", "unitedkingdom": "英国", "britain": "英国", "england": "英国",
    "usa": "美国", "unitedstates": "美国", "portugal": "葡萄牙", "china": "中国",
    "brazil": "巴西", "japan": "日本", "germany": "德国", "france": "法国",
}
_SCHOOL_COUNTRY_HINTS = SCHOOL_COUNTRY_HINTS or (
    (re.compile(r"Anna\s*University|安娜大学", re.I), "印度"),
    (re.compile(r"Chonnam|全南", re.I), "韩国"),
    (re.compile(r"Brighton", re.I), "英国"),
    (re.compile(r"Waterloo|滑铁卢", re.I), "加拿大"),
    (re.compile(r"Western|韦仕敦", re.I), "加拿大"),
    (re.compile(r"AdvEn|阿德文", re.I), "加拿大"),
)


def template_path(mode: str) -> Path:
    name = "HJ.docx" if str(mode or "").upper() == "HJ" else "QM.docx"
    p = ROOT_DIR / name
    if not p.is_file():
        raise FileNotFoundError("模板不存在：" + str(p))
    return p


def _norm(s: str) -> str:
    return re.sub(r"[\s/·•\u3000]+", "", str(s or "")).lower()


def _looks_like_label(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    n = _norm(t)
    if len(n) < 2:
        return True
    return any(h in t or h in n for h in _LABEL_HINTS)


def _distinct_cells(row) -> list:
    seen, out = set(), []
    for c in row.cells:
        cid = id(c._tc)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(c)
    return out


def _write_cell(cell, value: str) -> None:
    v = str(value or "").strip()
    if cell.paragraphs:
        cell.paragraphs[0].text = v
        for p in cell.paragraphs[1:]:
            p.text = ""
    else:
        cell.text = v


def _fmt_hj_date(s: str) -> str:
    """参考正式 HJ 申报书：YYYY.MM.DD（仅年月时补 .01）。"""
    raw = str(s or "").strip()
    if "至今" in raw:
        return "至今"
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 8:
        y, m, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        return f"{y}.{m:02d}.{d:02d}" if d else f"{y}.{m:02d}.01"
    if len(digits) == 6:
        return f"{int(digits[:4])}.{int(digits[4:6]):02d}.01"
    if len(digits) == 4:
        return digits
    if re.match(r"\d{4}[./-]\d{1,2}([./-]\d{1,2})?$", raw):
        parts = re.split(r"[./-]", raw)
        y = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        d = int(parts[2]) if len(parts) > 2 else 0
        if d:
            return f"{y}.{m:02d}.{d:02d}"
        if m:
            return f"{y}.{m:02d}.01"
        return str(y)
    if re.match(r"\d{4}\.\d{1,2}$", raw):
        return raw + ".01"
    return raw


def _fmt_hj_range(a: str, b: str) -> str:
    a_s, b_s = str(a or "").strip(), str(b or "").strip()
    if "至今" in b_s or b_s in {"999999", "999912"}:
        return _fmt_hj_date(a_s) + "-至今"
    return _fmt_hj_date(a_s) + "-" + _fmt_hj_date(b_s)


def _normalize_hj_range_text(s: str) -> str:
    """将解析器/OCR 各类起止时间统一为参考申报书格式。"""
    raw = str(s or "").strip()
    if not raw:
        return ""
    if "至今" in raw:
        head = raw.split("至今", 1)[0].rstrip("- ")
        return _fmt_hj_date(head) + "-至今"
    if re.match(r"\d{4}\.\d", raw) and "-" in raw:
        a, b = raw.split("-", 1)
        return _fmt_hj_date(a.strip()) + "-" + _fmt_hj_date(b.strip())
    m = re.match(r"(\d{4})-(\d{2})-(\d{4})-(\d{2})$", raw)
    if m:
        return f"{m.group(1)}.{m.group(2)}-{m.group(3)}.{m.group(4)}"
    m = re.search(r"(\d{6,8})\s*-\s*(\d{6,8}|至今)", raw)
    if m:
        return _fmt_hj_range(m.group(1), m.group(2))
    return raw


def _fmt_birth(s: str) -> str:
    return _fmt_hj_date(s)


def _normalize_country_name(val: str) -> str:
    raw = re.sub(r"\s+", "", str(val or "").strip())
    if not raw:
        return ""
    if _COUNTRY_NAME.match(raw):
        return raw
    key = re.sub(r"[^a-z]", "", raw.lower())
    for en, cn in _EN_COUNTRY_MAP.items():
        if key == en or key.startswith(en):
            return cn
    return raw


def _infer_country_from_entity(name: str) -> str:
    for pat, country in _SCHOOL_COUNTRY_HINTS:
        if pat.search(str(name or "")):
            return country
    return ""


def _lines_after_marker(
    text: str,
    markers: tuple[str, ...],
    stop_markers: tuple[str, ...] = (),
    max_lines: int = 8,
) -> list[str]:
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    start = -1
    for i, ln in enumerate(lines):
        if any(m in ln for m in markers):
            start = i + 1
            break
    if start < 0:
        return []
    out: list[str] = []
    for ln in lines[start: start + max_lines]:
        if not ln or _PAGE_MARK.match(ln) or re.match(r"^\d{1,3}/\d{1,3}$", ln):
            break
        if any(s in ln for s in stop_markers):
            break
        if re.match(r"^(Name of|Date of|Place of|ID Type|Other ID|Contact Information|证件类型)", ln, re.I):
            continue
        out.append(ln)
    return out


def _extract_chinese_transliteration(text: str) -> str:
    parts: list[str] = []
    for ln in _lines_after_marker(
        text,
        ("中文（音译）名", "中文(音译)名", "Name of Chinese Transliteration"),
        ("性别", "Gender", "出生日期", "Date of Birth"),
        8,
    ):
        if re.match(r"^Name of", ln, re.I):
            continue
        if re.search(r"[\u4e00-\u9fff]", ln):
            parts.append(re.sub(r"\s+", "", ln))
    name = "".join(parts)
    if name and len(name) < 6:
        m = re.search(
            r"中文[（(]音译[）)]名[\s\S]{0,120}?((?:[\u4e00-\u9fff·•\s]{2,12}\n?){1,3})"
            r"(?=\s*(?:性别|Gender|出生))",
            text,
            re.I,
        )
        if m:
            alt = re.sub(r"\s+", "", m.group(1))
            if len(alt) > len(name):
                name = alt
    return name


def _extract_vertical_bilingual_block(text: str, section: str, stop: str) -> tuple[str, str]:
    if section not in text:
        return "", ""
    block = text.split(section, 1)[1]
    if stop in block:
        block = block.split(stop, 1)[0]
    cn, en = "", ""
    skip_en = re.compile(
        r"(Last Employer|Before Returning|Coming to China|Highest Degree|Host Province|"
        r"Current or Expected|Type of Last|Country \(Region\)|Position$|Employer$)",
        re.I,
    )
    for ln in [x.strip() for x in block.splitlines() if x.strip()]:
        if ln.startswith(("□", "☑", "☐")):
            continue
        if re.match(r"^(中文|英文)\s*(Cn|En)\b", ln, re.I):
            continue
        if _looks_like_label(ln) or skip_en.search(ln):
            continue
        if re.search(r"[\u4e00-\u9fff]", ln) and not cn:
            cn = ln
        elif re.match(r"^[A-Za-z0-9]", ln) and not en and "@" not in ln:
            en = ln
    return cn, en


def _value_after_vertical_label(text: str, markers: tuple[str, ...], stop_markers: tuple[str, ...] = ()) -> str:
    for ln in _lines_after_marker(text, markers, stop_markers, 5):
        if _looks_like_label(ln) or ln.startswith(("□", "☑", "☐")):
            continue
        if re.match(r"^[A-Za-z].{0,40}$", ln) and any(m.lower() in ln.lower() for m in markers if re.match(r"^[A-Za-z]", m or "")):
            continue
        return ln
    return ""


def _normalize_degree(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    s = re.sub(r"[（(]([^）)]+)[）)]", r"/\1", s)
    s = re.sub(r"[（(]\s*", "/", s)
    s = s.replace(")", "").replace("）", "")
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\bPhD\b", "Doctor", s, flags=re.I)
    s = re.sub(r"\bPh\.D\.?\b", "Doctor", s, flags=re.I)
    if "/" not in s:
        m = re.search(r"(学士|硕士|博士|Bachelor|Master|Doctor)", s, re.I)
        if m:
            token = m.group(1).lower()
            cn = {"bachelor": "学士", "master": "硕士", "doctor": "博士", "博士": "博士", "硕士": "硕士", "学士": "学士"}.get(token, "")
            if cn:
                en = {"学士": "Bachelor", "硕士": "Master", "博士": "Doctor"}[cn]
                return f"{cn}/{en}"
    return s


def _normalize_bilingual(text: str) -> str:
    """职务/单位/院校：中文/English（参考 HJ 样例 table2 栏位）。"""
    s = fix_entity_name(fix_ocr_english(str(text or "").strip()))
    if not s:
        return ""
    if "/" in s:
        a, b = s.split("/", 1)
        return f"{a.strip()}/{fix_ocr_english(b)}"
    m = re.match(r"^(.+?)\s*[（(]([^）)]+)[）)]\s*$", s)
    if m:
        return f"{m.group(1).strip()}/{fix_ocr_english(m.group(2))}"
    m = re.match(r"^(.+?)\s+([A-Za-z][^，,;；]+)$", s)
    if m:
        return f"{m.group(1).strip()}/{fix_ocr_english(m.group(2))}"
    return s


def _hj_template_kind(doc: Document) -> str:
    return "compact" if len(doc.tables) <= 11 else "full"


def _edu_work_row_bounds(table) -> tuple[int, int, int]:
    """返回 (edu_start, edu_end, work_start) 行号。"""
    work_hdr = len(table.rows)
    for ri, row in enumerate(table.rows):
        t = _norm(" ".join(c.text for c in row.cells))
        if "工作经历" in t or "workexperience" in t:
            work_hdr = ri
            break
    return 1, work_hdr, work_hdr + 1


def _enrich_hj_fields(fields: dict[str, str], edu: list[dict], work: list[dict]) -> dict[str, str]:
    """参考 HJ 样例：用教育/工作表最高行补全 table1 与回国前信息。"""
    out = dict(fields)
    if edu:
        cn, en = highest_degree_from_edu_row(edu[0])
        if cn:
            out["最高学位中文"] = cn
        if en:
            out["最高学位英文"] = fix_ocr_english(en)
    if work:
        latest = work[0]
        if not out.get("回国前所在地"):
            c = str(latest.get("所在国家") or "").strip()
            if c:
                out["回国前所在地"] = _normalize_country_name(c)
        if not out.get("回国前单位职务中文") and not out.get("回国前单位职务英文"):
            ecn, een = split_bilingual(latest.get("工作单位") or "")
            pcn, pen = split_bilingual(latest.get("担任职务") or "")
            cn = cn_list_join(ecn, pcn)
            en = en_list_join(een, pen)
            if cn:
                out["回国前单位职务中文"] = cn
            if en:
                out["回国前单位职务英文"] = fix_ocr_english(en)
    cn_ret, en_ret = format_bilingual_pair(
        out.get("回国前单位职务中文") or "",
        out.get("回国前单位职务英文") or "",
    )
    if cn_ret:
        out["回国前单位职务中文"] = cn_ret
    if en_ret:
        out["回国前单位职务英文"] = fix_ocr_english(en_ret)
    for key in ("最高学位中文", "回国前单位职务中文"):
        if out.get(key):
            out[key] = normalize_hj_cn_field(out[key])
    for key in ("最高学位英文", "回国前单位职务英文"):
        if out.get(key):
            out[key] = normalize_hj_en_field(out[key])
    en_name, cn_name = split_certificate_name(out.get("有效证件姓名") or "")
    if en_name:
        out["有效证件姓名"] = en_name
    if cn_name and not out.get("中文（音译）名"):
        out["中文（音译）名"] = cn_name
    return out


def _clean_value(key: str, val: str) -> str:
    v = fix_ocr_english(str(val or "").strip())
    if not v:
        return ""
    v = re.sub(r"^[（(]?\s*Employer\s*[）)]?\s*[：:]\s*", "", v, flags=re.I)
    v = re.sub(r"^Current or Expected Employer Add\.?\s*", "", v, flags=re.I)
    if key == "出生国家（地区）":
        v = re.split(r"证件号码", v)[0].strip("：: ，,")
    if key in ("申报单位", "现工作单位", "用人单位名称") and v.startswith("（"):
        v = re.sub(r"^[（(][^）)]*[）)]\s*[：:]\s*", "", v)
    if key == "证件号码" and v.upper() in {"PASSPORTAL99", "OTHERIDNO", "IDNO"}:
        return ""
    if len(v) > 600 and key not in ("专长及代表性成果", "工作设想", "用人单位简介", "推荐理由", "支持条件"):
        v = v[:600]
    return v.strip()


def _first_group(patterns: list[tuple[str, str]], text: str) -> str:
    for key, pat in patterns:
        m = re.search(pat, text, re.I | re.M)
        if m:
            v = _clean_value(key, m.group(1))
            if v:
                return v
    return ""


def _section_between(text: str, starts: tuple[str, ...], ends: tuple[str, ...]) -> str:
    raw = str(text or "")
    start = -1
    for s in starts:
        i = raw.find(s)
        if i >= 0:
            start = i + len(s)
            break
    if start < 0:
        return ""
    end = len(raw)
    for e in ends:
        i = raw.find(e, start)
        if i > start:
            end = min(end, i)
    body = raw[start:end]
    lines = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or _PAGE_MARK.match(s):
            continue
        if s.startswith("I'm now zeroing"):
            break
        lines.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _fill_edu_gaps(edu: list[dict]) -> list[dict]:
    """教育经历竖排 OCR 缺列时，用相邻学历行补全国家/院校。"""
    out = [dict(r) for r in edu]
    for i, row in enumerate(out):
        if row.get("所在国家") and row.get("校名称"):
            continue
        for j in range(i + 1, len(out)):
            older = out[j]
            if not row.get("所在国家") and older.get("所在国家"):
                row["所在国家"] = older["所在国家"]
            if not row.get("校名称") and older.get("校名称"):
                row["校名称"] = older["校名称"]
            if row.get("所在国家") and row.get("校名称"):
                break
        for j in range(i - 1, -1, -1):
            newer = out[j]
            if not row.get("所在国家") and newer.get("所在国家"):
                row["所在国家"] = newer["所在国家"]
            if not row.get("校名称") and newer.get("校名称"):
                row["校名称"] = newer["校名称"]
            if row.get("所在国家") and row.get("校名称"):
                break
    return [_normalize_edu_row(r) for r in out]


def _normalize_edu_row(d: dict) -> dict:
    start = str(d.get("开始时间") or "").strip()
    end = str(d.get("结束时间") or "").strip()
    range_text = str(d.get("起止时间") or "").strip()
    if start or end:
        range_text = _fmt_hj_range(start, end or "至今")
    elif range_text:
        range_text = _normalize_hj_range_text(range_text)
    school = _normalize_bilingual(str(d.get("校名称") or d.get("学校名称") or "").strip())
    major = str(d.get("专业领域") or d.get("专业名称") or "").strip()
    country = _normalize_country_name(str(d.get("所在国家") or "").strip())
    if not country:
        country = _infer_country_from_entity(school) or _infer_country_from_entity(major)
    return {
        "起止时间": range_text,
        "所在国家": country,
        "校名称": school,
        "专业领域": major,
        "学位": _normalize_degree(str(d.get("学位") or "").strip()),
    }


def _normalize_work_row(d: dict) -> dict:
    start = str(d.get("开始时间") or "").strip()
    end = str(d.get("结束时间") or "").strip()
    range_text = str(d.get("起止时间") or "").strip()
    if start or end:
        range_text = _fmt_hj_range(start, end or "至今")
    elif range_text:
        range_text = _normalize_hj_range_text(range_text)
    employer = _normalize_bilingual(str(d.get("工作单位") or d.get("单位") or "").strip())
    country = _normalize_country_name(str(d.get("所在国家") or "").strip())
    if not country:
        country = _infer_country_from_entity(employer)
    return {
        "起止时间": range_text,
        "所在国家": country,
        "工作单位": employer,
        "担任职务": _normalize_bilingual(str(d.get("担任职务") or d.get("职务") or "").strip()),
        "任职情况": str(d.get("任职情况") or d.get("工作性质") or "全职").strip() or "全职",
    }


def _row_identity(row: dict, kind: str) -> str:
    if kind == "edu":
        return _norm(str(row.get("校名称") or "") + str(row.get("学位") or ""))
    return _norm(str(row.get("工作单位") or "") + str(row.get("担任职务") or ""))


def _merge_timeline_rows(ocr_rows: list[dict], parser_rows: list[dict], kind: str) -> list[dict]:
    """OCR 行优先；解析器补全缺失段，统一为 HJ 参考格式（时间倒序）。"""
    ocr = [r for r in (_normalize_edu_row(d) if kind == "edu" else _normalize_work_row(d) for d in ocr_rows) if _row_identity(r, kind)]
    parser = [r for r in (_normalize_edu_row(d) if kind == "edu" else _normalize_work_row(d) for d in parser_rows) if _row_identity(r, kind)]
    if not parser:
        return ocr
    if not ocr:
        return list(reversed(parser))
    seen = {_row_identity(r, kind) for r in ocr}
    merged = list(ocr)
    for row in reversed(parser):
        key = _row_identity(row, kind)
        if key and key not in seen:
            merged.append(row)
            seen.add(key)
    return merged


def _is_vertical_noise(line: str) -> bool:
    s = str(line or "").strip()
    if not s or _PAGE_MARK.match(s):
        return True
    if _VERTICAL_NOISE.match(s):
        return True
    if re.match(r"^\(?从本科", s) or re.match(r"^\(?完整填写", s):
        return True
    if len(s) > 80 and re.search(r"(Time|Country|Region|University|Degree)", s, re.I):
        return True
    return False


def _vertical_edu_from_buf(a: str, b: str, buf: list[str]) -> dict | None:
    lines = [x for x in buf if x and not _is_vertical_noise(x)]
    if not lines:
        return None
    deg_i = -1
    for i in range(len(lines) - 1, -1, -1):
        if re.search(r"(学士|硕士|博士)", lines[i], re.I):
            deg_i = i
            break
    if deg_i < 0:
        return None
    major = lines[deg_i - 1] if deg_i >= 1 else ""
    rest = lines[: max(0, deg_i - 1)]
    country, school = "", ""
    if rest and _COUNTRY_NAME.match(rest[0]):
        country = rest[0]
        school = " ".join(rest[1:]).strip()
    else:
        school = " ".join(rest).strip()
    return _normalize_edu_row({
        "起止时间": _fmt_hj_range(a, b),
        "所在国家": country,
        "校名称": school,
        "专业领域": major,
        "学位": lines[deg_i],
    })


def _vertical_work_from_buf(a: str, b: str, buf: list[str]) -> dict | None:
    lines = [x for x in buf if x and not _is_vertical_noise(x)]
    if not lines:
        return None
    perf = "全职"
    if lines[-1] in ("全职", "兼职"):
        perf = lines[-1]
        lines = lines[:-1]
    if not lines:
        return None
    position = lines[-1]
    employer = " ".join(lines[:-1]).strip() if len(lines) > 1 else lines[0]
    country = ""
    if _COUNTRY_NAME.match(employer):
        country, employer = employer, (lines[1] if len(lines) > 2 else "")
        position = lines[-1]
    return _normalize_work_row({
        "起止时间": _fmt_hj_range(a, b),
        "所在国家": country,
        "工作单位": employer,
        "担任职务": position,
        "任职情况": perf,
    })


def _parse_vertical_block(lines: list[str], start: int, end: int, kind: str) -> list[dict]:
    rows: list[dict] = []
    i = start
    while i < end:
        m = _DATE_ONLY.match(lines[i])
        if not m:
            i += 1
            continue
        buf: list[str] = []
        i += 1
        while i < end:
            if _DATE_ONLY.match(lines[i]):
                break
            if any(k in lines[i] for k in ("破格申报", "重要科研奖励", "科研情况", "工作设想")):
                break
            if not _is_vertical_noise(lines[i]):
                buf.append(lines[i])
            i += 1
        row = _vertical_edu_from_buf(m.group(1), m.group(2), buf) if kind == "edu" else _vertical_work_from_buf(m.group(1), m.group(2), buf)
        if row:
            rows.append(row)
    return rows


def _parse_vertical_list_lines(text: str) -> tuple[list[dict], list[dict]]:
    raw = [ln.strip() for ln in str(text or "").splitlines()]
    edu_i = work_i = -1
    for i, ln in enumerate(raw):
        if edu_i < 0 and ("教育经历" in ln or "Educational Background" in ln):
            edu_i = i
        if work_i < 0 and ("全部工作经历" in ln or "All Work Experience" in ln):
            work_i = i
    edu = _parse_vertical_block(raw, edu_i + 1, work_i if work_i > edu_i else len(raw), "edu") if edu_i >= 0 else []
    work = _parse_vertical_block(raw, work_i + 1, len(raw), "work") if work_i >= 0 else []
    return edu, work


def _parse_list_lines(text: str) -> tuple[list[dict], list[dict]]:
    edu, work = [], []
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = _EDU_TAB.search(s)
        if m:
            edu.append(_normalize_edu_row({
                "起止时间": _fmt_hj_range(m.group(1), m.group(2)),
                "所在国家": m.group(3).strip(),
                "校名称": m.group(4).strip(),
                "专业领域": m.group(5).strip(),
                "学位": m.group(6).strip(),
            }))
            continue
        m = _WORK_TAB.search(s)
        if m:
            work.append(_normalize_work_row({
                "起止时间": _fmt_hj_range(m.group(1), m.group(2)),
                "所在国家": m.group(3).strip(),
                "工作单位": m.group(4).strip(),
                "担任职务": m.group(5).strip(),
                "任职情况": m.group(6).strip(),
            }))
    if len(edu) < 2 and len(work) < 2:
        v_edu, v_work = _parse_vertical_list_lines(text)
        if len(v_edu) > len(edu):
            edu = v_edu
        if len(v_work) > len(work):
            work = v_work
    return list(reversed(edu)), list(reversed(work))


def _parse_papers(text: str) -> list[dict]:
    rows = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if not s or _PAGE_MARK.match(s):
            continue
        m = _PAPER_LINE.match(s)
        if not m:
            continue
        rows.append({
            "发表时间": m.group(1),
            "论文题目": m.group(2).strip(),
            "发表载体": m.group(3).strip(),
            "排序": m.group(4).strip(),
            "角色": m.group(5).strip(),
        })
    return rows


def _pick_tab_after(label: str, text: str) -> str:
    return _pick_after_label(text, label)


def _pick_after_label(text: str, label: str, stop_labels: tuple[str, ...] = ()) -> str:
    """取 OCR 竖排「标签\\n值」或「标签\\t值」中的值。"""
    raw = str(text or "")
    for pat in (
        re.escape(label) + r"\s*\n\s*([^\n]+)",
        re.escape(label) + r"\s*[：:]\s*([^\n]+)",
        re.escape(label) + r"\t+([^\n]+)",
    ):
        m = re.search(pat, raw, re.I)
        if not m:
            continue
        val = str(m.group(1) or "").strip()
        if not val or _looks_like_label(val):
            continue
        if any(s in val for s in stop_labels):
            continue
        if val.startswith(("□", "☑", "☐")):
            continue
        return val
    return ""


def _norm_cmp(s: str) -> str:
    s = str(s or "").replace("（", "(").replace("）", ")")
    return re.sub(r"[\s/·•\u3000]+", "", s).lower()


def _finalize_hj_fields(fields: dict[str, str], text: str) -> dict[str, str]:
    """补全 OCR 竖排/缺页时仍可提取的 HJ 栏位。"""
    out = dict(fields)
    raw = str(text or "")

    if not out.get("中文（音译）名"):
        m = re.search(r"申报人有效证件姓名[^：\n]*[：:]\s*[^\n（]+\(([^）)]+)\)", raw)
        if m:
            out["中文（音译）名"] = m.group(1).strip()
        elif out.get("有效证件姓名"):
            m = re.search(r"\(([^）)]+)\)", str(out["有效证件姓名"]))
            if m and re.search(r"[\u4e00-\u9fff]", m.group(1)):
                out["中文（音译）名"] = m.group(1).strip()

    if not out.get("最高学位中文"):
        out["最高学位中文"] = _pick_after_label(raw, "中文 Cn", ("英文", "En"))
    if not out.get("最高学位英文"):
        out["最高学位英文"] = fix_ocr_english(_pick_after_label(raw, "英文 En", ("回国", "Last Employer")))

    if not out.get("回国前单位职务中文") or not out.get("回国前单位职务英文"):
        cn_v, en_v = _extract_vertical_bilingual_block(raw, "回国（来华）前单位及职务", "相当于国内职称")
        if cn_v and not out.get("回国前单位职务中文"):
            out["回国前单位职务中文"] = cn_v.replace("、", "，")
        if en_v and not out.get("回国前单位职务英文"):
            out["回国前单位职务英文"] = fix_ocr_english(en_v.replace("、", ", "))

    if not out.get("回国时间"):
        rt = _value_after_vertical_label(
            raw,
            ("Time of Coming to China", "回国（来华）时间"),
            ("【第", "拟（现）任职单位地址", "4/30"),
        )
        if rt:
            out["回国时间"] = _fmt_hj_date(rt.replace("年", ".").replace("月", ""))

    if not out.get("相当于国内职称") and "相当于国内职称" in raw:
        block = raw.split("相当于国内职称", 1)[-1][:240]
        m = re.search(r"☑\s*([^□☐\n]{2,24})", block)
        if m:
            out["相当于国内职称"] = re.sub(r"\s+", "", m.group(1))

    if not out.get("回国前单位类型") and "回国（来华）前工作单位类型" in raw:
        block = raw.split("回国（来华）前工作单位类型", 1)[-1][:260]
        m = re.search(r"☑\s*(高校|科研机构|企业|其他)", block)
        if m:
            out["回国前单位类型"] = m.group(1)

    if not out.get("回国前所在地"):
        cn = _pick_after_label(raw, "Country (Region) of Residence", ("Host Province", "拟落地"))
        if cn:
            out["回国前所在地"] = _normalize_country_name(cn)

    if not out.get("国籍地区"):
        m = re.search(r"(?i)Foreign Nationality\s*\n\s*([^\n☐□☑]+)", raw)
        if m:
            out["国籍地区"] = _normalize_country_name(m.group(1))
        elif out.get("回国前所在地"):
            out["国籍地区"] = out["回国前所在地"]

    if not out.get("出生国家（地区）"):
        m = re.search(r"(?i)Place of Birth\s*\n\s*([^\n]+)", raw)
        if m and _COUNTRY_NAME.match(_normalize_country_name(m.group(1))):
            out["出生国家（地区）"] = _normalize_country_name(m.group(1))
        else:
            edu_block = raw.split("教育经历", 1)[-1] if "教育经历" in raw else raw
            for ln in edu_block.splitlines():
                s = ln.strip()
                if _DATE_ONLY.match(s):
                    continue
                if _COUNTRY_NAME.match(s):
                    out["出生国家（地区）"] = s
                    break

    if not out.get("证件号码"):
        m = re.search(r"☑\s*护照[^\n]*\n\s*([A-Z0-9]{6,})", raw, re.I)
        if m:
            out["证件号码"] = m.group(1)

    if not out.get("手机号"):
        out["手机号"] = _pick_after_label(raw, "手机号 Mobile", ("电子邮件", "Email")) or out.get("手机号", "")

    if not out.get("电子邮箱"):
        out["电子邮箱"] = _pick_after_label(raw, "电子邮件 Email", ("最终毕业", "Highest Degree"))

    if not out.get("性别"):
        if re.search(r"☑\s*男", raw):
            out["性别"] = "男"
        elif re.search(r"☑\s*女", raw):
            out["性别"] = "女"

    if not out.get("出生日期"):
        m = re.search(r"(?i)Date of Birth[^\d]{0,40}(\d{8})", raw)
        if m:
            out["出生日期"] = _fmt_birth(m.group(1))

    for label, key in (
        ("化学", "专业领域勾选"), ("材料科学", "专业领域勾选"), ("工程科学", "专业领域勾选"),
        ("环境", "专业领域勾选"), ("信息科学", "专业领域勾选"),
        ("新能源", "关键技术勾选"), ("集成电路", "关键技术勾选"),
        ("生命健康", "关键技术勾选"), ("应用基础研究", "研究类型勾选"),
        ("地方（地市级）实验室", "实验室类别勾选"), ("地方(地市级)实验室", "实验室类别勾选"),
        ("地市级", "实验室类别勾选"), ("地方（省级）实验室", "实验室类别省级"),
        ("创新项目", "项目类别勾选"),
    ):
        if not out.get(key) and re.search(r"☑\s*" + re.escape(label), raw):
            out[key] = label

    corpus = raw + str(out.get("专长及代表性成果") or "") + str(out.get("最高学位中文") or "")
    if not out.get("专业领域勾选"):
        if re.search(r"电化学|应用化学|化学工程|化学与|化学化工|化工|Chemical", corpus, re.I):
            out["专业领域勾选"] = "化学"
        elif re.search(r"材料科学|材料工程|Advanced Materials|纳米材料|导热材料", corpus, re.I):
            out["专业领域勾选"] = "材料科学"
        elif re.search(r"工程科学|Engineering|机械|制造", corpus, re.I):
            out["专业领域勾选"] = "工程科学"
    if not out.get("关键技术勾选"):
        if re.search(r"新能源|电池|储能|电解质|动力电池", corpus, re.I):
            out["关键技术勾选"] = "新能源"
        elif re.search(r"生命健康|医学|生物", corpus, re.I):
            out["关键技术勾选"] = "生命健康"

    if out.get("国籍地区"):
        out["国籍地区"] = _normalize_country_name(out["国籍地区"])
    if out.get("回国前所在地"):
        out["回国前所在地"] = _normalize_country_name(out["回国前所在地"])
    if out.get("出生国家（地区）"):
        out["出生国家（地区）"] = _normalize_country_name(out["出生国家（地区）"])
    if out.get("最高学位英文"):
        out["最高学位英文"] = normalize_hj_en_field(out["最高学位英文"])
    if out.get("回国前单位职务英文"):
        out["回国前单位职务英文"] = normalize_hj_en_field(out["回国前单位职务英文"])
    for key in ("最高学位中文", "回国前单位职务中文"):
        if out.get(key):
            out[key] = normalize_hj_cn_field(out[key])
    en_name, cn_name = split_certificate_name(out.get("有效证件姓名") or "")
    if en_name:
        out["有效证件姓名"] = en_name
    if cn_name and not out.get("中文（音译）名"):
        out["中文（音译）名"] = cn_name

    return {k: v for k, v in out.items() if str(v or "").strip()}


def apply_text_edits(text: str, edits: list) -> str:
    """将已确认编辑应用到 OCR 原文（供模板渲染使用）。"""
    out = str(text or "")
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        find = str(e.get("find") or "")
        repl = str(e.get("replace") or "")
        if find and repl and find in out:
            out = out.replace(find, repl, 1)
    return out


def _build_fields(data: dict, raw_text: str) -> dict[str, str]:
    person = data.get("申报人基本信息") if isinstance(data.get("申报人基本信息"), dict) else {}
    info = data.get("申报信息") if isinstance(data.get("申报信息"), dict) else {}
    emp = data.get("用人单位情况及承诺") if isinstance(data.get("用人单位情况及承诺"), dict) else {}
    basic = emp.get("企业基本情况") if isinstance(emp.get("企业基本情况"), dict) else {}

    text = str(raw_text or "")
    regex_fields = {
        "有效证件姓名": _first_group([
            ("有效证件姓名", r"(?i)Name of ID\t+([^\n\t]+)"),
            ("有效证件姓名", r"申报人有效证件姓名[^：\n]*[：:]\s*([^\n（]+)"),
        ], text),
        "中文（音译）名": _extract_chinese_transliteration(text) or _first_group([
            ("中文（音译）名", r"(?i)Name of Chinese Transliteration\t+([^\n\t]+)"),
        ], text),
        "性别": _first_group([
            ("性别", r"(?i)Gender\t+.*?([男女])"),
            ("性别", r"☑\s*(男)"),
            ("性别", r"☑\s*(女)"),
        ], text),
        "出生日期": _first_group([
            ("出生日期", r"(?i)Date of Birth[\s\S]{0,120}?(\d{8})"),
            ("出生日期", r"(?i)Date of Birth[^\n]*\t+(\d{8})"),
        ], text),
        "出生国家（地区）": _first_group([
            ("出生国家（地区）", r"(?i)Place of Birth\t+([^\n\t]+)"),
            ("出生国家（地区）", r"(?i)Place of\s*\n?\s*Birth\s*\n\s*([^\n]+)"),
        ], text),
        "国籍地区": _first_group([
            ("国籍地区", r"Foreign Nationality\t+([^\n\t☐□]+)"),
            ("国籍地区", r"☑\s*外籍[^\n]*?([^\n\t☐□]{2,20})"),
        ], text),
        "证件号码": _first_group([
            ("证件号码", r"☑\s*护照[\s\S]{0,40}?Passport[\s\t]+([A-Z0-9]{6,})"),
            ("证件号码", r"☑\s*护照[^\n]*?Passport[\s\t]+([A-Z0-9]{6,})"),
            ("证件号码", r"☑\s*护照[^\n]*\n\s*([A-Z0-9]{6,})"),
            ("证件号码", r"(?i)Other ID NO\.\t+([A-Z0-9]{6,})"),
            ("证件号码", r"(?i)ID NO\.\t+([A-Z0-9]{6,})"),
        ], text),
        "证件类型": _first_group([("证件类型", r"☑\s*(护照|身份证|永居证)")], text) or "护照",
        "手机号": _first_group([
            ("手机号", r"(?i)Mobile\t+([+\d\- ]{8,})"),
            ("手机号", r"(?i)Mobile\s*\n\s*([+\d\- ]{8,})"),
            ("手机号", r"手机号\s*Mobile\t+([+\d\- ]{8,})"),
        ], text),
        "电子邮箱": _first_group([
            ("电子邮箱", r"(?i)电子邮件\s*Email\s*\n\s*([^\s\t\n]+@[^\s\t\n]+)"),
            ("电子邮箱", r"(?i)Email\s*\n\s*([^\s\t\n]+@[^\s\t\n]+)"),
            ("电子邮箱", r"(?i)Email\t+([^\s\t\n]+@[^\s\t\n]+)"),
        ], text),
        "最高学位中文": _pick_after_label(text, "中文 Cn", ("英文", "En")),
        "最高学位英文": _pick_after_label(text, "英文 En", ("回国", "Last Employer")),
        "回国前单位职务中文": _pick_tab_after("中文 Cn", text.split("回国（来华）前单位及职务")[-1] if "回国（来华）前单位及职务" in text else ""),
        "回国前单位职务英文": _pick_tab_after("英文 En", text.split("回国（来华）前单位及职务")[-1] if "回国（来华）前单位及职务" in text else ""),
        "相当于国内职称": _first_group([("相当于国内职称", r"☑\s*([^□☐\n]{2,20})")], text.split("相当于国内职称")[-1] if "相当于国内职称" in text else ""),
        "回国前单位类型": _first_group([("回国前单位类型", r"☑\s*(高校|科研机构|企业|其他)")], text.split("回国（来华）前工作单位类型")[-1] if "回国（来华）前工作单位类型" in text else ""),
        "回国前所在地": _normalize_country_name(_first_group([
            ("回国前所在地", r"(?i)Country \(Region\) of Residence\t+([^\n\t]+)"),
            ("回国前所在地", r"(?i)Country \(Region\) of Residence\s*\n\s*([^\n]+)"),
        ], text)),
        "拟落地省": _first_group([("拟落地省", r"(?i)Host Province\t+([^\n\t]+)")], text) or _value_after_vertical_label(
            text, ("拟落地省", "Host Province"), ("落地地级市", "Host City"),
        ),
        "拟落地市": _first_group([("拟落地市", r"(?i)Host City\t+([^\n\t]+)")], text) or _value_after_vertical_label(
            text, ("落地地级市", "Host City"), ("拟（现）任职单位名称", "Current or Expected Employer"),
        ),
        "现工作单位": _first_group([
            ("现工作单位", r"(?i)Current or Expected Employer\t+([^\n\t]+)"),
            ("现工作单位", r"拟（现）任职单位名称[^\n]*\t+([^\n\t]+)"),
        ], text) or _value_after_vertical_label(
            text.split("拟（现）任职单位名称", 1)[-1] if "拟（现）任职单位名称" in text else text,
            ("Current or Expected Employer",),
            ("职务（岗位）", "Position", "拟（现）任职单位地址"),
        ),
        "引进职务": _first_group([
            ("引进职务", r"(?i)Position\t+([^\n\t]+)"),
            ("引进职务", r"职务（岗位）\s*\t+([^\n\t]+)"),
        ], text) or _value_after_vertical_label(
            text.split("职务（岗位）", 1)[-1] if "职务（岗位）" in text else text,
            ("Position",),
            ("拟（现）任职单位地址", "Current or Expected Employer Add", "回国（来华）时间"),
        ),
        "任职单位地址": _first_group([
            ("任职单位地址", r"(?i)Current or Expected Employer Add\.\t+([^\n]+)"),
        ], text) or _value_after_vertical_label(
            text,
            ("拟（现）任职单位地址", "Current or Expected Employer Add"),
            ("回国（来华）时间", "Time of Coming to China"),
        ),
        "回国时间": _first_group([
            ("回国时间", r"(?i)Time of Coming to China\t+([^\n\t]+)"),
            ("回国时间", r"(?i)Time of Coming to China\s*\n\s*([^\n]+)"),
        ], text),
        "申报单位": _first_group([("申报单位", r"申报单位[^：\n]*[：:]\s*([^\n]+)")], text),
        "实验室名称": _first_group([("实验室名称", r"实验室名称[^：\n]*[：:]\s*([^\n]+)")], text),
        "项目类别": _first_group([("项目类别", r"项目类别[^：\n]*[：:]\s*([^\n]+)")], text),
        "单位联系人": _first_group([
            ("单位联系人", r"单位联系人[^：\n]*[：:]\s*([^\n]+)"),
            ("单位联系人", r"(?i)Contact Person\t+([^\n\t]+)"),
        ], text),
        "联系人电话": _first_group([
            ("联系人电话", r"联系人电话[^：\n]*[：:]\s*([^\n]+)"),
            ("联系人电话", r"(?i)Telephone Number\t+([^\n\t]+)"),
        ], text),
        "填表日期": _first_group([
            ("填表日期", r"填表日期[^：\n\d]*[：:]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"),
            ("填表日期", r"填表日期[^：\n]*[：:]\s*([^\n]+)"),
        ], text),
        "二级学科及代码": _first_group([
            ("二级学科及代码", r"所属二级学科及代码[^\n]*\n\s*([^\n\d□☑]{2,80}\d{4,6}[^\n]*)"),
            ("二级学科及代码", r"所属二级学科及代码[^：\n]*[：:]\s*([^\n]+)"),
            ("二级学科及代码", r"Category II Discipline and Code[：:]\s*([^\n]+)"),
        ], text),
        "具体研究方向": _first_group([
            ("具体研究方向", r"具体研究方向[^：\n]*[：:]\s*([^\n]+)"),
        ], text),
        "申报单位上级": _first_group([
            ("申报单位上级", r"上级部门/地方[^：\n]*[：:]\s*([^\n]+)"),
            ("申报单位上级", r"按照隶属关系填写[^：\n]*[：:]\s*([^\n]+)"),
        ], text),
        "用人单位类型": _first_group([("用人单位类型", r"☑\s*(民营企业|中央企业|地方国有企业|部属高校|地方高校)")], text.split("用人单位类型")[-1] if "用人单位类型" in text else ""),
    }

    # 回国前职务：优先 tab 格式，否则取竖排 OCR 的中英行
    if "回国（来华）前单位及职务" in text:
        block = text.split("回国（来华）前单位及职务", 1)[1].split("相当于国内职称")[0]
        cn_parts = re.findall(r"中文\s*Cn\t+([^\n]+)", block, re.I)
        en_parts = re.findall(r"英文\s*En\t+([^\n]+)", block, re.I)
        if cn_parts:
            regex_fields["回国前单位职务中文"] = _clean_value("回国前单位职务中文", cn_parts[0])
        if en_parts:
            regex_fields["回国前单位职务英文"] = _clean_value("回国前单位职务英文", en_parts[0])
        if not regex_fields.get("回国前单位职务中文") or not regex_fields.get("回国前单位职务英文"):
            cn_v, en_v = _extract_vertical_bilingual_block(text, "回国（来华）前单位及职务", "相当于国内职称")
            if cn_v and not regex_fields.get("回国前单位职务中文"):
                regex_fields["回国前单位职务中文"] = _clean_value("回国前单位职务中文", cn_v)
            if en_v and not regex_fields.get("回国前单位职务英文"):
                regex_fields["回国前单位职务英文"] = _clean_value("回国前单位职务英文", en_v)

    if "最终毕业院校及专业" in text or "Highest Degree" in text:
        block = text
        if "回国（来华）前单位及职务" in text:
            block = text.split("回国（来华）前单位及职务")[0]
        cn_parts = re.findall(r"中文\s*Cn\t+([^\n]+)", block, re.I)
        en_parts = re.findall(r"英文\s*En\t+([^\n]+)", block, re.I)
        if cn_parts:
            regex_fields["最高学位中文"] = _clean_value("最高学位中文", cn_parts[-1])
        if en_parts:
            regex_fields["最高学位英文"] = _clean_value("最高学位英文", en_parts[-1])

    parser_fields = {
        "有效证件姓名": str(person.get("有效证件姓名") or info.get("申报人") or ""),
        "中文（音译）名": str(person.get("外籍专家中文姓名") or person.get("中文（音译）名") or ""),
        "性别": str(person.get("性别") or ""),
        "出生日期": str(person.get("出生日期") or ""),
        "出生国家（地区）": str(person.get("出生国家（地区）") or person.get("出生国家") or ""),
        "国籍地区": str(person.get("国籍地区") or ""),
        "证件号码": str(person.get("证件号码") or ""),
        "证件类型": str(person.get("证件类型") or ""),
        "手机号": str(person.get("手机号") or ""),
        "电子邮箱": str(person.get("电子邮箱") or person.get("电子邮件") or ""),
        "现工作单位": str(person.get("现工作单位") or person.get("拟（现）任职单位名称") or basic.get("企业名称") or person.get("用人单位名称") or ""),
        "引进职务": str(person.get("引进职务") or person.get("职务（岗位）") or ""),
        "申报单位": str(info.get("申报企业") or basic.get("企业名称") or person.get("用人单位名称") or ""),
        "实验室名称": str(basic.get("实验室名称") or person.get("实验室名称") or ""),
        "单位联系人": str(basic.get("联系人") or person.get("单位联系人") or ""),
        "联系人电话": str(basic.get("联系人电话") or person.get("联系人电话") or ""),
        "填表日期": str(info.get("申报日期") or person.get("填表日期") or ""),
    }

    fields: dict[str, str] = {}
    for k in set(regex_fields) | set(parser_fields):
        for src in (regex_fields, parser_fields):
            v = _clean_value(k, src.get(k) or "")
            if v and (k not in fields or len(v) > len(fields[k])):
                fields[k] = v

    for label, val in re.findall(r"^([^：\n]{2,40})[：:]\s*(.+)$", text):
        k = label.strip()
        v = _clean_value(k, val)
        if v and (k not in fields or not fields[k]):
            fields[k] = v

    if fields.get("性别") in ("☑ 男 Male", "男 Male", "Male"):
        fields["性别"] = "男"
    elif fields.get("性别") in ("☑ 女 Female", "女 Female", "Female"):
        fields["性别"] = "女"
    if not fields.get("性别"):
        if re.search(r"☑\s*男", text):
            fields["性别"] = "男"
        elif re.search(r"☑\s*女", text):
            fields["性别"] = "女"
    if not fields.get("国籍地区"):
        m = re.search(r"Foreign Nationality\s*\n\s*([^\n☐□☑]+)", text, re.I)
        if m:
            fields["国籍地区"] = _normalize_country_name(_clean_value("国籍地区", m.group(1)))
    if fields.get("国籍地区"):
        fields["国籍地区"] = _normalize_country_name(fields["国籍地区"])
    if fields.get("回国前所在地"):
        fields["回国前所在地"] = _normalize_country_name(fields["回国前所在地"])
    if fields.get("出生国家（地区）"):
        fields["出生国家（地区）"] = _normalize_country_name(fields["出生国家（地区）"])
    if not fields.get("出生国家（地区）"):
        lines = [ln.strip() for ln in text.splitlines()]
        for i, ln in enumerate(lines):
            if re.fullmatch(r"\d{8}", ln):
                for j in range(i + 1, min(i + 12, len(lines))):
                    if _COUNTRY_NAME.match(lines[j]):
                        fields["出生国家（地区）"] = lines[j]
                        break
                break
    if fields.get("出生日期"):
        fields["出生日期"] = _fmt_birth(fields["出生日期"])
    if fields.get("回国时间"):
        fields["回国时间"] = _fmt_hj_date(fields["回国时间"].replace("年", ".").replace("月", ""))

    # 从 OCR 勾选框推断专业领域/研究类型
    for label, key in (
        ("化学", "专业领域勾选"), ("材料科学", "专业领域勾选"), ("工程科学", "专业领域勾选"),
        ("新能源", "关键技术勾选"), ("应用基础研究", "研究类型勾选"),
    ):
        if re.search(r"☑\s*" + label, text):
            fields[key] = label

    fields["专长及代表性成果"] = _section_between(
        text,
        ("一、个人简介及贡献", "一、亮点履历", "1. 亮点履历", "申报人是国际能源"),
        ("2.代表性科研项目", "Grants (As a Leader", "代表性科研项目（主持"),
    ) or _section_between(
        text,
        ("科研情况 (请概述", "Research Status (Please"),
        ("2.代表性科研项目", "Grants (As a Leader"),
    ) or str(data.get("专长及代表性成果") or data.get("科研情况") or "")[:8000]

    fields["工作设想"] = _section_between(
        text,
        ("一、研究背景及意义", "一、研究背景", "面向全球"),
        ("申报单位（用人单位）情况部分", "申报单位（用人单位）情况\n", "用人单位类型"),
    ) or str(data.get("工作设想") or "")[:8000]

    fields["用人单位简介"] = _section_between(
        text,
        ("申报单位（用人单位）简介", "3.申报单位（用人单位）简介", "主要指实验室建设情况"),
        ("申报单位（用人单位）意见部分", "申报单位（用人单位）意见", "1. 推荐理由"),
    )

    fields["推荐理由"] = _section_between(
        text,
        ("1. 推荐理由", "1.推荐理由", "推荐理由及引进的必要性"),
        ("2. 支持条件", "2.支持条件", "支持条件（包括工作和生活"),
    )

    fields["支持条件"] = _section_between(
        text,
        ("2. 支持条件", "2.支持条件", "支持条件（包括工作和生活"),
        ("申报人有关信息属实", "主要负责人签字", "申报渠道意见"),
    )

    return _finalize_hj_fields(fields, text)


def _seg_matches_kw(seg: str, kw: str) -> bool:
    sn, kn = _norm_cmp(seg), _norm_cmp(kw)
    if not sn or not kn:
        return False
    if sn == kn:
        return True
    if len(sn) >= len(kn) and sn.startswith(kn):
        return True
    if len(kn) > len(sn) and kn.startswith(sn) and len(sn) >= 4:
        return True
    if len(kn) >= 3 and kn in sn and len(sn) <= len(kn) + 6:
        return True
    return False


def _mark_checkbox(text: str, keywords: tuple[str, ...]) -> str:
    if not text.strip():
        return text
    out = re.sub(r"☑", "□", str(text))
    for kw in sorted((k for k in keywords if k), key=lambda x: len(_norm_cmp(x)), reverse=True):
        for m in re.finditer(r"[□☐口]\s*([^□☐口\n]*)", out):
            seg = m.group(1)
            if _seg_matches_kw(seg, kw):
                out = out[: m.start()] + "☑" + seg + out[m.end() :]
                break
    return out


def _fill_cover_paragraphs(doc: Document, fields: dict[str, str]) -> None:
    scalar_rules = (
        (re.compile(r"(申报人姓名[^:：\n]*[:：])\s*.+$", re.I), "有效证件姓名"),
        (re.compile(r"(申报单位[^:：\n]*[:：])\s*.+$", re.I), "申报单位"),
        (re.compile(r"(实验室名称[^:：\n]*[:：])\s*.+$", re.I), "实验室名称"),
        (re.compile(r"(单位联系人[^:：\n]*[:：])\s*.+$", re.I), "单位联系人"),
        (re.compile(r"(联系人电话[^:：\n]*[:：])\s*.+$", re.I), "联系人电话"),
        (re.compile(r"(填表日期[^:：\n]*[:：])\s*.+$", re.I), "填表日期"),
        (re.compile(r"(项目类别[^:：\n]*[:：])\s*.+$", re.I), "项目类别"),
    )
    _discipline_markers = ("数学", "物理", "化学", "工程科学", "材料科学", "生命科学", "医学", "环境", "信息科学")
    for p in doc.paragraphs:
        cur = str(p.text or "")
        if not cur.strip():
            continue
        compact = _norm_cmp(cur)
        for pat, key in scalar_rules:
            val = str(fields.get(key) or "").strip()
            if val and pat.search(cur):
                cur = pat.sub(lambda m: m.group(1) + " " + val, cur, count=1)
                break
        kw_major = fields.get("专业领域勾选") or ""
        if kw_major and "□" in cur and any(x in compact for x in _discipline_markers):
            cur = _mark_checkbox(cur, (kw_major,))
        if "前沿领域" in compact:
            cur = _mark_checkbox(cur, (fields.get("关键技术勾选") or "新能源",))
        if "关键核心技术" in compact:
            cur = _mark_checkbox(cur, (fields.get("关键技术勾选") or "新能源",))
        if "实验室" in compact and "类别" in compact:
            kw_lab = fields.get("实验室类别勾选") or ""
            if kw_lab:
                cur = _mark_checkbox(cur, (kw_lab,))
        if "项目类别" in compact and fields.get("项目类别"):
            cur = _mark_checkbox(cur, (fields.get("项目类别勾选") or "创新项目",))
        if cur != p.text:
            p.text = cur
    # 二级学科：参考正式申报书，写在标签段后第一个空段
    disc = fields.get("二级学科及代码") or ""
    if disc:
        for i, p in enumerate(doc.paragraphs):
            t = str(p.text or "")
            if "二级学科" in t and "代码" in t and not re.search(r"\d{4,}", t):
                for j in range(i + 1, min(i + 4, len(doc.paragraphs))):
                    nxt = doc.paragraphs[j]
                    if not str(nxt.text or "").strip():
                        nxt.text = disc
                        break
                else:
                    p.text = t.rstrip() + "\n" + disc
                break


def _fill_hj_table0(table, fields: dict[str, str]) -> None:
    if not table.rows:
        return
    for row in table.rows[:2]:
        cells = _distinct_cells(row)
        row_n = _norm(" ".join(c.text for c in cells))
        if "nameofvalidcertificate" in row_n or "nameofid" in row_n:
            if fields.get("有效证件姓名") and len(cells) >= 3:
                _write_cell(cells[2], fields["有效证件姓名"])
        if "chinese" in row_n or "音译" in row_n:
            if fields.get("中文（音译）名") and len(cells) >= 3:
                _write_cell(cells[2], fields["中文（音译）名"])

    if len(table.rows) > 2:
        cells = _distinct_cells(table.rows[2])
        if fields.get("性别") and len(cells) >= 2:
            t = cells[1].text
            if fields["性别"] == "男":
                if "□" in t or "☑" in t:
                    t = re.sub(r"[□☐]\s*男", "☑ 男", t)
                else:
                    t = "☑ 男"
            elif fields["性别"] == "女":
                if "□" in t or "☑" in t:
                    t = re.sub(r"[□☐]\s*女", "☑ 女", t)
                else:
                    t = "☑ 女"
            _write_cell(cells[1], t)
        if fields.get("出生日期") and len(cells) >= 4:
            _write_cell(cells[3], fields["出生日期"])
        if fields.get("出生国家（地区）") and len(cells) >= 6:
            _write_cell(cells[5], fields["出生国家（地区）"])

    for row in table.rows:
        cells = _distinct_cells(row)
        row_n = _norm(" ".join(c.text for c in cells))
        if "foreignnationality" in row_n or "外籍" in row_n:
            if fields.get("国籍地区"):
                wrote = False
                for i, c in enumerate(cells):
                    t = c.text.strip()
                    if not t or _looks_like_label(t) or "华裔" in t:
                        continue
                    if t.startswith(("□", "☑", "☐")):
                        continue
                    if len(t) < 24:
                        _write_cell(c, fields["国籍地区"])
                        wrote = True
                        break
                if not wrote:
                    for i, c in enumerate(cells):
                        if i > 0 and not _looks_like_label(c.text) and "华裔" not in c.text:
                            if not c.text.strip() or len(c.text.strip()) < 3:
                                _write_cell(c, fields["国籍地区"])
                                break
            if "外籍" in row_n:
                for c in cells:
                    if "外籍" in c.text or "Foreign" in c.text:
                        _write_cell(c, _mark_checkbox(c.text, ("外籍", "Foreign Nationality")))
                        break
        if "passport" in row_n:
            if fields.get("证件类型", "").find("护照") >= 0:
                for c in cells:
                    if "passport" in _norm(c.text) or "护照" in c.text:
                        _write_cell(c, _mark_checkbox(c.text, ("护照", "Passport")))
                if fields.get("证件号码"):
                    for c in reversed(cells):
                        if not _looks_like_label(c.text) and "passport" not in _norm(c.text):
                            _write_cell(c, fields["证件号码"])
                            break


def _fill_hj_table1(table, fields: dict[str, str]) -> None:
    if len(table.rows) < 12:
        return
    rows = table.rows
    if fields.get("手机号"):
        c = _distinct_cells(rows[0])
        if len(c) >= 3:
            _write_cell(c[2], fields["手机号"])
    if fields.get("电子邮箱"):
        c = _distinct_cells(rows[1])
        if len(c) >= 2:
            _write_cell(c[-1], fields["电子邮箱"])
    for ri in (2, 3):
        c = _distinct_cells(rows[ri])
        row_n = _norm(" ".join(x.text for x in c))
        if "cn" in row_n or "中文" in row_n:
            if fields.get("最高学位中文") and len(c) >= 2:
                _write_cell(c[-1], fields["最高学位中文"])
        elif fields.get("最高学位英文") and len(c) >= 2:
            _write_cell(c[-1], fields["最高学位英文"])
    for ri in (4, 5):
        c = _distinct_cells(rows[ri])
        row_n = _norm(" ".join(x.text for x in c))
        if "cn" in row_n or "中文" in row_n:
            if fields.get("回国前单位职务中文") and len(c) >= 2:
                _write_cell(c[-1], fields["回国前单位职务中文"])
        elif fields.get("回国前单位职务英文") and len(c) >= 2:
            _write_cell(c[-1], fields["回国前单位职务英文"])
    c6 = _distinct_cells(rows[6])
    title = str(fields.get("相当于国内职称") or "").strip()
    if title:
        if is_academic_title(title) and len(c6) >= 3:
            _write_cell(c6[2], title)
        else:
            for cell in c6:
                if re.search(r"[□☐口☑]", cell.text):
                    _write_cell(cell, _mark_checkbox(cell.text, (title,)))
    c7 = _distinct_cells(rows[7])
    if fields.get("回国前单位类型"):
        for cell in c7:
            if re.search(r"[□☐口☑]", cell.text):
                _write_cell(cell, _mark_checkbox(cell.text, (fields["回国前单位类型"], "Enterprise")))
    c8 = _distinct_cells(rows[8])
    for ci, fk in ((1, "回国前所在地"), (3, "拟落地省"), (5, "拟落地市")):
        if fields.get(fk) and ci < len(c8):
            _write_cell(c8[ci], fields[fk])
    c9 = _distinct_cells(rows[9])
    if fields.get("现工作单位") and len(c9) >= 2:
        _write_cell(c9[1], fields["现工作单位"])
    if fields.get("引进职务") and len(c9) >= 4:
        _write_cell(c9[3], fields["引进职务"])
    c10 = _distinct_cells(rows[10])
    if fields.get("任职单位地址") and len(c10) >= 2:
        _write_cell(c10[1], fields["任职单位地址"])
    c11 = _distinct_cells(rows[11])
    if fields.get("回国时间") and len(c11) >= 2:
        _write_cell(c11[1], fields["回国时间"])


def _fill_scalar_rows(doc: Document, fields: dict[str, str], skip_tables: set[int] | None = None) -> int:
    skip = skip_tables or set()
    n = 0
    for ti, table in enumerate(doc.tables):
        if ti in skip:
            continue
        for row in table.rows:
            cells = _distinct_cells(row)
            if not cells:
                continue
            row_text = " ".join(c.text for c in cells)
            key = _row_field_key(row_text)
            if not key:
                continue
            val = str(fields.get(key) or "").strip()
            if not val:
                continue
            label_last = -1
            for i, cell in enumerate(cells):
                if _looks_like_label(cell.text):
                    label_last = i
            wrote = False
            for i, cell in enumerate(cells):
                if i <= label_last or _looks_like_label(cell.text):
                    continue
                _write_cell(cell, val)
                wrote = True
            if wrote:
                n += 1
    return n


def _row_field_key(row_text: str) -> str | None:
    n = _norm(row_text)
    rules = (
        (("nameofvalidcertificate", "nameofid"), "有效证件姓名"),
        (("chinesetransliteration", "nameofchinese"), "中文（音译）名"),
        (("contactperson",), "单位联系人"),
        (("telephonenumber",), "联系人电话"),
        (("laboratory",), "实验室名称"),
        (("employer",), "申报单位"),
    )
    for keys, field in rules:
        if any(k in n for k in keys):
            return field
    return None


def _fill_hj_table2(table, edu: list[dict], work: list[dict]) -> None:
    edu_start, edu_end, work_start = _edu_work_row_bounds(table)
    for i, d in enumerate(edu[: max(0, edu_end - edu_start)]):
        ri = edu_start + i
        if ri >= edu_end or ri >= len(table.rows):
            break
        cells = _distinct_cells(table.rows[ri])
        if len(cells) < 5:
            continue
        vals = [
            d.get("起止时间", ""),
            d.get("所在国家", ""),
            d.get("校名称", ""),
            d.get("专业领域", ""),
            d.get("学位", ""),
        ]
        for off, val in enumerate(vals, start=1):
            if off < len(cells):
                _write_cell(cells[off], str(val or "").strip())
    max_work = len(table.rows) - work_start
    for i, d in enumerate(work[:max_work]):
        ri = work_start + i
        if ri >= len(table.rows):
            break
        cells = _distinct_cells(table.rows[ri])
        if len(cells) < 5:
            continue
        vals = [
            d.get("起止时间", ""),
            d.get("所在国家", ""),
            d.get("工作单位", ""),
            d.get("担任职务", ""),
            d.get("任职情况", ""),
        ]
        for off, val in enumerate(vals, start=1):
            if off < len(cells):
                _write_cell(cells[off], str(val or "").strip())


def _fill_paper_table(table, papers: list[dict], start: int = 0) -> int:
    n = 0
    for ri in range(1, len(table.rows)):
        pi = start + n
        if pi >= len(papers):
            break
        cells = _distinct_cells(table.rows[ri])
        if len(cells) < 6:
            continue
        d = papers[pi]
        vals = ["", d.get("发表时间", ""), d.get("论文题目", ""), d.get("发表载体", ""), d.get("排序", ""), d.get("角色", "")]
        for ci, val in enumerate(vals):
            if ci < len(cells) and str(val or "").strip():
                _write_cell(cells[ci], str(val))
        n += 1
    return n


def _fill_expertise_cell(cell, body: str) -> None:
    raw = str(cell.text or "")
    title = "专长及代表性成果"
    if title in raw:
        head = raw.split(title)[0] + title
    else:
        head = title + "(Expertise and Achievements)"
    intro = "所从事的专业领域及取得的成绩描述（概述与所在或拟聘实验室相关的研究领域、方向及取得的成就，5000字以内）"
    if intro not in body[:120]:
        body = intro + "\n" + body
    cell.text = head + "\n" + body.strip()


def _fill_narrative_tables(doc: Document, fields: dict[str, str]) -> None:
    kind = _hj_template_kind(doc)
    exp = fields.get("专长及代表性成果") or ""
    wp = fields.get("工作设想") or ""
    for head in ("一、研究背景", "面向全球", "1. 研究背景"):
        if head in wp:
            wp = wp[wp.find(head):]
            break

    if kind == "compact":
        if len(doc.tables) > 3 and exp:
            if len(doc.tables[3].rows) > 1:
                _write_cell(doc.tables[3].rows[1].cells[0], exp)
            else:
                _fill_expertise_cell(doc.tables[3].rows[0].cells[0], exp)
        if len(doc.tables) > 5 and wp:
            if len(doc.tables[5].rows) > 1:
                _write_cell(doc.tables[5].rows[1].cells[0], wp)
            else:
                _write_cell(doc.tables[5].rows[0].cells[0], wp)
        if len(doc.tables) > 6 and fields.get("用人单位简介"):
            _write_cell(doc.tables[6].rows[0].cells[0], fields["用人单位简介"])
        if len(doc.tables) > 8:
            cell = doc.tables[8].rows[-1].cells[0]
            parts = []
            if fields.get("推荐理由"):
                parts.append("1.推荐理由\n" + fields["推荐理由"])
            if fields.get("支持条件"):
                parts.append("2.支持条件\n" + fields["支持条件"])
            if parts:
                cell.text = (cell.text.split("\n")[0] if cell.text.strip() else "申报单位(用人单位)意见") + "\n\n" + "\n\n".join(parts)
        return

    if len(doc.tables) > 4 and exp:
        _fill_expertise_cell(doc.tables[4].rows[0].cells[0], exp)
    if len(doc.tables) > 5 and fields.get("研究类型勾选"):
        t5 = doc.tables[5].rows[0].cells[0].text
        doc.tables[5].rows[0].cells[0].text = _mark_checkbox(t5, (fields["研究类型勾选"],))
    if len(doc.tables) > 12 and wp and len(doc.tables[12].rows) > 1:
        _write_cell(doc.tables[12].rows[1].cells[0], wp)
    if len(doc.tables) > 14 and fields.get("用人单位简介"):
        t = doc.tables[14].rows[0].cells[0].text
        if fields.get("用人单位类型"):
            t = _mark_checkbox(t, (fields["用人单位类型"],))
        if fields.get("申报单位上级"):
            t = t + "\n2.申报单位(用人单位)的上级部门/地方：" + fields["申报单位上级"]
        t = t + "\n3.申报单位(用人单位)简介\n" + fields["用人单位简介"]
        doc.tables[14].rows[0].cells[0].text = t
    if len(doc.tables) > 15:
        cell = doc.tables[15].rows[-1].cells[0]
        parts = []
        if fields.get("推荐理由"):
            parts.append("1.推荐理由\n" + fields["推荐理由"])
        if fields.get("支持条件"):
            parts.append("2.支持条件\n" + fields["支持条件"])
        if parts:
            head = cell.text.split("\n")[0] if cell.text.strip() else "申报单位(用人单位)意见"
            cell.text = head + "\n\n" + "\n\n".join(parts)


def _fill_hj_lists(doc: Document, data: dict, raw_text: str, fields: dict[str, str]) -> dict:
    parser_edu = list(data.get("主要学历") or [])
    parser_work = list(data.get("工作经历") or [])
    ocr_edu, ocr_work = _parse_list_lines(raw_text)
    edu = _fill_edu_gaps(_merge_timeline_rows(ocr_edu, parser_edu, "edu"))
    work = _merge_timeline_rows(ocr_work, parser_work, "work")
    fields = _enrich_hj_fields(fields, edu, work)
    if len(doc.tables) >= 3:
        _fill_hj_table0(doc.tables[0], fields)
        _fill_hj_table1(doc.tables[1], fields)
        _fill_hj_table2(doc.tables[2], edu, work)
    papers = _parse_papers(raw_text)
    filled_papers = 0
    if len(doc.tables) > 8:
        filled_papers += _fill_paper_table(doc.tables[8], papers, 0)
    if len(doc.tables) > 9:
        filled_papers += _fill_paper_table(doc.tables[9], papers, filled_papers)
    return {"edu": len(edu), "work": len(work), "papers": filled_papers}


def _fill_qm_list_table(table, rows: list[dict]) -> int:
    if not rows:
        return 0
    n = 0
    for ri in range(1, len(table.rows)):
        if n >= len(rows):
            break
        cells = _distinct_cells(table.rows[ri])
        if len(cells) < 6:
            continue
        d = rows[n]
        vals = [
            str(n + 1),
            d.get("起止时间", ""),
            d.get("所在国家", ""),
            d.get("校名称", "") or d.get("工作单位", ""),
            d.get("专业领域", "") or d.get("担任职务", ""),
            d.get("学位", "") or d.get("任职情况", ""),
        ]
        for ci, val in enumerate(vals):
            if str(val or "").strip():
                _write_cell(cells[ci], str(val))
        n += 1
    return n


def _fill_qm_lists(doc: Document, data: dict, raw_text: str) -> None:
    parser_edu = list(data.get("主要学历") or [])
    parser_work = list(data.get("工作经历") or [])
    ocr_edu, ocr_work = _parse_list_lines(raw_text)
    edu = _merge_timeline_rows(ocr_edu, parser_edu, "edu")
    work = _merge_timeline_rows(ocr_work, parser_work, "work")
    if len(doc.tables) >= 3:
        _fill_qm_list_table(doc.tables[2], edu)
    if len(doc.tables) >= 4:
        _fill_qm_list_table(doc.tables[3], work)


def fill_declaration_template(mode: str, data: dict, raw_text: str, out_path: Path) -> dict:
    """复制 QM/HJ 模板并填入 data，写出 out_path。"""
    tpl = template_path(mode)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tpl, out_path)
    doc = Document(str(out_path))
    if str(mode).upper() == "HJ":
        from .hj_template_blank import blank_hj_document

        blank_hj_document(doc)
    fields = _build_fields(data, raw_text)
    if str(mode).upper() == "HJ":
        ocr_edu, ocr_work = _parse_list_lines(raw_text)
        fields = _enrich_hj_fields(fields, ocr_edu, ocr_work)
    _fill_cover_paragraphs(doc, fields)
    list_stats = {}
    if str(mode).upper() == "HJ":
        list_stats = _fill_hj_lists(doc, data, raw_text, fields)
        _fill_narrative_tables(doc, fields)
        n_scalar = _fill_scalar_rows(doc, fields, skip_tables={0, 1, 2, 4, 8, 9, 12, 14, 15})
    else:
        _fill_qm_lists(doc, data, raw_text)
        n_scalar = _fill_scalar_rows(doc, fields)
    doc.save(str(out_path))
    return {
        "ok": True,
        "template": str(tpl),
        "fields": len(fields),
        "scalarRows": n_scalar,
        "eduRows": list_stats.get("edu", 0),
        "workRows": list_stats.get("work", 0),
        "paperRows": list_stats.get("papers", 0),
        "hasExpertise": bool(fields.get("专长及代表性成果")),
        "hasWorkPlan": bool(fields.get("工作设想")),
        "name": fields.get("有效证件姓名") or "",
        "enterprise": fields.get("申报单位") or fields.get("实验室名称") or "",
    }
