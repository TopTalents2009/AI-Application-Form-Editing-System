# -*- coding: utf-8 -*-
"""修改意见清洗：剔除与申报书重复的伪意见、拆分市专题/微信综合意见为多条可执行条款。"""
from __future__ import annotations

import re

from .matcher import is_app_content, stem_of
from .hj_form import remap_qm_section

_META_ITEM = re.compile(
    r"(?<![0-9])([一二三四五六七八九十]+)是[，,]?"
)
_META_TAIL = re.compile(
    r"(还有【[^】]+】[^。；;]*[。；;]?|拟提供申报人支持条件[^。；;]*[。；;]?|申报人推荐理由[^。；;]*[。；;]?)"
)
_YEAR_BANNER = re.compile(r"【[^】]*模板修改意见】")
_YEAR_META_LINE = re.compile(
    r"^(?:"
    r"【[^】]*模板修改意见】"
    r"|申报人\s*[:：]"
    r"|申报\s*ID\s*[:：]"
    r"|库内\s*id\s*[:：]"
    r"|出生日期\s*[:：]"
    r"|模板\s*[:：]"
    r"|来源\s*[:：]"
    r"|请按下列条款修改申报书原文"
    r")",
    re.I,
)
_YEAR_REAL_ITEM = re.compile(r"^\s*\d{1,3}[\.、．\)]\s*【", re.M)
_ID_META_CLAUSE = re.compile(
    r"申报人(姓名|及项目)|有效证件姓名|封面申报人|出生日期|模板修改指引|基础信息说明"
)


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def is_echo_app_opinion(app_text: str, op_text: str, app_name: str = "", op_name: str = "") -> bool:
    """意见文件实质是申报书正文副本（非修改意见）时返回 True。"""
    app_n = stem_of(app_name or "")
    op_n = stem_of(op_name or "")
    if app_n and op_n and app_n == op_n:
        return True
    app = str(app_text or "")
    op = str(op_text or "")
    if len(op) < 2000:
        return False
    if not is_app_content(op):
        return False
    ca, cb = _compact(app), _compact(op)
    if len(ca) < 1500 or len(cb) < 1500:
        return False
    # 前缀高度重合即视为同一份申报书
    n = min(len(ca), len(cb), 4000)
    if ca[:n] == cb[:n]:
        return True
    # 较短一方几乎完全包含在较长一方
    short, long = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    if len(short) >= 2000 and short in long:
        return True
    return False


def strip_year_template_header(text: str) -> str:
    """去掉年转模板意见页眉，只留编号条款。"""
    raw = str(text or "")
    if not raw.strip():
        return ""
    if not (_YEAR_BANNER.search(raw) or re.search(r"^(?:申报人|出生日期|申报\s*ID|库内\s*id|模板|来源)\s*[:：]", raw, re.M)):
        return raw.strip()
    kept = []
    started = False
    for ln in raw.splitlines():
        s = ln.strip()
        if not started:
            if not s or _YEAR_META_LINE.match(s) or _YEAR_BANNER.search(s):
                continue
            started = True
        kept.append(ln)
    return "\n".join(kept).strip()


def is_identity_meta_text(text: str) -> bool:
    """页眉/摘要把姓名、出生日期当成条款，但不是可执行修改。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    if is_year_template_meta(raw):
        return True
    if _YEAR_REAL_ITEM.search(raw) and re.search(r"修改前|修改后", raw):
        return False
    if re.search(r"出生年月在.{0,20}以前", raw):
        return False
    return bool(_ID_META_CLAUSE.search(raw) and not re.search(r"修改前|修改后", raw))


def is_year_template_meta(text: str) -> bool:
    """8091 年转模板意见的页眉（申报人/出生日期/编号）不是可执行条款。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    if _YEAR_REAL_ITEM.search(raw) and re.search(r"修改前|修改后", raw):
        return False
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return False
    meta_n = sum(1 for ln in lines if _YEAR_META_LINE.match(ln) or _YEAR_BANNER.search(ln))
    if meta_n == 0:
        return False
    rest = [ln for ln in lines if not (_YEAR_META_LINE.match(ln) or _YEAR_BANNER.search(ln))]
    if not rest:
        return True
    rest_blob = "".join(rest)
    if len(rest_blob) < 80 and not re.search(r"修改前|修改后", rest_blob):
        return True
    return meta_n >= 2 and not re.search(r"^\s*\d{1,3}[\.、．\)]", raw, re.M)


def _section_for_meta_item(text: str) -> str:
    raw = str(text or "").strip()
    blob = _compact(raw)
    # 市专题「一是…二是…」按序号优先，避免「500字与1500字项目关联」被误判进工作计划
    if raw.startswith("一是") or re.search(r"主观|泰斗|知名大佬", blob):
        return "基本信息"
    if raw.startswith("二是") or re.search(r"(?<![0-9])500\s*字|(?<![0-9])300\s*字|技术能力|重要履历", blob):
        return "基本信息"
    if raw.startswith("三是") or "标志性成果" in blob:
        return "基本信息"
    if raw.startswith("四是") or "研发经验和工作基础" in blob:
        return "工作计划"
    if re.search(r"1500\s*字|工作目标及可行性|AR-HUD.*安全|安全.*AR-HUD", blob):
        return "工作计划"
    if "支持条件" in blob:
        return "其他"
    if "推荐理由" in blob:
        return "其他"
    return "其他"


def split_meta_directives(text: str, source_name: str = "") -> list[dict]:
    """把「一是…二是…四是1500字里…」类综合意见拆成多条带章节的子条款。"""
    raw = str(text or "").strip()
    if len(raw) < 80:
        return []
    if not re.search(r"[一二三四五六七八九十]+是", raw):
        return []
    if not re.search(r"1500|500\s*字|技术能力|支持条件|推荐理由|主观|泰斗", raw):
        return []

    parts: list[str] = []
    ms = list(_META_ITEM.finditer(raw))
    if len(ms) >= 2:
        for i, m in enumerate(ms):
            start = m.start()
            end = ms[i + 1].start() if i + 1 < len(ms) else len(raw)
            seg = raw[start:end].strip()
            for stop in ("还有【", "拟提供申报人", "申报人推荐理由"):
                j = seg.find(stop)
                if j > 0:
                    seg = seg[:j].strip()
            if len(seg) >= 12:
                parts.append(seg)
    else:
        parts.append(raw)

    for m in _META_TAIL.finditer(raw):
        seg = m.group(1).strip()
        if seg and seg not in parts and len(seg) >= 10:
            parts.append(seg)

    out: list[dict] = []
    for i, seg in enumerate(parts, 1):
        sec = _section_for_meta_item(seg)
        sec = remap_qm_section(sec, seg, seg)
        summary = seg.split("，", 1)[0].split("。", 1)[0][:60]
        out.append({
            "text": seg,
            "section": sec,
            "clause": summary,
            "metaPart": i,
            "sourceName": source_name,
        })
    return out


def sanitize_opinion_blocks(
    blocks: list[dict],
    app_text: str = "",
    app_name: str = "",
    hj: bool = False,
) -> tuple[list[dict], list[str]]:
    """过滤伪意见、展开综合意见。返回 (新 blocks, 日志说明)。"""
    notes: list[str] = []
    out: list[dict] = []
    seq = 0
    skipped_files: set[str] = set()
    skipped_year_meta = False

    for b in blocks or []:
        text = str(b.get("text") or "").strip()
        name = str(b.get("name") or "")
        if not text:
            continue
        if is_echo_app_opinion(app_text, text, app_name, name):
            if name not in skipped_files:
                notes.append("已跳过与申报书正文重复的意见文件「" + name + "」（非修改意见，避免误把填表内容当条款）")
                skipped_files.add(name)
            continue
        stripped = strip_year_template_header(text)
        if stripped != text:
            text = stripped
            if not skipped_year_meta:
                notes.append("已去掉年转模板意见页眉（申报人姓名/出生日期不改正文）")
                skipped_year_meta = True
        if not text or is_year_template_meta(text) or is_identity_meta_text(text):
            if not skipped_year_meta:
                notes.append("已跳过年转模板意见页眉（申报人姓名/出生日期等元数据，不改正文身份栏）")
                skipped_year_meta = True
            continue

        if not hj and (len(text) >= 120) and re.search(r"[一二三四五六七八九十]+是", text):
            subs = split_meta_directives(text, name)
            if len(subs) >= 2:
                notes.append("已将综合意见「" + name + "」拆为 " + str(len(subs)) + " 条子条款")
                for sub in subs:
                    seq += 1
                    out.append({
                        "id": "S" + str(seq),
                        "name": name,
                        "text": sub["text"],
                        "_metaSection": sub["section"],
                        "_metaClause": sub["clause"],
                        "_metaParent": b.get("id"),
                    })
                continue

        seq += 1
        out.append({"id": "S" + str(seq), "name": name, "text": text})

    return out, notes
