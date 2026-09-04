# -*- coding: utf-8 -*-
"""火炬（HJ）申报书：按印刷栏位切章、抽正文、改写提示。与启明（QM）六章互不混用。"""
from __future__ import annotations
import re

HJ_SECTION_ORDER = [
    "关键申报信息",
    "个人基本信息",
    "教育",
    "工作",
    "学术荣誉",
    "专长成果",
    "工作设想",
    "用人单位",
    "其他",
]

HJ_SECTION_FILES = {
    "关键申报信息": "hj-key.md",
    "个人基本信息": "hj-profile.md",
    "教育": "hj-education.md",
    "工作": "hj-work.md",
    "学术荣誉": "hj-honors.md",
    "专长成果": "hj-expertise.md",
    "工作设想": "hj-plan.md",
    "用人单位": "hj-employer.md",
}

# 印刷大节标题（提取文本里可能带制表符）
_HJ_MARKERS = (
    ("关键申报信息", ("（1）关键申报信息", "(1)关键申报信息")),
    ("个人基本信息", ("（2）个人基本信息", "(2)个人基本信息")),
    ("教育", ("（3）教育经历", "(3)教育经历")),
    ("工作", ("（4）工作经历", "(4)工作经历")),
    ("学术荣誉", ("（5）重要学术荣誉", "(5)重要学术荣誉")),
    ("专长成果", ("（6）专长及代表性成果", "(6)专长及代表性成果")),
    ("工作设想", ("（7）工作设想", "(7)工作设想")),
    ("用人单位", ("（8）申报单位", "申报单位（用人单位）")),
)

QM_SECTION_ENUM = """- 基本信息（申报人基本情况、个人简介、姓名脱敏等）
- 教育（学历、学位、教育经历、时间门槛）
- 工作（工作经历、任职、履历年限）
- 论文（代表性论文、论著、期刊、影响因子、引用、预印本）
- 项目（科研项目、研究方向描述、项目成果）
- 其他（工作计划/三年目标、成果转化、推荐理由、企业情况、专利、附件材料、格式规范等）"""

HJ_SECTION_ENUM = """- 关键申报信息（国籍、姓名、实验室、专业领域、所属二级学科及代码、所属前沿领域等封面栏）
- 个人基本信息（性别、出生、证件、最高学位信息、回国前/来华前信息、申报情况、破格）
- 教育（教育经历表格）
- 工作（工作经历表格）
- 学术荣誉（重要学术荣誉与国际学术组织兼职）
- 专长成果（专业领域综述/亮点履历、研究领域关键词、代表性科研项目、论著、知识产权；含去重与履历/成果分工）
- 工作设想（回国/来华后工作目标、研究背景、研发内容、技术路线、预期目标、量化指标、担任职务）
- 用人单位（用人单位简介、推荐理由、支持条件、拟任职单位与职务）
- 其他（附件、格式、无法归入上列者）"""

# 意见关键词 → 强制归入 HJ 章（纠正把工作设想/关键词丢进「项目/其他」）
_HJ_FORCE = (
    ("工作设想", ("工作设想", "研发目标", "技术路线", "量化指标", "量化目标", "预期目标", "研究背景及意义")),
    ("专长成果", ("研究领域关键词", "关键词遗漏", "专长和代表性成果", "专长及代表性成果", "专长部分", "代表性成果", "成果集中在履历")),
    ("关键申报信息", ("学科分类", "二级学科", "一级学科", "专业领域选", "前沿领域")),
    ("用人单位", ("用人单位简介", "推荐理由", "支持条件", "拟任职")),
    ("教育", ("教育经历", "学历表格")),
    ("工作", ("工作经历表格", "任职起止")),
    ("学术荣誉", ("学术荣誉", "国际学术组织兼职")),
)

HJ_LAYOUT_HINTS = """【HJ·Excel 栏位对齐（必须遵守）】
1. 本申报书是 Excel。find 必须是某一个单元格内部的连续原文（该格内换行可保留），禁止把制表符、多列拼进 find，禁止把「中文标题格 + 英文标题格 + 空白值格」拼成一条 find。
2. 空栏填写（研究领域关键词等）：find 只用中文标题的整格原文，例如「研究领域关键词（不超过5个）」；replace 只写要填入空白合并格的正文（中文顿号分隔，不超过 5 个；如需英文，换行后再写英文，仍不超过 5 个）。不要把标题或「Key words」复制进 replace。
3. 下拉框（所属二级学科及代码）：find / replace 必须是表内已有选项的全文。计算机方向一级用「520计算机科学技术」；二级与「计算机架构」对应的表内项是「52030计算机系统机构」（表内写作「机构」不是「结构」），与「计算机应用」对应的是「52060计算机应用」。禁止自拟「计算机架构」「计算机科学」等不在下拉中的写法。一级、二级各写一条编辑，两条必须配套。
4. 「专长及代表性成果」「工作设想」是超大合并格：一次编辑只替换该格里的一段连续原文；不要把相邻小节标题写进 replace；换行结构尽量与 find 一致。
5. 不得套用启明申报书的「申报人基本情况 500 字 / 重要经历·主要技术能力·标志性成果」或「项目成果三句话」体例。
"""


def compact(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def is_hj_app(mode: str = "", app_text: str = "", fname: str = "") -> bool:
    if str(mode or "").strip().upper() == "HJ":
        return True
    name = str(fname or "").upper()
    if re.search(r"(^|[_\-\s\(\[（])HJ([_\-\s\)\]）]|$)", name):
        return True
    blob = compact(app_text)
    return "国家火炬计划申报书" in blob or "（1）关键申报信息" in blob or "(1)关键申报信息" in blob


def remap_hj_section(section: str, clause: str = "", opinion: str = "") -> str:
    """用意见原文把误分到「项目/其他/基本信息」的条款纠正到 HJ 印刷章。"""
    blob = compact(str(clause or "") + str(opinion or ""))
    for sec, keys in _HJ_FORCE:
        if any(compact(k) in blob for k in keys):
            return sec
    sec = str(section or "").strip()
    return sec if sec in HJ_SECTION_ORDER else "其他"


def _find_marker(text: str, keys: tuple[str, ...]) -> int:
    raw = str(text or "")
    for k in keys:
        i = raw.find(k)
        if i >= 0:
            return i
        ck, cr = compact(k), compact(raw)
        j = cr.find(ck)
        if j < 0:
            continue
        seen = 0
        for i2, ch in enumerate(raw):
            if ch.isspace():
                continue
            if seen == j:
                return i2
            seen += 1
    return -1


def split_hj_sections(app_text: str) -> dict[str, str]:
    raw = str(app_text or "")
    hits = []
    for sec, keys in _HJ_MARKERS:
        i = _find_marker(raw, keys)
        if i >= 0:
            hits.append((i, sec))
    hits.sort()
    out = {}
    for n, (i, sec) in enumerate(hits):
        end = hits[n + 1][0] if n + 1 < len(hits) else len(raw)
        chunk = raw[i:end].strip()
        if chunk:
            out[sec] = chunk
    return out


def slice_hj_section(app_text: str, sec: str) -> str:
    """只把本章印刷正文交给模型，避免把整本 Excel 噪音塞进上下文。"""
    parts = split_hj_sections(app_text)
    sec = str(sec or "").strip()
    if sec in parts:
        body = parts[sec]
    else:
        body = str(app_text or "")
    toc = "、".join(s for s, _ in _HJ_MARKERS)
    extra = []
    if sec == "专长成果" and "研究领域关键词" in body and "【空栏待填】" not in body:
        nxt = body.find("2.代表性科研项目")
        window = body if nxt < 0 else body[:nxt]
        if "研究领域关键词" in window and compact(window).count("关键词") <= 2:
            extra.append(
                "【空栏待填】「研究领域关键词（不超过5个）」标题下方合并格目前为空白；"
                "find 用该中文标题整格原文，replace 只写不超过 5 个关键词正文。"
            )
    if sec == "关键申报信息":
        extra.append(
            "【下拉选项】一级学科计算机方向必须写成「520计算机科学技术」；"
            "二级「计算机架构」对应「52030计算机系统机构」，「计算机应用」对应「52060计算机应用」。"
        )
    head = "【HJ 印刷章节目录】" + toc + "\n【本章节正文 · " + sec + "】\n"
    return head + body + (("\n" + "\n".join(extra)) if extra else "")


def hj_form_requirements(app_text: str) -> str:
    blob = str(app_text or "")
    lines = [
        "本表为火炬计划（HJ）Excel 申报书，栏位以印刷标题为准，禁止套用启明（QM）500 字基本情况或项目三句话体例。",
        "研究领域关键词：不超过 5 个；中英文分两行写在标题下方空白合并格，不要改标题格。",
        "代表性科研项目：不超过 2 个研究方向，每个方向不超过 5 项。",
        "代表性论著：不超过 2 个研究方向，每个方向不超过 10 项。",
        "知识产权：不超过 10 项。",
    ]
    if "用人单位简介（300字以内）" in compact(blob) or "用人单位简介" in blob:
        lines.append("「用人单位简介」上限 300 字（去空白计），合格带 240–270 字。")
    if "字数不多于30字" in blob:
        lines.append("破格申报详情等注明「不多于 30 字」的栏位不得超过 30 字。")
    return "\n".join("- " + x for x in lines)
