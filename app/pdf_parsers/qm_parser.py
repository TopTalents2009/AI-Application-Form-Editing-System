# -*- coding: utf-8 -*-
"""QM（启明）申报书 OCR/抽取文本解析（轻量版，供 template_fill 使用）。"""
from __future__ import annotations

import re

from ..template_fill import _section_between


def _parse_qm_edu_work(text: str) -> tuple[list[dict], list[dict]]:
    """从启明版式竖排/分段文本粗提学历与工作行。"""
    edu, work = [], []
    lines = [ln.strip() for ln in str(text or "").splitlines()]
    mode = ""
    buf: list[str] = []
    for ln in lines:
        if re.search(r"教育经历", ln):
            mode = "edu"
            buf = []
            continue
        if re.search(r"工作经历", ln):
            if mode == "edu" and buf:
                edu.append(_flush_qm_edu(buf))
            mode = "work"
            buf = []
            continue
        if re.search(r"^(论文|项目|代表性|主要技术)", ln):
            if mode == "work" and buf:
                work.append(_flush_qm_work(buf))
            mode = ""
            buf = []
            continue
        if mode:
            if ln:
                buf.append(ln)
    if mode == "work" and buf:
        work.append(_flush_qm_work(buf))
    return edu, work


def _flush_qm_edu(buf: list[str]) -> dict:
    blob = " ".join(buf)
    m = re.search(r"(\d{4}[-./]\d{1,2}(?:[-./]\d{1,2})?)\s*[-–—至~]+\s*(\d{4}[-./]\d{1,2}(?:[-./]\d{1,2})?|至今)", blob)
    return {
        "起止时间": (m.group(1) + " - " + m.group(2)) if m else "",
        "所在国家": "",
        "校名称": blob[:120],
        "专业领域": "",
        "学位": "",
    }


def _flush_qm_work(buf: list[str]) -> dict:
    blob = " ".join(buf)
    m = re.search(r"(\d{4}[-./]\d{1,2}(?:[-./]\d{1,2})?)\s*[-–—至~]+\s*(\d{4}[-./]\d{1,2}(?:[-./]\d{1,2})?|至今)", blob)
    return {
        "起止时间": (m.group(1) + " - " + m.group(2)) if m else "",
        "所在国家": "",
        "工作单位": blob[:120],
        "担任职务": "",
        "任职情况": "全职",
    }


def parse_qm(text: str) -> dict:
    raw = str(text or "")
    edu, work = _parse_qm_edu_work(raw)
    basic = _section_between(
        raw,
        ("申报人基本情况", "申报人姓名", "一、申报人基本情况"),
        ("引进企业基本情况", "二、", "教育经历"),
    )
    return {
        "申报人基本情况": basic,
        "主要学历": edu,
        "工作经历": work,
        "科研情况": _section_between(raw, ("代表性论文", "论文"), ("项目", "其他")),
        "工作设想": _section_between(raw, ("工作目标", "可行性论证", "三年目标"), ("推荐理由", "企业情况")),
    }
