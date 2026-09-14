# -*- coding: utf-8 -*-
"""扫描件 OCR 文本清洗：转为 hj_parser / qm_parser 可识别的「标签：值」形态。"""
from __future__ import annotations

import re

_PAGE_MARK = re.compile(r"^【第\d+页】\s*$")
_PAGE_FOOT = re.compile(r"^\d{1,3}/\d{1,3}$")
_STAMP_LINE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}\s+")
_INLINE_KV = re.compile(r"^(.{2,60}?)[：:]\s*(.+)$")

# OCR 表格行「英文标签\t值」或「英文标签\t中文标签\t值」→ 标准中文字段名
_TAB_LABEL_MAP = (
    (re.compile(r"^Name of ID\b", re.I), "有效证件姓名"),
    (re.compile(r"^Name of Chinese Transliteration\b", re.I), "中文（音译）名"),
    (re.compile(r"^Gender\b", re.I), "性别"),
    (re.compile(r"^Date of Birth\b", re.I), "出生日期"),
    (re.compile(r"^Place of Birth\b", re.I), "出生国家（地区）"),
    (re.compile(r"^Other ID NO\.\b", re.I), "证件号码"),
    (re.compile(r"^ID NO\.\b", re.I), "证件号码"),
    (re.compile(r"^Mobile\b", re.I), "手机号"),
    (re.compile(r"^Email\b", re.I), "电子邮箱"),
    (re.compile(r"^中文\s*Cn\b", re.I), "最高学位中文"),
    (re.compile(r"^英文\s*En\b", re.I), "最高学位英文"),
    (re.compile(r"^Host Province\b", re.I), "拟落地省"),
    (re.compile(r"^Host City\b", re.I), "拟落地市"),
    (re.compile(r"^Position\b", re.I), "引进职务"),
    (re.compile(r"^Country \(Region\) of Residence\b", re.I), "回国（来华）前所在地"),
)

_SKIP_LINES = {
    "申报书", "Application Form", "个人信息部分", "申报人联系方式",
    "Contact Information", "证件类型", "ID Type", "其他证件类型", "Other ID Type",
}


def _clean_value(val: str) -> str:
    s = str(val or "").strip()
    if not s or s in _SKIP_LINES:
        return ""
    if s.startswith(("□", "☑", "■")) and len(s) < 8:
        return ""
    return s


def _pick_tab_value(parts: list[str]) -> str:
    for p in reversed(parts):
        v = _clean_value(p)
        if not v:
            continue
        if re.match(r"^(Name of|Date of|Place of|Other ID|ID NO|Mobile|Email|中文|英文|Host )", v, re.I):
            continue
        if v in ("有效证件姓名", "中文（音译）名", "性别", "出生日期", "证件号码"):
            continue
        return v
    return ""


def _map_tab_row(line: str) -> str | None:
    parts = [p.strip() for p in str(line or "").split("\t") if p.strip()]
    if len(parts) < 2:
        return None
    head = parts[0]
    for pat, label in _TAB_LABEL_MAP:
        if pat.search(head):
            val = _pick_tab_value(parts[1:])
            if val:
                return label + "：" + val
    return None


def _pending_cn_label(line: str) -> str | None:
    s = str(line or "").strip()
    if not s or "\t" in s or "：" in s or ":" in s:
        return None
    if s in _SKIP_LINES or re.match(r"^[A-Za-z].{0,40}$", s):
        return None
    if len(s) > 24:
        return None
    return s


def _regex_seed_fields(raw: str) -> list[str]:
    """从 OCR 原文用正则先抽出高频封面/个人信息字段。"""
    seeds: list[str] = []
    patterns = (
        (r"申报人有效证件姓名[^：\n]*[：:]\s*([^\n]+)", "申报人有效证件姓名"),
        (r"申报单位[^：\n]*[：:]\s*([^\n]+)", "申报单位"),
        (r"实验室名称[^：\n]*[：:]\s*([^\n]+)", "实验室名称"),
        (r"单位联系人[^：\n]*[：:]\s*([^\n]+)", "单位联系人"),
        (r"联系人电话[^：\n]*[：:]\s*([^\n]+)", "联系人电话"),
        (r"填表日期[^：\n\d]*[：:]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", "填表日期"),
        (r"填表日期[^：\n]*[：:]\s*([^\n]+)", "填表日期"),
        (r"中文\s*Cn\s*\n\s*([^\n]+)", "最高学位中文"),
        (r"英文\s*En\s*\n\s*([^\n]+)", "最高学位英文"),
        (r"Country \(Region\) of Residence\s*\n\s*([^\n]+)", "回国（来华）前所在地"),
        (r"Time of Coming to China\s*\n\s*([^\n]+)", "回国时间"),
        (r"(?i)Name of ID\t+([^\n\t]+)", "有效证件姓名"),
        (r"(?i)Name of Chinese Transliteration\t+([^\n\t]+)", "中文（音译）名"),
        (r"(?i)Gender\t+.*?([男女])", "性别"),
        (r"(?i)Date of Birth[^\n]*\t+(\d{8})", "出生日期"),
        (r"(?i)Place of Birth\t+([^\n\t]+)", "出生国家（地区）"),
        (r"(?i)Other ID NO\.\t+([A-Z0-9]+)", "证件号码"),
        (r"☑\s*护照[^\n]*?\t+([A-Z0-9]{6,})", "证件号码"),
        (r"(?i)Mobile\t+([+\d\- ]{8,})", "手机号"),
        (r"(?i)Email\t+([^\s\t\n]+@[^\s\t\n]+)", "电子邮箱"),
        (r"拟（现）任职单位名称[^\n]*\t+([^\n\t]+)", "现工作单位"),
        (r"职务（岗位）\s*\t+([^\n\t]+)", "引进职务"),
        (r"Host Province\t+([^\n\t]+)", "拟落地省"),
        (r"Host City\t+([^\n\t]+)", "拟落地市"),
    )
    seen = set()
    for pat, label in patterns:
        m = re.search(pat, raw)
        if not m:
            continue
        val = collapse(m.group(1))
        if not val or val in seen:
            continue
        if val.startswith(("□", "☑", "有效证件姓名", "Name of")):
            continue
        seeds.append(label + "：" + val)
        seen.add(val)
    return seeds


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def normalize_scanned_declaration_text(text: str, mode: str = "") -> str:
    """把 Gemini OCR 的申报书全文整理为解析器友好文本。"""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = _regex_seed_fields(raw)
    pending_cn = ""

    for raw_line in raw.split("\n"):
        line = re.sub(r"[ \t\u3000]+", " ", raw_line).strip()
        if not line:
            continue
        if _PAGE_MARK.match(line) or _PAGE_FOOT.match(line) or _STAMP_LINE.match(line):
            pending_cn = ""
            continue
        if line in _SKIP_LINES:
            pending_cn = ""
            continue

        m = _INLINE_KV.match(line)
        if m:
            out.append(m.group(1).strip() + "：" + m.group(2).strip())
            pending_cn = ""
            continue

        if "\t" in line:
            mapped = _map_tab_row(line)
            if mapped:
                out.append(mapped)
                pending_cn = ""
                continue
            parts = [p.strip() for p in line.split("\t") if p.strip()]
            val = _pick_tab_value(parts)
            if val and pending_cn:
                out.append(pending_cn + "：" + val)
                pending_cn = ""
                continue
            if val and len(parts) >= 2:
                out.append(parts[0] + "：" + val)
                pending_cn = ""
                continue

        cn = _pending_cn_label(line)
        if cn:
            pending_cn = cn
            continue

        if re.match(r"^[A-Za-z].{0,60}$", line):
            continue

        if pending_cn and len(line) <= 80 and not line.startswith(("□", "☑")):
            out.append(pending_cn + "：" + line)
            pending_cn = ""
            continue

        out.append(line)
        pending_cn = ""

    # 去重保序
    dedup: list[str] = []
    seen_lines = set()
    for line in out:
        key = re.sub(r"\s+", "", line)
        if key in seen_lines:
            continue
        seen_lines.add(key)
        dedup.append(line)
    return "\n".join(dedup)
