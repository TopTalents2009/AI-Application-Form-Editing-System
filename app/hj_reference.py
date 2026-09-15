# -*- coding: utf-8 -*-
"""HJ 正式申报书（work/HJ 参考样例）中的栏位书写规范。"""
from __future__ import annotations

import re

# 参考样例中常见的院校/单位 → 国家（地区）
SCHOOL_COUNTRY_HINTS = (
    (re.compile(r"Anna\s*University|安娜大学", re.I), "印度"),
    (re.compile(r"Chonnam|全南", re.I), "韩国"),
    (re.compile(r"Brighton", re.I), "英国"),
    (re.compile(r"Waterloo|滑铁卢", re.I), "加拿大"),
    (re.compile(r"Western|韦仕敦", re.I), "加拿大"),
    (re.compile(r"AdvEn|阿德文", re.I), "加拿大"),
    (re.compile(r"Ly[oó]n|里昂", re.I), "法国"),
    (re.compile(r"Lebanese|黎巴嫩大学", re.I), "黎巴嫩"),
    (re.compile(r"Rome|罗马", re.I), "意大利"),
    (re.compile(r"Shivaji|希瓦吉", re.I), "印度"),
    (re.compile(r"Calgary|卡尔加里", re.I), "加拿大"),
    (re.compile(r"McMaster|麦克马斯特", re.I), "加拿大"),
    (re.compile(r"Munich|慕尼黑", re.I), "德国"),
    (re.compile(r"Cambridge|剑桥", re.I), "英国"),
    (re.compile(r"Alexandria|亚历山大", re.I), "埃及"),
    (re.compile(r"Granada|格拉纳达", re.I), "西班牙"),
    (re.compile(r"Tehran|德黑兰", re.I), "伊朗"),
    (re.compile(r"Isfahan|伊斯法罕", re.I), "伊朗"),
    (re.compile(r"King Abdullah|阿卜杜拉国王", re.I), "沙特阿拉伯"),
    (re.compile(r"National Center for Nu|国家核研究中心", re.I), "波兰"),
)

_ENTITY_FIXES = (
    (re.compile(r"Western\s+of\s+University", re.I), "Western University"),
    (re.compile(r"University\s+of\s+Western\s+of\s+University", re.I), "University of Western Ontario"),
)

_MAJOR_EN = (
    (re.compile(r"应用化学与工程"), "Applied Chemistry and Engineering"),
    (re.compile(r"化学与电化学工程"), "Chemical and Electrochemical Engineering"),
    (re.compile(r"电化学工程"), "Electrochemical Engineering"),
    (re.compile(r"化工与工程"), "Chemical Engineering"),
    (re.compile(r"理论物理化学"), "Theoretical Physical Chemistry"),
    (re.compile(r"物理学"), "Physics"),
)

_OCR_EN_FIXES = (
    (re.compile(r"Universit\s+y", re.I), "University"),
    (re.compile(r"Chemic\s+al", re.I), "Chemical"),
    (re.compile(r"\bo\s+f\b", re.I), "of"),
    (re.compile(r"Canad\s+a", re.I), "Canada"),
    (re.compile(r"Engin\s+eering", re.I), "Engineering"),
    (re.compile(r"Resear\s+ch", re.I), "Research"),
)

_ACADEMIC_TITLES = frozenset({"教授", "副教授", "讲师", "博士后"})


def fix_ocr_english(text: str) -> str:
    s = re.sub(r"\s+", " ", str(text or "").strip())
    for pat, repl in _OCR_EN_FIXES:
        s = pat.sub(repl, s)
    for pat, repl in _ENTITY_FIXES:
        s = pat.sub(repl, s)
    return s.strip()


def fix_entity_name(text: str) -> str:
    s = str(text or "").strip()
    for pat, repl in _ENTITY_FIXES:
        s = pat.sub(repl, s)
    return s.strip()


def major_to_en(major_cn: str) -> str:
    s = str(major_cn or "").strip()
    if not s:
        return ""
    for pat, en in _MAJOR_EN:
        if pat.search(s):
            return en
    if re.search(r"[A-Za-z]", s):
        return fix_ocr_english(s)
    return ""


def cn_list_join(*parts: str) -> str:
    """参考仙湖/南京样例 table1：中文多项用分号连接。"""
    items = [str(p or "").strip() for p in parts if str(p or "").strip()]
    if not items:
        return ""
    return "；".join(items)


def en_list_join(*parts: str) -> str:
    """参考仙湖/南京样例 table1：英文多项用 ; 连接。"""
    items = [fix_ocr_english(str(p or "").strip()) for p in parts if str(p or "").strip()]
    if not items:
        return ""
    return "; ".join(items)


def split_bilingual(text: str) -> tuple[str, str]:
    s = fix_entity_name(fix_ocr_english(str(text or "").strip()))
    if not s:
        return "", ""
    if "/" in s:
        a, b = s.split("/", 1)
        return a.strip(), fix_ocr_english(b.strip())
    m = re.match(r"^(.+?)\s*[（(]([^）)]+)[）)]\s*$", s)
    if m:
        return m.group(1).strip(), fix_ocr_english(m.group(2).strip())
    if re.search(r"[\u4e00-\u9fff]", s) and re.search(r"[A-Za-z]", s):
        m = re.match(r"^(.+?)([A-Za-z].+)$", s)
        if m:
            return m.group(1).strip(), fix_ocr_english(m.group(2).strip())
    return s, ""


def degree_cn_en(degree: str) -> tuple[str, str]:
    s = str(degree or "").strip()
    if not s:
        return "", ""
    if "/" in s:
        a, b = s.split("/", 1)
        return a.strip(), fix_ocr_english(b.strip())
    m = re.match(r"^(学士|硕士|博士)\s*[（(]([^）)]+)[）)]", s)
    if m:
        return m.group(1), fix_ocr_english(m.group(2))
    m = re.search(r"(学士|硕士|博士)", s)
    if m:
        cn = m.group(1)
        en = {"学士": "Bachelor", "硕士": "Master", "博士": "Doctor"}[cn]
        return cn, en
    if re.search(r"\bPhD\b|\bPh\.D\.?\b|\bDoctor\b", s, re.I):
        return "博士", "PhD"
    if re.search(r"\bMaster\b|\bMaste\b", s, re.I):
        return "硕士", "Master"
    if re.search(r"\bBachelor\b", s, re.I):
        return "学士", "Bachelor"
    return s, s


def format_hj_degree_cell(degree: str) -> str:
    """HJ table2 学位栏：学士（Bachelor） / 博士（PhD）。"""
    cn, en = degree_cn_en(degree)
    if not cn:
        return str(degree or "").strip()
    en = fix_ocr_english(en)
    if en in {"Doctor", "doctor"}:
        en = "PhD"
    if en and en != cn and re.search(r"[A-Za-z]", en):
        return f"{cn}（{en}）"
    return cn


_POSITION_PAIRS = (
    (re.compile(r"Chief\s*Scientist|首席科学家", re.I), "首席科学家/Chief Scientist"),
    (re.compile(r"Assistant\s*Professor|助理教授", re.I), "助理教授/Assistant Professor"),
    (re.compile(r"Postdoctoral(?:\s+Research)?\s+Fellow|博士后", re.I), "博士后/Postdoctoral Research Fellow"),
    (re.compile(r"Research\s*Scientist|研究科学家", re.I), "研究科学家/Research Scientist"),
    (re.compile(r"Chief\s*Researcher|首席研究员", re.I), "首席研究员/Chief Researcher"),
    (re.compile(r"\bResearcher\b|研究员", re.I), "研究员/Researcher"),
)


def format_hj_position_cell(text: str) -> str:
    """HJ table2 职务栏：中文/English（参考洲瓴/仙湖样例）。"""
    s = fix_ocr_english(str(text or "").strip())
    if not s:
        return ""
    s = re.sub(r"\s*/\s*", " ", s)
    s = re.sub(r"\s+", " ", s)
    for pat, pair in _POSITION_PAIRS:
        if pat.search(s):
            return pair
    if re.search(r"[\u4e00-\u9fff]", s) and re.search(r"[A-Za-z]", s):
        cn, en = split_bilingual(s)
        if cn and en:
            return f"{cn}/{en}"
    return s


def highest_degree_from_edu_row(row: dict) -> tuple[str, str]:
    """从最高学历行生成 table1 最终毕业院校及专业、学位（中英）。"""
    school_cn, school_en = split_bilingual(str(row.get("校名称") or ""))
    major_cn = str(row.get("专业领域") or "").strip()
    major_en = major_to_en(major_cn) or major_cn
    deg_cn, deg_en = degree_cn_en(str(row.get("学位") or ""))
    cn = cn_list_join(school_cn, major_cn, deg_cn)
    en = en_list_join(school_en or school_cn, major_en, deg_en)
    return cn, en


def format_bilingual_pair(cn: str, en: str) -> tuple[str, str]:
    """单位/职务：中文；英文（参考样例 table1 分行填写）。"""
    cn_s = re.sub(r"[、,，]\s*", "；", str(cn or "").strip())
    en_s = fix_ocr_english(str(en or "").strip())
    en_s = re.sub(r"[、，]\s*", "; ", en_s)
    en_s = re.sub(r",\s*", "; ", en_s)
    if cn_s and not en_s and "；" in cn_s:
        parts = [p.strip() for p in cn_s.split("；", 1)]
        if len(parts) == 2 and re.search(r"[A-Za-z]", parts[1]):
            return parts[0], parts[1]
    return cn_s, en_s


def split_certificate_name(name: str) -> tuple[str, str]:
    """有效证件姓名仅保留英文；中文音译名单独提取。"""
    s = re.sub(r"\s+", " ", str(name or "").strip())
    m = re.match(r"^(.+?)\s*[（(]([^）)]+)[）)]\s*$", s)
    if m:
        en = fix_ocr_english(m.group(1).strip())
        cn = re.sub(r"\s+", "", m.group(2).strip())
        return en, cn
    m = re.match(r"^([A-Za-z][A-Za-z\s'\"]+?)\s*[\"'“”]\s*[（(]([^）)]+)", s)
    if m:
        return fix_ocr_english(m.group(1).strip()), re.sub(r"\s+", "", m.group(2))
    return fix_ocr_english(s), ""


def fix_email_ocr(email: str) -> str:
    s = str(email or "").strip().replace(" ", "")
    if not s:
        return ""
    s = re.sub(r"@?gmailcom$", "@gmail.com", s, flags=re.I)
    if "@" not in s and re.search(r"gmail", s, re.I):
        s = s.replace("gmailcom", "@gmail.com")
    return s


def normalize_hj_cn_field(text: str) -> str:
    s = re.sub(r"\s+", "", str(text or "").strip())
    return re.sub(r"[、,，]\s*", "；", s)


def normalize_hj_en_field(text: str) -> str:
    s = fix_ocr_english(str(text or "").strip())
    s = re.sub(r"[、，]\s*", "; ", s)
    return re.sub(r",\s*", "; ", s)


def is_academic_title(title: str) -> bool:
    s = re.sub(r"\s+", "", str(title or ""))
    return bool(s) and s in _ACADEMIC_TITLES
