# -*- coding: utf-8 -*-
"""HJ 申报书 OCR/抽取文本解析（轻量版，供 template_fill 使用）。"""
from __future__ import annotations

from ..template_fill import _parse_list_lines, _section_between


def _rows_to_parser_edu(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows or []:
        rng = str(r.get("起止时间") or "").strip()
        start, end = "", ""
        if " - " in rng:
            start, end = [x.strip() for x in rng.split(" - ", 1)]
        elif "-" in rng:
            parts = [x.strip() for x in rng.split("-", 1)]
            if len(parts) == 2:
                start, end = parts
        out.append({
            "开始时间": start,
            "结束时间": end,
            "起止时间": rng,
            "所在国家": r.get("所在国家") or "",
            "校名称": r.get("校名称") or "",
            "学校名称": r.get("校名称") or "",
            "专业领域": r.get("专业领域") or "",
            "学位": r.get("学位") or "",
        })
    return out


def _rows_to_parser_work(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows or []:
        rng = str(r.get("起止时间") or "").strip()
        start, end = "", ""
        if " - " in rng:
            start, end = [x.strip() for x in rng.split(" - ", 1)]
        elif "-" in rng:
            parts = [x.strip() for x in rng.split("-", 1)]
            if len(parts) == 2:
                start, end = parts
        out.append({
            "开始时间": start,
            "结束时间": end,
            "起止时间": rng,
            "所在国家": r.get("所在国家") or "",
            "工作单位": r.get("工作单位") or "",
            "担任职务": r.get("担任职务") or "",
            "任职情况": r.get("任职情况") or "",
        })
    return out


def parse_hj(text: str) -> dict:
    """从规范化后的 HJ 全文抽出 template_fill 可用的结构化字段。"""
    raw = str(text or "")
    edu_rows, work_rows = _parse_list_lines(raw)
    expertise = _section_between(
        raw,
        (
            "一、个人简介及贡献", "一、亮点履历", "1. 亮点履历",
            "专长及代表性成果", "科研情况及代表性成果",
            "Research Expertise and Achievements",
        ),
        (
            "2.代表性科研项目", "Grants (As a Leader", "代表性科研项目（主持",
            "二、创新成果", "研究领域关键词",
        ),
    )
    work_plan = _section_between(
        raw,
        (
            "一、研究背景及意义", "一、研究背景", "（7）工作设想", "工作设想",
            "面向全球", "1. 研究背景",
        ),
        (
            "申报单位（用人单位）情况部分", "申报单位（用人单位）情况",
            "用人单位类型", "申报单位（用人单位）简介",
        ),
    )
    return {
        "申报人基本信息": {},
        "申报信息": {},
        "用人单位情况及承诺": {"企业基本情况": {}},
        "主要学历": _rows_to_parser_edu(edu_rows),
        "工作经历": _rows_to_parser_work(work_rows),
        "专长及代表性成果": expertise,
        "科研情况": expertise,
        "工作设想": work_plan,
    }
