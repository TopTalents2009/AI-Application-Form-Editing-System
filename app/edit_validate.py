# -*- coding: utf-8 -*-
"""结构化编辑硬约束：个人贡献、正序多行、1500字、教育/论文行覆盖。"""
from __future__ import annotations

import re

from .form_reqs import char_count, resolve_limit
from .hj_form import is_hj_app, remap_hj_section, remap_qm_section

_ITEM_MARK = re.compile(r"(论文\s*\d+|项目\s*\d+|专利\s*\d+|论著\s*\d+)", re.I)
_CHRON_N = re.compile(
    r"(?:补充|填写|列出|增加|新增|梳理)?\s*(\d+)\s*条.*?(?:正序|顺序)"
    r"|(?:正序|顺序).*?(\d+)\s*条"
    r"|补充\s*(\d+)\s*篇",
    re.I,
)
_CONTRIB_KW = re.compile(r"个人贡献|角色与贡献|简述个人贡献|贡献情况|补充.*贡献", re.I)
_WRONG_TARGET = re.compile(r"项目成果|成果描述|项目简介|项目名称", re.I)
_PAPER_REF = re.compile(r"论文\s*(\d+)|第\s*(\d+)\s*篇|论著\s*(\d+)", re.I)
_EDU_KW = re.compile(r"学历|本科|硕士|博士|学士|院校|毕业|学位|教育经历|博士后", re.I)
_JOURNAL_KW = re.compile(r"期刊|影响因子|卷\s*[（(]|页码|发表载体", re.I)
_KEY_PROBLEM_OP = re.compile(
    r"重新拆分|三个技术问题|1、3重复|1、3\s*重复|拟解决的关键技术|关键技术问题.*重复",
    re.I,
)
_FEASIBILITY_OP = re.compile(r"政策|市场|技术发展趋势|可行性论证分析|可行性分析", re.I)
_SUPPORT_OP = re.compile(
    r"拟提供申报人支持条件|申报人支持条件|支持条件|500\s*平米|科研启动经费|空话|贴合实际",
    re.I,
)
_SALARY_OP = re.compile(r"年薪|薪酬标准")
_TITLE_OP = re.compile(r"终身副教授|改为.{0,6}副教授")
_COL_ALL_OP = re.compile(r"这一栏|本栏|该栏|须对该表全部|全部条目|逐项")
_FAKE_COOP = re.compile(
    r"双方前期[^。；]{0,24}(?:深入对接|合作基础|合作互信)[^。；]{0,20}[。；]?"
    r"|具备良好的合作(?:互信)?基础[^。；]{0,10}[。；]?"
    r"|申报企业与申报人所在[^。；]{0,20}(?:深入对接|合作基础)[^。；]{0,16}[。；]?",
)
_PAST_AT_FIRM = re.compile(r"依托申报企业[^。]{0,40}(?:推进转化|转化)")
_PAST_DONE = re.compile(r"已完成中试|已实现.{0,8}量产|工业级批量|已产业化")
_COMPANY_FIELD = re.compile(r"企业名称|申报企业\s*[:：]|用人单位名称")
_QUESTION_ONLY = re.compile(r"是不是|相当于.*[？?]|是否.*[？?]|吗[？?]\s*$", re.I)
_DIRECTIVE = re.compile(
    r"修改|改为|替换|补充|增加|删除|重写|拆分|统一|规范|细化|贴合|调整|删除|写成",
    re.I,
)
def _norm_sid(s) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", str(s or "")).upper()


def _norm_find(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))[:120]


def _blob(edit: dict) -> str:
    return str(edit.get("find") or "") + str(edit.get("replace") or "")


def requires_contribution(text: str) -> bool:
    return bool(_CONTRIB_KW.search(str(text or "")))


def edit_addresses_contribution(edit: dict) -> bool:
    blob = _blob(edit)
    if _CONTRIB_KW.search(blob):
        return True
    if re.search(r"角色|贡献|第一作者|通讯作者|共同第一|独立完成", blob, re.I):
        return True
    return False


def is_wrong_contribution_target(edit: dict, opinion: str = "") -> bool:
    op = str(opinion or edit.get("opinion") or edit.get("clause") or "")
    if not requires_contribution(op):
        return False
    if edit_addresses_contribution(edit):
        return False
    find = str(edit.get("find") or "")
    replace = str(edit.get("replace") or "")
    if _WRONG_TARGET.search(find) and not re.search(r"贡献|角色", find, re.I):
        return True
    if _JOURNAL_KW.search(replace) and not _CONTRIB_KW.search(replace):
        return True
    if char_count(replace) > 40 and _WRONG_TARGET.search(replace) and not re.search(r"贡献|角色", replace, re.I):
        return True
    return False


def expected_item_count(text: str) -> int | None:
    raw = str(text or "")
    m = _CHRON_N.search(raw)
    if m:
        for g in m.groups():
            if g:
                return int(g)
    if "论文" in raw or "论著" in raw:
        m2 = re.search(r"(\d+)\s*篇", raw)
        if m2:
            return int(m2.group(1))
    return None


def _edits_for_clause(clause: dict, edits: list) -> list:
    sid = _norm_sid(clause.get("sourceId") or clause.get("cid"))
    cid = _norm_sid(clause.get("cid"))
    ck = _norm_find(clause.get("clause"))
    out = []
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        eid = _norm_sid(e.get("clauseId"))
        if eid and eid in (sid, cid):
            out.append(e)
            continue
        if ck and ck == _norm_find(e.get("clause")):
            out.append(e)
    return out


def _row_overlap(blob: str, row: dict) -> bool:
    compact = re.sub(r"\s+", "", str(blob or ""))
    if not compact:
        return False
    for v in (row or {}).values():
        s = re.sub(r"\s+", "", str(v or ""))
        if len(s) >= 4 and s in compact:
            return True
    return False


def _parse_edu_papers(app_text: str):
    from .template_fill import _parse_list_lines, _parse_papers

    edu, _work = _parse_list_lines(app_text)
    papers = _parse_papers(app_text)
    return edu, papers


def _find_line_anchor(app_text: str, needle: str, fallback: str = "") -> str:
    needle = str(needle or "").strip()
    if not needle:
        return fallback
    for line in str(app_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if needle in s:
            return s[:160]
    compact_n = re.sub(r"\s+", "", needle)[:24]
    if len(compact_n) < 6:
        return fallback
    for line in str(app_text or "").splitlines():
        s = line.strip()
        if compact_n in re.sub(r"\s+", "", s):
            return s[:160]
    return fallback


def try_split_merged_edit(edit: dict, app_text: str) -> list[dict]:
    rep = str(edit.get("replace") or "")
    find = str(edit.get("find") or "")
    if len(_ITEM_MARK.findall(rep)) < 2:
        return [edit]
    parts = [p.strip() for p in re.split(r"(?=(?:论文|项目|专利|论著)\s*\d+)", rep, flags=re.I) if p.strip()]
    if len(parts) < 2:
        return [edit]

    _, papers = _parse_edu_papers(app_text)
    new_edits = []
    for part in parts:
        anchor = ""
        for row in papers:
            title = str(row.get("论文题目") or "")
            if len(title) >= 6 and title[:16] in part:
                anchor = _find_line_anchor(app_text, title[:20], find)
                break
        if not anchor:
            m = re.search(r"[,，]\s*([^,，\n]{6,80})", part)
            if m:
                anchor = _find_line_anchor(app_text, m.group(1)[:20], find)
        if not anchor and find:
            anchor = find
        if not anchor:
            continue
        ne = dict(edit)
        ne["find"] = anchor
        ne["replace"] = part
        new_edits.append(ne)
    return new_edits if len(new_edits) >= 2 else [edit]


def enforce_no_merge(edits: list, app_text: str) -> tuple[list, list[str]]:
    out: list = []
    issues: list[str] = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        rep = str(e.get("replace") or "")
        find = str(e.get("find") or "")
        r_m = _ITEM_MARK.findall(rep)
        f_m = _ITEM_MARK.findall(find)
        if len(r_m) >= 2 and len(f_m) < 2:
            splits = try_split_merged_edit(e, app_text)
            if len(splits) >= 2:
                out.extend(splits)
                issues.append("【正序拆分】第 %d 条已自动拆分为 %d 条独立编辑（避免多条合并抹行）" % (i, len(splits)))
                continue
            issues.append(
                "【多条合并阻断】第 %d 条 replace 含 %d 个条目标记但无法拆分，已剔除该编辑"
                % (i, len(r_m))
            )
            continue
        out.append(e)
    return out, issues


def filter_contribution_edits(edits: list, clauses: list) -> tuple[list, list[str]]:
    by_id = {}
    for c in clauses or []:
        cid = _norm_sid(c.get("cid") or c.get("sourceId"))
        if cid:
            by_id[cid] = c
    kept: list = []
    issues: list[str] = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        cid = _norm_sid(e.get("clauseId"))
        clause = by_id.get(cid) or {}
        opinion = str(e.get("opinion") or clause.get("opinion") or clause.get("clause") or "")
        if is_wrong_contribution_target(e, opinion):
            issues.append(
                "【个人贡献串栏】第 %d 条意见要求改贡献，但锚点/改写落在项目成果或期刊栏，已剔除"
                % i
            )
            continue
        kept.append(e)
    return kept, issues


def enforce_chronological_counts(clauses: list, edits: list, app_text: str) -> list[str]:
    issues: list[str] = []
    for c in clauses or []:
        op = str(c.get("opinion") or "")
        clause = str(c.get("clause") or "")
        blob = clause + op
        need = expected_item_count(blob)
        if not need or need < 2:
            continue
        matched = _edits_for_clause(c, edits)
        if not matched:
            continue
        item_edits = 0
        for e in matched:
            rep = str(e.get("replace") or "")
            find = str(e.get("find") or "")
            marks = set(_ITEM_MARK.findall(rep)) | set(_ITEM_MARK.findall(find))
            if len(marks) >= 1:
                item_edits += max(1, len(set(_ITEM_MARK.findall(rep))))
            elif str(e.get("find") or "").strip():
                item_edits += 1
        if item_edits < need:
            cid = str(c.get("cid") or c.get("sourceId") or "")
            issues.append(
                "【正序不足】%s 要求 %d 条独立编辑，当前仅 %d 条，须拆成多条各改一行"
                % (cid or clause[:40], need, item_edits)
            )
    return issues


def _clause_has_edu_edit(clause: dict, edits: list, app_text: str) -> bool:
    edu_rows, _ = _parse_edu_papers(app_text)
    if not edu_rows:
        return bool(_edits_for_clause(clause, edits))
    for e in _edits_for_clause(clause, edits):
        blob = _blob(e)
        if any(_row_overlap(blob, row) for row in edu_rows):
            return True
        if _EDU_KW.search(blob):
            return True
    return False


def _paper_index_from_opinion(op: str) -> list[int]:
    out = []
    for m in _PAPER_REF.finditer(op):
        for g in m.groups():
            if g:
                out.append(int(g))
    return out


def _clause_has_paper_edit(clause: dict, edits: list, app_text: str) -> bool:
    _, papers = _parse_edu_papers(app_text)
    matched = _edits_for_clause(clause, edits)
    if not matched:
        return False
    op = str(clause.get("opinion") or "")
    want_idx = _paper_index_from_opinion(op)
    for e in matched:
        blob = _blob(e)
        if _ITEM_MARK.search(blob):
            return True
        for row in papers:
            if _row_overlap(blob, row):
                return True
        if requires_contribution(op) and edit_addresses_contribution(e):
            return True
    if want_idx and papers:
        for idx in want_idx:
            if 1 <= idx <= len(papers):
                title = str(papers[idx - 1].get("论文题目") or "")
                for e in matched:
                    if title and title[:12] in _blob(e):
                        return True
    return bool(matched) and not want_idx and not requires_contribution(op)


def _clause_by_id(clauses: list) -> dict:
    out = {}
    for c in clauses or []:
        cid = _norm_sid(c.get("cid") or c.get("sourceId") or "")
        if cid:
            out[cid] = c
    return out


def is_question_only_opinion(opinion: str) -> bool:
    op = str(opinion or "").strip()
    if not op or _DIRECTIVE.search(op):
        return False
    # 「单位是？」是补经费单位（万元），不是核对类疑问
    if re.search(r"单位是", op) and not re.search(r"是不是|是否等于", op):
        return False
    if re.search(
        r"顺序|不一致|语序|错误|翻译|漏|重复|空话|虚高|精简|补充|增加|修改|统一|规范|细化|贴合|卷期|授权年份",
        op,
    ):
        return False
    if _QUESTION_ONLY.search(op):
        return True
    for line in op.splitlines():
        s = line.strip()
        if not s or s.startswith("标注原文"):
            continue
        if (s.endswith("？") or s.endswith("?")) and not _DIRECTIVE.search(s):
            return True
    return False


def filter_question_only_edits(edits: list, clauses: list) -> tuple[list, list[str]]:
    """疑问式意见（核对/是不是）不应自动改写正文。"""
    by_id = _clause_by_id(clauses)
    kept, issues = [], []
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        cid = _norm_sid(e.get("clauseId") or "")
        c = by_id.get(cid, {})
        op = str(c.get("opinion") or e.get("opinion") or "")
        if is_question_only_opinion(op):
            issues.append(
                "【疑问式意见·已剔除】" + (cid or "?") + "："
                + str(c.get("clause") or e.get("clause") or "")[:40]
                + "（核对类问题不自动改正文，请人工确认或写 leftovers）"
            )
            continue
        kept.append(e)
    return kept, issues


def _key_problem_span(app_text: str) -> str:
    wp = _work_plan_span(app_text)
    if not wp:
        return ""
    start = wp.find("（三）拟解决的关键技术问题")
    if start < 0:
        start = wp.find("拟解决的关键技术")
    if start < 0:
        return ""
    end = wp.find("（四）", start + 1)
    return wp[start:end if end > start else len(wp)]


def _feasibility_span(app_text: str) -> str:
    wp = _work_plan_span(app_text)
    if not wp:
        return ""
    start = wp.find("（五）可行性论证分析")
    if start < 0:
        start = wp.find("可行性论证分析")
    if start < 0:
        return ""
    end = wp.find("在匿名评审", start + 1)
    if end < 0:
        end = len(wp)
    return wp[start:end]


def _employer_support_span(app_text: str) -> str:
    raw = str(app_text or "")
    start = -1
    for pat in (
        "拟提供申报人支持条件\n(300字以内)",
        "拟提供申报人支持条件(300字以内)",
        "拟提供申报人支持条件（300字以内）",
        "支持条件（包括工作和生活",
    ):
        start = raw.find(pat)
        if start >= 0:
            break
    if start < 0:
        idx = 0
        while True:
            i = raw.find("拟提供申报人支持条件", idx)
            if i < 0:
                break
            prev = raw[i - 1] if i > 0 else ""
            if prev != "【":
                start = i
            idx = i + 1
    if start < 0:
        start = raw.find("七、用人单位情况及承诺")
    if start < 0:
        return ""
    end = -1
    for mark in (
        "7-2企业荣誉", "7-4申报人推荐理由", "申报人推荐理由",
        "(二)企业荣誉", "（二）企业荣誉", "企业荣誉和资质",
    ):
        j = raw.find(mark, start + 8)
        if j > start:
            end = j
            break
    if end < 0:
        end = min(len(raw), start + 1800)
    return raw[start:end]


def _edit_in_span(edit: dict, span: str) -> bool:
    f = str(edit.get("find") or "")
    if not f or not span:
        return False
    if f in span:
        return True
    k = re.sub(r"\s+", "", f)[:36]
    return bool(k and k in re.sub(r"\s+", "", span))


def _key_problem_nums_hit(edits: list, span: str, cid: str) -> set:
    nums = set()
    for e in edits or []:
        if cid and _norm_sid(e.get("clauseId") or "") != _norm_sid(cid):
            continue
        f = str(e.get("find") or "")
        if span and not _edit_in_span(e, span) and not re.match(r"^\s*[123][\.、．]", f):
            continue
        for n in ("1.", "2.", "3.", "1、", "2、", "3、"):
            if f.strip().startswith(n) or ("\n" + n) in f:
                nums.add(n[0])
    return nums


def detect_key_problem_rewrite_gaps(clauses: list, edits: list, app_text: str, hj: bool = False) -> list[str]:
    if hj or is_hj_app(app_text=app_text):
        return []
    span = _key_problem_span(app_text)
    if not span:
        return []
    gaps = []
    for c in clauses or []:
        op = str(c.get("opinion") or "")
        if not _KEY_PROBLEM_OP.search(op):
            continue
        cid = str(c.get("cid") or c.get("sourceId") or "")
        nums = _key_problem_nums_hit(edits, span, cid)
        clause_edits = [e for e in (edits or []) if _norm_sid(e.get("clauseId") or "") == _norm_sid(cid)]
        if len(nums) >= 3:
            continue
        if len(clause_edits) >= 3:
            continue
        gaps.append(
            "【关键问题拆分】" + cid + "：意见要求重写三个独立技术问题，"
            "须产出 3 条 edit 分别锚定 1./2./3. 条，且第 3 条不得与第 1 条语义重复"
        )
    return gaps


def remediate_feasibility_edits(
    edits: list, clauses: list, app_text: str, hj: bool = False,
) -> tuple[list, list[str]]:
    if hj or is_hj_app(app_text=app_text):
        return list(edits or []), []
    span5 = _feasibility_span(app_text)
    by_id = _clause_by_id(clauses)
    out, issues = [], []
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        cid = _norm_sid(e.get("clauseId") or "")
        c = by_id.get(cid, {})
        op = str(c.get("opinion") or e.get("opinion") or "")
        if not _FEASIBILITY_OP.search(op):
            out.append(e)
            continue
        f = str(e.get("find") or "")
        if span5 and _edit_in_span(e, span5):
            out.append(e)
            continue
        if "【背景】" in f or re.search(r"（二）制定目标依据", f):
            issues.append(
                "【可行性落点错误·已剔除】" + (cid or "?")
                + "：政策/市场/趋势类意见须改（五）可行性论证分析，不得只改（二）【背景】"
            )
            continue
        out.append(e)
    return out, issues


def detect_feasibility_gaps(clauses: list, edits: list, app_text: str, hj: bool = False) -> list[str]:
    if hj or is_hj_app(app_text=app_text):
        return []
    span5 = _feasibility_span(app_text)
    if not span5:
        return []
    gaps = []
    for c in clauses or []:
        op = str(c.get("opinion") or "")
        if not _FEASIBILITY_OP.search(op):
            continue
        cid = str(c.get("cid") or c.get("sourceId") or "")
        ok = any(
            _norm_sid(e.get("clauseId") or "") == _norm_sid(cid) and _edit_in_span(e, span5)
            for e in (edits or [])
        )
        if not ok:
            gaps.append(
                "【可行性论证漏改】" + cid + "：须在（五）可行性论证分析栏补充政策/市场/技术趋势依据"
            )
    return gaps


def detect_support_conditions_gaps(clauses: list, edits: list, app_text: str, hj: bool = False) -> list[str]:
    if hj or is_hj_app(app_text=app_text):
        return []
    span = _employer_support_span(app_text)
    if not span:
        return []
    gaps = []
    for c in clauses or []:
        op = str(c.get("opinion") or "")
        if not _SUPPORT_OP.search(op):
            continue
        cid = str(c.get("cid") or c.get("sourceId") or "")
        ok = any(
            _norm_sid(e.get("clauseId") or "") == _norm_sid(cid) and _edit_in_span(e, span)
            for e in (edits or [])
        )
        if not ok:
            gaps.append(
                "【支持条件漏改】" + cid + "：须锚定「拟提供申报人支持条件」栏（工作环境/设备/团队等）"
            )
    return gaps


def _work_plan_span(app_text: str) -> str:
    raw = str(app_text or "")
    start = raw.find("申报人拟实现工作目标及可行性论证")
    if start < 0:
        start = raw.find("六、工作计划及个人承诺")
    if start < 0:
        return ""
    end = raw.find("七、用人单位情况及承诺", start + 1)
    return raw[start:end if end > start else len(raw)]


def _edit_in_work_plan(edit: dict, wp_span: str) -> bool:
    f = str(edit.get("find") or "")
    if not f or not wp_span:
        return False
    if f in wp_span:
        return True
    k = re.sub(r"\s+", "", f)[:32]
    return bool(k and k in re.sub(r"\s+", "", wp_span))


def detect_work_plan_gaps(clauses: list, edits: list, app_text: str, hj: bool = False) -> list[str]:
    if hj or is_hj_app(app_text=app_text):
        return []
    wp_span = _work_plan_span(app_text)
    if not wp_span:
        return []
    meta = []
    for c in clauses or []:
        op = str(c.get("opinion") or "") + str(c.get("clause") or "")
        sec = remap_qm_section(str(c.get("section") or ""), str(c.get("clause") or ""), op)
        if sec == "工作计划" or re.search(r"1500\s*字|研发经验和工作基础|四是.*1500", op):
            meta.append(c)
    if not meta:
        return []
    if any(_edit_in_work_plan(e, wp_span) for e in edits or []):
        return []
    cids = "、".join(str(c.get("cid") or c.get("sourceId") or "") for c in meta[:5])
    return [
        "【工作计划漏改】" + cids + "：修改意见要求调整1500字「工作目标及可行性论证」栏，但未生成锚定该栏的编辑"
    ]


def detect_row_coverage_gaps(clauses: list, edits: list, app_text: str, hj: bool = False) -> list[str]:
    gaps: list[str] = []
    for c in clauses or []:
        sec = str(c.get("section") or "")
        op = str(c.get("opinion") or "")
        clause = str(c.get("clause") or "")
        eff_sec = remap_hj_section(sec, clause, op) if hj or is_hj_app(app_text=app_text) else sec
        cid = str(c.get("cid") or c.get("sourceId") or "")

        if eff_sec == "教育" and _EDU_KW.search(op):
            if not _clause_has_edu_edit(c, edits, app_text):
                gaps.append(
                    "【教育漏改】%s %s：意见涉及学历/院校，但未找到锚定对应教育经历行的编辑"
                    % (cid, clause[:36])
                )
            continue

        if eff_sec in ("论文", "专长成果", "项目"):
            need_paper = bool(
                _PAPER_REF.search(op)
                or _CONTRIB_KW.search(op)
                or re.search(r"代表性论文|论文排序|论著", op, re.I)
            )
            if need_paper and not _clause_has_paper_edit(c, edits, app_text):
                gaps.append(
                    "【论文漏改】%s %s：意见涉及论文/贡献条目，但未找到锚定对应论文行的编辑"
                    % (cid, clause[:36])
                )
    return gaps


def detect_work_plan_overlimit(edits: list, app_text: str) -> list[str]:
    issues: list[str] = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        rep = str(e.get("replace") or "")
        if not rep.strip():
            continue
        sec = str(e.get("section") or e.get("_sec") or "")
        lim = resolve_limit(e.get("find") or "", rep, sec, app_text)
        if not lim:
            blob = rep + sec + str(e.get("find") or "")
            if re.search(r"工作设想|研发目标|技术路线|1500", blob):
                lim = resolve_limit(e.get("find") or "", rep, "工作设想", app_text)
        if not lim:
            continue
        from .form_reqs import _count_for_limit

        n = _count_for_limit(rep, lim)
        if n > int(lim["n"]):
            issues.append(
                "第 %d 条「%s」replace 现 %d 字，超过栏位上限 %d 字，须压缩后再落盘"
                % (i, lim["title"], n, lim["n"])
            )
    return issues


def detect_column_all_rows_gaps(clauses: list, edits: list, app_text: str) -> list[str]:
    from .inline_opinions import count_project_rows, is_column_wide_opinion

    n = count_project_rows(app_text)
    if n < 2:
        return []
    gaps = []
    for c in clauses or []:
        op = str(c.get("opinion") or "")
        if not _COL_ALL_OP.search(op) and not is_column_wide_opinion(op):
            continue
        cid = str(c.get("cid") or c.get("sourceId") or "")
        matched = _edits_for_clause(c, edits)
        if len(matched) < n:
            gaps.append(
                "【整列漏改】" + cid + "：意见针对该栏全部 " + str(n)
                + " 项，当前仅 " + str(len(matched)) + " 条编辑，须为每一项各改一条"
            )
    return gaps


def detect_salary_gaps(clauses: list, edits: list, app_text: str) -> list[str]:
    gaps = []
    blob_app = str(app_text or "")
    if "薪酬标准" not in blob_app and "年薪" not in blob_app:
        return []
    for c in clauses or []:
        op = str(c.get("opinion") or "") + str(c.get("clause") or "")
        if not _SALARY_OP.search(op):
            continue
        cid = str(c.get("cid") or c.get("sourceId") or "")
        ok = False
        for e in _edits_for_clause(c, edits):
            b = _blob(e)
            if re.search(r"年薪|薪酬标准", b) or re.search(r"\d{2,3}\s*万", str(e.get("replace") or "")):
                ok = True
                break
        if not ok:
            gaps.append("【年薪漏改】" + cid + "：须锚定「拟提供申报人薪酬标准」栏按意见调整年薪")
    return gaps


def detect_title_gaps(clauses: list, edits: list) -> list[str]:
    gaps = []
    for c in clauses or []:
        op = str(c.get("opinion") or "") + str(c.get("clause") or "")
        if not _TITLE_OP.search(op):
            continue
        cid = str(c.get("cid") or c.get("sourceId") or "")
        ok = False
        for e in _edits_for_clause(c, edits):
            find = str(e.get("find") or "")
            rep = str(e.get("replace") or "")
            if "终身副教授" in find and "终身副教授" not in rep and "副教授" in rep:
                ok = True
                break
            if "终身副教授" in find and "副教授" in rep:
                ok = True
                break
        if not ok:
            gaps.append("【职务漏改】" + cid + "：须把「终身副教授」改为「副教授」（所有出现处）")
    return gaps


def _is_company_name_field(find: str, app_text: str) -> bool:
    f = str(find or "")
    if _COMPANY_FIELD.search(f):
        return True
    raw = str(app_text or "")
    i = raw.find(f[:24]) if f else -1
    if i < 0:
        return False
    window = raw[max(0, i - 40): i + 12]
    return bool(_COMPANY_FIELD.search(window))


def declaring_company_names(app_text: str, extra: list | None = None) -> list[str]:
    from .matcher import extract_company, String_splitlines

    names = []
    co = extract_company(String_splitlines(app_text))
    if co:
        names.append(co)
    for x in extra or []:
        s = str(x or "").strip()
        if s and s not in names:
            names.append(s)
    names = [n for n in names if len(re.sub(r"\s+", "", n)) >= 4]
    names.sort(key=len, reverse=True)
    return names


def sanitize_declaring_company_edits(edits: list, app_text: str, extra_names: list | None = None) -> tuple[list, list[str]]:
    """正文里的申报企业全称改为「申报企业」；去掉虚构合作与「过往中试发生在拟入职企业」。"""
    names = declaring_company_names(app_text, extra_names)
    issues: list[str] = []
    out = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        ne = dict(e)
        find = str(ne.get("find") or "")
        rep = str(ne.get("replace") or "")
        if not _is_company_name_field(find, app_text):
            new = rep
            for n in names:
                if n and n in new:
                    new = new.replace(n, "申报企业")
            if new != rep:
                issues.append("【申报企业脱敏】第 %d 条正文企业全称已替换为「申报企业」" % i)
                rep = new
        stripped = _FAKE_COOP.sub("", rep)
        stripped = re.sub(r"[，,]{2,}", "，", stripped)
        if stripped != rep:
            issues.append("【虚构合作已删】第 %d 条删除了申报企业与人才现单位的无依据合作表述" % i)
            rep = stripped
        if _PAST_DONE.search(rep) and "申报企业" in rep and (ne.get("section") or ne.get("_sec") or "") in ("项目", "专长成果", "论文"):
            fixed = _PAST_AT_FIRM.sub("已在合作单位完成转化", rep)
            if fixed != rep:
                issues.append("【过往成果归属】第 %d 条已去掉把已完成中试/量产写成在申报企业完成的表述" % i)
                rep = fixed
        ne["replace"] = rep
        out.append(ne)
    return out, issues


def validate_structured_edits(
    edits: list,
    clauses: list,
    app_text: str,
    hj: bool = False,
) -> tuple[list, list[str]]:
    """计划/落盘前结构化校验：拆合并、剔串栏、检行覆盖与限字。"""
    issues: list[str] = []
    hj = hj or is_hj_app(app_text=app_text)

    edits, merge_issues = enforce_no_merge(list(edits or []), app_text)
    issues.extend(merge_issues)

    edits, contrib_issues = filter_contribution_edits(edits, clauses)
    issues.extend(contrib_issues)

    edits, q_issues = filter_question_only_edits(edits, clauses)
    issues.extend(q_issues)

    if not hj:
        edits, feas_drop = remediate_feasibility_edits(edits, clauses, app_text, hj=hj)
        issues.extend(feas_drop)

    issues.extend(enforce_chronological_counts(clauses, edits, app_text))
    issues.extend(detect_row_coverage_gaps(clauses, edits, app_text, hj=hj))
    issues.extend(detect_work_plan_gaps(clauses, edits, app_text, hj=hj))
    issues.extend(detect_key_problem_rewrite_gaps(clauses, edits, app_text, hj=hj))
    issues.extend(detect_feasibility_gaps(clauses, edits, app_text, hj=hj))
    issues.extend(detect_support_conditions_gaps(clauses, edits, app_text, hj=hj))
    issues.extend(detect_column_all_rows_gaps(clauses, edits, app_text))
    issues.extend(detect_salary_gaps(clauses, edits, app_text))
    issues.extend(detect_title_gaps(clauses, edits))
    issues.extend(detect_work_plan_overlimit(edits, app_text))

    return edits, issues


def clauses_from_edits(edits: list) -> list:
    seen: dict[str, dict] = {}
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        cid = str(e.get("clauseId") or "").strip()
        if not cid:
            continue
        key = _norm_sid(cid)
        if key in seen:
            continue
        seen[key] = {
            "cid": cid,
            "sourceId": cid,
            "section": e.get("section") or e.get("_sec") or "",
            "clause": e.get("clause") or "",
            "opinion": e.get("opinion") or "",
        }
    return list(seen.values())
