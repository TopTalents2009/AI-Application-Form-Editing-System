"""未知信息意见：定位申报书原文，生成人工复核行（修改后预填原文供改写）。"""
from __future__ import annotations

import re

from .hj_form import remap_qm_section, is_hj_app, remap_hj_section

_ANCHOR_RE = re.compile(r"标注原文[：:]\s*(.+?)(?:\n|$)", re.M)
_CID_RE = re.compile(r"(?:\[|【|针对\s*)?(S\d+)(?:\]|】)?", re.I)

_UNKNOWN_LO = re.compile(
    r"人才库未|未提供|禁止臆造|待核实|缺少真实|无法安全|无对应数据|库内无|"
    r"未查人才|需提供.*佐证|待核对|无法确定|不要臆造|未检索到|无依据.*补充|"
    r"意见未覆盖|需人工|核实后补|待补录|无法在正文完成",
    re.I,
)

_UNKNOWN_OP = re.compile(
    r"是否.*(?:一致|累计)|资助金额|经济效益|科研启动经费|"
    r"贴合实际|务实|佐证|核对.*对应|职称.*对应|聘书|立项金额|累计.*吗",
    re.I,
)
_SKIP_MANUAL_OP = re.compile(r"是不是.*首创|是否.*首创|首创.*[？?]", re.I)

_SECTION_MARKERS = (
    ("工作计划", ("六、工作计划及个人承诺", "工作目标及可行性论证", "拟解决的关键技术", "可行性论证分析")),
    ("用人单位", ("七、用人单位情况及承诺", "拟提供申报人支持条件", "企业基本情况", "申报人推荐理由")),
    ("基本信息", ("申报人基本情况", "引进企业基本情况", "一、申报人", "相当于国内职务情况")),
    ("论文", ("代表性论文", "三、代表性论文", "奖励表彰", "五、代表性")),
    ("项目", ("工作成果及业绩", "四、工作成果", "科研项目")),
    ("教育", ("教育经历", "主要学历", "二、主要学历")),
    ("工作", ("工作经历", "职务职责", "三、工作经历")),
)

_QM_SLICE_BOUNDS = (
    ("基本信息", ("一、申报人", "二、引进企业")),
    ("教育", ("二、主要学历", "三、工作经历")),
    ("工作", ("三、工作经历", "四、工作成果")),
    ("项目", ("四、工作成果及业绩", "五、代表性")),
    ("论文", ("五、代表性", "六、工作计划")),
    ("工作计划", ("六、工作计划", "七、用人单位")),
    ("用人单位", ("七、用人单位", "\x00")),
)

_FIELD_LABELS = frozenset({
    "项目成果", "简述个人贡献", "副教授", "助理教授", "WorDepth:", "项目来源", "研发投入",
})


def extract_anchor(opinion: str) -> str:
    m = _ANCHOR_RE.search(str(opinion or ""))
    return (m.group(1).strip() if m else "")


def extract_cid(text: str) -> str:
    m = _CID_RE.search(str(text or ""))
    if not m:
        return ""
    n = m.group(1).upper()
    return "S" + str(int(n[1:])) if n.startswith("S") else n


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _anchor_pat(anchor: str) -> re.Pattern | None:
    parts = [p for p in re.split(r"\s+", anchor.strip()) if p]
    if not parts:
        return None
    body = r"\s*".join(re.escape(p) for p in parts)
    return re.compile(body, re.I)


def _slice_section(app_text: str, section: str) -> str:
    sec = str(section or "").strip()
    for name, (start, end) in _QM_SLICE_BOUNDS:
        if name != sec:
            continue
        s = app_text.find(start)
        if s < 0:
            continue
        e = len(app_text) if end == "\x00" else app_text.find(end, s + 1)
        if e < 0:
            e = len(app_text)
        return app_text[s:e]
    return app_text


def _hint_at(app_text: str, pos: int) -> str:
    before = app_text[: max(0, pos)]
    best_sec, best_key, best_i = "", "", -1
    for sec, keys in _SECTION_MARKERS:
        for k in keys:
            i = before.rfind(k)
            if i > best_i:
                best_i, best_sec, best_key = i, sec, k
    if best_i >= 0 and pos - best_i < 12000:
        line_start = before.rfind("\n", max(0, pos - 300), pos) + 1
        line_end = app_text.find("\n", pos)
        if line_end < 0:
            line_end = min(len(app_text), pos + 120)
        line = app_text[line_start:line_end].strip()
        if line:
            return best_sec + " · " + best_key + " · " + line[:72]
        return best_sec + " · " + best_key
    return "申报书正文"


def _expand_snippet(app_text: str, start: int, length: int, max_len: int = 80) -> str:
    snippet = app_text[start: start + length]
    if len(snippet) <= max_len:
        return snippet
    extra = max_len - length
    left = extra // 2
    s = max(0, start - left)
    e = min(len(app_text), start + length + (extra - left))
    out = app_text[s:e]
    if s > 0:
        out = "…" + out
    if e < len(app_text):
        out = out + "…"
    return out


def _expand_short_context(app_text: str, pos: int, anchor: str, max_len: int) -> str:
    line_start = app_text.rfind("\n", 0, pos) + 1
    line_end = app_text.find("\n", pos)
    if line_end < 0:
        line_end = len(app_text)
    prev_start = app_text.rfind("\n", 0, max(0, line_start - 1)) + 1
    prev_line = app_text[prev_start:line_start].strip()
    cur_line = app_text[line_start:line_end].strip()
    if prev_line and len(prev_line) < 48 and not prev_line.endswith("。"):
        snippet = prev_line + "\n" + cur_line
    else:
        snippet = cur_line or anchor
    if len(snippet) > max_len:
        snippet = snippet[:max_len]
    return snippet


def _field_label_find(app_text: str, anchor: str, max_len: int) -> tuple[str, str]:
    if anchor not in _FIELD_LABELS and anchor not in ("项目成果", "简述个人贡献"):
        return "", ""
    if anchor.startswith("项目成果"):
        for pat in (
            r"(项目针对[^\n]{24,})",
            r"(申报人作为项目[^\n]{24,})",
            r"项目成果\s*(?:\([^\n]+\))?\s*\n[\s\S]{0,800}?(项目针对[^\n]{24,})",
        ):
            m = re.search(pat, app_text, re.M)
            if m:
                body = (m.group(1) if m.lastindex == 1 else m.group(m.lastindex)).strip()
                pos = app_text.find(body)
                return body[:max_len], _hint_at(app_text, pos if pos >= 0 else m.start())
    if anchor == "副教授":
        key = "相当于国内职务情况"
        i = app_text.find(key)
        if i >= 0:
            sub = app_text[i: i + 80]
            return sub.strip()[:max_len], _hint_at(app_text, i)
    return "", ""


def locate_anchor(app_text: str, anchor: str, max_len: int = 80, section: str = "") -> tuple[str, str]:
    anchor = str(anchor or "").strip()
    if not anchor or not app_text:
        return "", ""
    hay = _slice_section(app_text, section) if section else app_text
    full = app_text

    if anchor.startswith("项目成果"):
        proj = _slice_section(app_text, "项目")
        hit, hint = _field_label_find(proj, anchor, max_len)
        if hit:
            return hit, hint

    def _search(text: str, full_pos_offset: int = 0) -> tuple[str, str]:
        if anchor in text:
            pos = text.index(anchor)
            abs_pos = full_pos_offset + pos
            if len(_norm(anchor)) < 4 or anchor in _FIELD_LABELS:
                return _expand_short_context(full, abs_pos, anchor, max_len), _hint_at(full, abs_pos)
            return _expand_snippet(full, abs_pos, len(anchor), max_len), _hint_at(full, abs_pos)
        pat = _anchor_pat(anchor)
        if pat:
            m = pat.search(text)
            if m:
                abs_pos = full_pos_offset + m.start()
                if len(_norm(anchor)) < 4 or anchor in _FIELD_LABELS:
                    return _expand_short_context(full, abs_pos, anchor, max_len), _hint_at(full, abs_pos)
                return _expand_snippet(full, abs_pos, m.end() - m.start(), max_len), _hint_at(full, abs_pos)
        return "", ""

    hit, hint = _search(hay, app_text.find(hay) if hay != app_text and hay in app_text else 0)
    if hit:
        return hit, hint
    hit, hint = _field_label_find(hay, anchor, max_len)
    if hit:
        return hit, hint
    hit, hint = _search(app_text, 0)
    if hit:
        return hit, hint
    return _field_label_find(app_text, anchor, max_len)


def _clause_section(clause: dict, app_text: str, hj: bool) -> str:
    sec = str(clause.get("section") or "其他")
    cl = str(clause.get("clause") or "")
    op = str(clause.get("opinion") or "")
    if hj or is_hj_app(app_text=app_text):
        return remap_hj_section(sec, cl, op)
    return remap_qm_section(sec, cl, op)


def _has_edit_for(clause: dict, edits: list) -> bool:
    cid = extract_cid(clause.get("cid") or clause.get("sourceId") or "")
    ck = _norm(clause.get("clause"))
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        if e.get("manualFill"):
            continue
        eid = extract_cid(e.get("clauseId") or "")
        if cid and eid and cid == eid:
            return True
        if ck and ck == _norm(e.get("clause")):
            return True
    return False


def _collect_targets(clauses: list, edits: list, leftovers: list) -> dict[str, str]:
    """clause_id -> reason"""
    out: dict[str, str] = {}
    by_id = {}
    for c in clauses or []:
        cid = extract_cid(c.get("cid") or c.get("sourceId") or "")
        if cid:
            by_id[cid] = c

    for lv in leftovers or []:
        text = str(lv)
        if not _UNKNOWN_LO.search(text):
            continue
        cid = extract_cid(text)
        if cid:
            out[cid] = text
            continue
        for cid2, c in by_id.items():
            if cid2 in out:
                continue
            blob = str(c.get("opinion") or "") + str(c.get("clause") or "")
            if _UNKNOWN_OP.search(blob):
                out[cid2] = text

    for c in clauses or []:
        cid = extract_cid(c.get("cid") or c.get("sourceId") or "")
        if not cid or cid in out:
            continue
        if _has_edit_for(c, edits):
            continue
        blob = str(c.get("opinion") or "")
        if _SKIP_MANUAL_OP.search(blob):
            continue
        if _UNKNOWN_OP.search(blob) or _UNKNOWN_LO.search(blob):
            out[cid] = "意见涉及需核实/库内未知数据：" + " ".join(blob.split())[:120]

    return out


def _make_manual_edit(clause: dict, reason: str, app_text: str, app_no: str, hj: bool) -> dict | None:
    opinion = str(clause.get("opinion") or "")
    sec = _clause_section(clause, app_text, hj)
    anchor = extract_anchor(opinion)
    find, hint = locate_anchor(app_text, anchor, section=sec)
    if not find and anchor:
        find, hint = locate_anchor(app_text, anchor[: max(6, len(anchor) // 2)], section=sec)
    if not find:
        cl = str(clause.get("clause") or "")
        find, hint = locate_anchor(app_text, cl[:40], section=sec)
    if not find:
        return None
    cid = extract_cid(clause.get("cid") or clause.get("sourceId") or "")
    note = "【需人工补充】系统无法自动填入真实数据，请在「修改后」栏改写。原因：" + str(reason)[:180]
    return {
        "find": find,
        "replace": find,
        "clause": str(clause.get("clause") or "")[:80],
        "opinion": opinion,
        "opinionGemini": note,
        "clauseId": cid or str(clause.get("cid") or ""),
        "opName": str(clause.get("opName") or ""),
        "section": sec,
        "_sec": sec,
        "appNo": app_no,
        "manualFill": True,
        "locationHint": hint,
        "unknownReason": str(reason)[:240],
    }


def promote_unknown_to_manual_edits(
    clauses: list,
    edits: list,
    leftovers: list,
    app_text: str,
    app_no: str = "",
    hj: bool = False,
) -> tuple[list, list, int]:
    """将含未知信息的意见转为计划表人工行；返回 (edits, leftovers, 新增条数)。"""
    edits = list(edits or [])
    leftovers = list(leftovers or [])
    targets = _collect_targets(clauses, edits, leftovers)
    if not targets:
        return edits, leftovers, 0

    by_id = {}
    for c in clauses or []:
        cid = extract_cid(c.get("cid") or c.get("sourceId") or "")
        if cid:
            by_id[cid] = c

    promoted: set[str] = set()
    added = 0
    for cid, reason in targets.items():
        clause = by_id.get(cid)
        if not clause:
            continue
        if any(extract_cid(e.get("clauseId") or "") == cid and e.get("manualFill") for e in edits):
            continue
        item = _make_manual_edit(clause, reason, app_text, app_no, hj)
        if not item:
            continue
        edits.append(item)
        promoted.add(cid)
        added += 1

    if not promoted:
        return edits, leftovers, 0

    new_lo: list[str] = []
    for lv in leftovers:
        text = str(lv)
        cid = extract_cid(text)
        if cid and cid in promoted:
            new_lo.append("【已转人工复核·" + cid + "】已在计划表定位原文（黄底行），请在「修改后」栏补充")
            continue
        new_lo.append(text)
    return edits, new_lo, added
