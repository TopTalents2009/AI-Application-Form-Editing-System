# -*- coding: utf-8 -*-
"""结构化编辑硬约束：个人贡献、正序多行、1500字、教育/论文行覆盖。"""
from __future__ import annotations

import re

from .form_reqs import char_count, resolve_limit
from .hj_form import is_hj_app, remap_hj_section, remap_qm_section

# 只认表格行号「论文1 / 项目 2」，不认成果汇总里的「项目4项、论文4篇、专利7项」
_ITEM_MARK = re.compile(r"(?:论文|项目|专利|论著)\s*\d+(?!\s*[项篇个件次])", re.I)
_HEAD_PREFIX = re.compile(
    r"^[（(]?\s*[一二三四五六七八九十0-9]{1,3}\s*[)）、.．]\s*"
)
_HEADING_RENAME_OP = re.compile(r"更新小标题|规范标题|小标题.{0,6}更新|标题改为")
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
    if len(new_edits) < 2:
        return [edit]
    finds = {re.sub(r"\s+", "", str(x.get("find") or "")) for x in new_edits}
    if len(finds) <= 1:
        return [edit]
    return new_edits


def collapse_duplicate_find_edits(edits: list) -> tuple[list, list[str]]:
    """同一改前摘录被拆成多条（成果汇总「项目4项/论文4篇」）时合并为一条。"""
    out: list = []
    issues: list[str] = []
    seen: dict[str, dict] = {}
    merged = 0
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        key = str(e.get("_k") or "").strip() or _norm_find(e.get("find"))
        if not key:
            out.append(e)
            continue
        prev = seen.get(key)
        if prev is None:
            ne = dict(e)
            seen[key] = ne
            out.append(ne)
            continue
        merged += 1
        gem = str(e.get("opinionGemini") or prev.get("opinionGemini") or "")
        if len(str(e.get("find") or "")) > len(str(prev.get("find") or "")):
            prev["find"] = e.get("find")
        for cand in (str(e.get("replace") or ""), gem):
            if len(cand) > len(str(prev.get("replace") or "")):
                prev["replace"] = cand
        if gem:
            prev["opinionGemini"] = gem
    if merged:
        issues.append("【重复锚点】同一改前摘录的 %d 条拆分编辑已合并为 1 条" % (merged + 1))
    return out, issues


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


def _heading_core(s: str) -> str:
    s = str(s or "").strip().splitlines()[0].strip()
    s = _HEAD_PREFIX.sub("", s)
    return re.sub(r"[\s（）()【】\[\]]+", "", s)


def is_heading_only_edit(edit: dict) -> bool:
    """只改了「一、标题」↔「（一）标题」这类小标题，正文没有实质改写。"""
    if not isinstance(edit, dict):
        return False
    find = str(edit.get("find") or "").strip()
    rep = str(edit.get("replace") or "").strip()
    if not find or not rep:
        return False
    op = str(edit.get("opinion") or "") + str(edit.get("clause") or "")
    if _HEADING_RENAME_OP.search(op):
        return False
    cf, cr = _heading_core(find), _heading_core(rep)
    if len(cf) < 4 or len(cr) < 4:
        return False
    if not (cf == cr or cf in cr or cr in cf):
        return False
    find_body = "\n".join(find.splitlines()[1:]).strip()
    rep_body = "\n".join(rep.splitlines()[1:]).strip()
    extra = abs(len(re.sub(r"\s+", "", rep_body)) - len(re.sub(r"\s+", "", find_body)))
    if extra >= 24:
        return False
    if abs(len(re.sub(r"\s+", "", rep)) - len(re.sub(r"\s+", "", find))) >= 40 and len(rep) > 40:
        return False
    return True


def filter_heading_only_edits(edits: list) -> tuple[list, list[str]]:
    kept, issues = [], []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        if is_heading_only_edit(e):
            issues.append(
                "【仅改小标题】第 %d 条只改了章节标题格式，未按意见改写正文，已剔除"
                % i
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
    cids = "、".join(str(c.get("cid") or c.get("sourceId") or "") for c in meta[:5])
    if not any(_edit_in_work_plan(e, wp_span) for e in edits or []):
        return [
            "【工作计划漏改】" + cids + "：修改意见要求调整1500字「工作目标及可行性论证」栏，但未生成锚定该栏的编辑"
        ]
    wants_rd = any(
        re.search(r"研发目标|三年目标|未来三年|工作目标|工作计划|1500", str(c.get("opinion") or "") + str(c.get("clause") or ""))
        for c in meta
    )
    has_rd = any(
        _edit_in_work_plan(e, wp_span) and _RD_GOAL.search(str(e.get("find") or "") + str(e.get("replace") or ""))
        for e in (edits or [])
    )
    if wants_rd and not has_rd:
        return [
            "【研发目标漏改】" + cids + "：工作计划意见须改「（一）未来三年工作目标」中的研发目标段，不能只改其他小节"
        ]
    return []


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


_RANK_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(\*)?\s*/\s*(\d{1,2})(?!\d)")
_ROLE_RE = re.compile(r"第一作者兼通讯作者|共同第一作者、通讯作者|共同第一作者|第一作者|通讯作者|核心研究员|核心作者")
_EXPLICIT_FIRST = re.compile(r"改为第一作者|改成第一作者|应为第一作者|是第一作者")
_TITLE_STOP = {
    "produced", "factor", "cells", "through", "system", "journal", "nature", "science",
    "research", "university", "behavior", "immunity", "activation", "selective",
    "inhibition", "inflammation", "resolution", "effectors", "control", "function",
    "functions", "migration", "reports", "medicine",
}
_AUTHOR_FIELDS = ("replace", "opinionGemini", "opinionGrok", "opinionDoubao")


def _title_tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[A-Za-z][A-Za-z'\-]{5,}", str(text or "").lower())
        if t not in _TITLE_STOP
    }


def _last_rank(text: str):
    ms = list(_RANK_RE.finditer(str(text or "")))
    if not ms:
        return None
    m = ms[-1]
    pos, total = int(m.group(1)), int(m.group(3))
    if pos < 1 or total < 1 or pos > total:
        return None
    return {"pos": pos, "star": bool(m.group(2)), "total": total, "span": m.span(), "raw": m.group(0)}


def _claims_first(text: str) -> bool:
    blob = str(text or "")
    return "第一作者" in blob or "共同第一" in blob


def _same_paper(a: str, b: str) -> bool:
    inter = _title_tokens(a) & _title_tokens(b)
    if any(len(t) >= 12 for t in inter):
        return True
    return len(inter) >= 3


def _kept_role(find_rank: dict, find_text: str) -> str:
    roles = _ROLE_RE.findall(find_text or "")
    if find_rank["pos"] > 1:
        kept = []
        for r in roles:
            if "第一" in r or r in kept:
                continue
            kept.append(r)
        if find_rank["star"] and "通讯作者" not in kept:
            kept.insert(0, "通讯作者")
        return "、".join(kept) if kept else ("通讯作者" if find_rank["star"] else "")
    if find_rank["star"]:
        return "第一作者兼通讯作者"
    return "第一作者"


def _apply_rank_and_role(text: str, rank_span: tuple, rank_raw: str, role_keep: str) -> str:
    new = text[: rank_span[0]] + rank_raw + text[rank_span[1] :]
    lines = new.split("\n")
    idx = len(lines) - 1
    while idx > 0 and not lines[idx].strip():
        idx -= 1
    line = lines[idx]
    if _ROLE_RE.search(line):
        cleaned = _ROLE_RE.sub("", line)
        cleaned = re.sub(r"[、,，]{2,}", "、", cleaned).strip(" \t、,，")
        if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned):
            lines[idx] = role_keep
        else:
            lines[idx] = cleaned
            if role_keep and role_keep not in cleaned:
                lines.insert(idx + 1, role_keep)
    elif role_keep:
        lines.append(role_keep)
    return "\n".join(x for x in lines if x is not None)


def _downgrade_author_text(find: str, text: str) -> tuple[str, bool]:
    fr = _last_rank(find)
    rr = _last_rank(text)
    if not fr or not rr or fr["pos"] <= 1 or _claims_first(find):
        return text, False
    if not _same_paper(find, text):
        return text, False
    better = rr["pos"] < fr["pos"]
    if not better and not _claims_first(text):
        return text, False
    role = _kept_role(fr, find)
    new = _apply_rank_and_role(text, rr["span"], fr["raw"], role)
    return new, new != text


def sanitize_paper_author_edits(edits: list) -> tuple[list, list[str]]:
    """同一篇论文更新题名/期刊时，不得把非第一作者改成第一作者或更靠前的排序。"""
    issues: list[str] = []
    out = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        ne = dict(e)
        op = str(ne.get("opinion") or "") + str(ne.get("clause") or "")
        find = str(ne.get("find") or "")
        if _EXPLICIT_FIRST.search(op) or not _last_rank(find):
            out.append(ne)
            continue
        changed = False
        for key in _AUTHOR_FIELDS:
            raw = str(ne.get(key) or "")
            if not raw.strip():
                continue
            fixed, did = _downgrade_author_text(find, raw)
            if did:
                ne[key] = fixed
                changed = True
        if changed:
            issues.append(
                "【作者位次】第 %d 条同一篇论文保留原文作者排序与角色，未改成第一作者" % i
            )
        out.append(ne)
    return out, issues


_COVER_NAME = re.compile(
    r"有效证件姓名|申报人姓名|Name of (?:Applicant|ID)|中文[（(]音译[）)]名"
    r"|申\s*报\s*人\s{1,}[A-Za-z\u4e00-\u9fff]",
    re.I,
)
_NAME_CLAUSE = re.compile(r"(更正|核准|修改|改为).{0,12}(申报人姓名|有效证件姓名|封面申报人|姓名)")
_BIRTH_CLAUSE = re.compile(r"(更正|核准|修改|改为).{0,12}出生日期|出生日期.{0,12}(改为|更正|核准)")
_BIRTH_LABEL = re.compile(r"出生日期|Date of Birth", re.I)
_DATE_ONLY = re.compile(r"^(?:19|20)\d{2}[-./年]\s*\d{1,2}[-./月]\s*\d{1,2}日?$")
_YEAR_HEADER_OP = re.compile(
    r"模板修改意见|出生日期\s*[:：]\s*(?:19|20)\d{2}|申报人\s*[:：]\s*[A-Za-z\u4e00-\u9fff]"
)


def _compact_id(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def form_identity_values(app_text: str) -> tuple[set[str], set[str]]:
    """抽出申报书封面/基本信息里的姓名与出生日期，供拦截身份栏编辑。"""
    names: set[str] = set()
    dates: set[str] = set()
    raw = str(app_text or "")
    if not raw:
        return names, dates
    for pat in (
        r"有效证件姓名\s*[:：\n]\s*([A-Za-z\u4e00-\u9fff·•][A-Za-z\u4e00-\u9fff·•\s.'-]{1,58})",
        r"申报人姓名\s*[:：]\s*([^\n]{2,60})",
        r"申\s*报\s*人\s{2,}([A-Za-z\u4e00-\u9fff·•][A-Za-z\u4e00-\u9fff·•\s.'-]{1,58})",
        r"Name of (?:Applicant|ID)\s*[:：\n]\s*([A-Za-z][A-Za-z\s.'-]{1,58})",
    ):
        for m in re.finditer(pat, raw, re.I):
            v = _compact_id(m.group(1).split("\n")[0])
            if 4 <= len(v) <= 48:
                names.add(v)
    head = raw[:2800]
    win = ""
    m = re.search(r"(?:出生日期|Date of Birth)(.{0,260})", head, re.I | re.S)
    if m:
        win += m.group(1)
    m2 = re.search(r"性别(.{0,220})国籍", head, re.S)
    if m2:
        win += m2.group(1)
    for m in re.finditer(r"(?:19|20)\d{2}[-./年]\s*\d{1,2}[-./月]\s*\d{1,2}日?", win or ""):
        dates.add(_compact_id(m.group(0)))
    return names, dates


def is_identity_field_edit(edit: dict, app_text: str = "") -> bool:
    """申报人姓名、有效证件姓名、出生日期属于身份栏，不能按意见改。"""
    if not isinstance(edit, dict):
        return False
    find = str(edit.get("find") or "")
    replace = str(edit.get("replace") or "")
    clause = str(edit.get("clause") or "")
    opinion = str(edit.get("opinion") or edit.get("clause") or "")
    if _compact_id(find) == _compact_id(replace):
        return False
    if _COVER_NAME.search(find) or _NAME_CLAUSE.search(clause):
        return True
    if _BIRTH_CLAUSE.search(clause):
        return True
    if _BIRTH_LABEL.search(find) and not re.search(r"出生年月在.{0,16}以前", opinion):
        return True
    find_d, replace_d = _compact_id(find), _compact_id(replace)
    if _DATE_ONLY.match(find_d) and _DATE_ONLY.match(replace_d) and (
        _BIRTH_CLAUSE.search(clause) or _BIRTH_LABEL.search(clause + opinion)
    ):
        return True
    if _YEAR_HEADER_OP.search(opinion) and not re.search(r"修改前|修改后", opinion):
        if _COVER_NAME.search(find + clause) or _BIRTH_LABEL.search(clause + opinion):
            return True
        if _DATE_ONLY.match(find_d) and _DATE_ONLY.match(replace_d):
            return True
    names, dates = form_identity_values(app_text)
    if names and find_d in names and len(find_d) >= 6:
        return True
    if dates and _DATE_ONLY.match(find_d) and find_d in dates:
        return True
    return False


def retarget_pre_return_title_edits(edits: list, app_text: str = "") -> tuple[list, list[str]]:
    """意见改回国前职务时，禁止锚到「相当于国内职务情况」。"""
    from .manual_fill import (
        _EQ_DOMESTIC, _PRE_RETURN_TITLE, _pre_return_title_kind, locate_pre_return_title,
    )

    issues: list[str] = []
    out = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        ne = dict(e)
        blob = str(ne.get("opinion") or "") + str(ne.get("clause") or "")
        find = str(ne.get("find") or "")
        if not _PRE_RETURN_TITLE.search(blob) or _EQ_DOMESTIC.search(blob):
            out.append(ne)
            continue
        if not _EQ_DOMESTIC.search(find) and "相当于国内" not in find:
            out.append(ne)
            continue
        kind = _pre_return_title_kind(blob)
        hit, _ = locate_pre_return_title(app_text, kind)
        if not hit:
            issues.append("【回国前职务定位】第 %d 条已剔除：锚到了「相当于国内职务」，无法改写回国前职务栏" % i)
            continue
        old_part = find.split("相当于国内")[0].strip()
        old_lines = [ln.strip() for ln in old_part.splitlines() if ln.strip()]
        old = old_lines[-1] if old_lines else ""
        new_part = str(ne.get("replace") or "").split("相当于国内")[0].strip()
        new_lines = [ln.strip() for ln in new_part.splitlines() if ln.strip()]
        new = new_lines[-1] if new_lines else str(ne.get("replace") or "")
        if old and old in hit and new:
            ne["find"] = hit
            ne["replace"] = hit.replace(old, new, 1)
        else:
            ne["find"] = hit
            ne["replace"] = hit if not new else (hit.rsplit("\n", 1)[0] + "\n" + new if "\n" in hit else new)
        issues.append("【回国前职务定位】第 %d 条已改锚到「回国(来华)前职务」，不再改相当于国内职务" % i)
        out.append(ne)
    return out, issues


def sanitize_identity_edits(edits: list, app_text: str = "") -> tuple[list, list[str]]:
    """剔除改申报人姓名 / 出生日期的编辑。"""
    issues: list[str] = []
    out = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        if is_identity_field_edit(e, app_text=app_text):
            issues.append(
                "【身份栏不可改】第 %d 条已剔除：申报人姓名与出生日期不能按意见或人才库改写" % i
            )
            continue
        out.append(e)
    return out, issues


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


_VAGUE_DEGREE = re.compile(r"显著[的地]?")
_CO_NAME = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()]{2,40}(?:股份)?有限公司")
_ZH_JOURNAL_GLOSS = re.compile(r"[（(][\u4e00-\u9fff]{2,16}[）)]")
_METRIC_TOKEN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％)"
    r"|\d+(?:\.\d+)?\s*亿(?:元)?"
    r"|\d+(?:\.\d+)?\s*万(?:元)?"
    r"|\d+(?:\.\d+)?\s*小时"
)
_RD_GOAL = re.compile(r"研发目标|未来三年工作目标|（一）\s*未来三年")


def _norm_metric(tok: str) -> str:
    return re.sub(r"\s+", "", tok or "")


def _metric_set(text: str) -> set[str]:
    return {_norm_metric(m.group(0)) for m in _METRIC_TOKEN.finditer(str(text or ""))}


def _tidy_grounded(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"[。．]{2,}", "。", s)
    s = re.sub(r"^[，,。；;\s]+", "", s)
    s = re.sub(r"[，,；;\s]+$", "。", s)
    s = re.sub(r"\s{2,}", "", s)
    return s.strip()


def _strip_unknown_companies(text: str, allowed: str) -> str:
    s = str(text or "")
    for m in list(_CO_NAME.finditer(s)):
        name = m.group(0)
        if name and name in allowed:
            continue
        s = re.sub(
            r"(?:依托与|联合|与)?" + re.escape(name) + r"(?:战略合作|合作)?(?:及既有示范工程)?",
            "",
            s,
        )
    s = re.sub(r"依托与[，,]?", "", s)
    s = re.sub(r"战略合作及既有示范工程[，,]?", "", s)
    return s


def _strip_new_metrics(text: str, allowed: set[str]) -> str:
    parts = re.split(r"([。；;，,])", str(text or ""))
    kept = []
    skip_punct = False
    for part in parts:
        if part in "。；;，,":
            if skip_punct:
                skip_punct = False
                continue
            if kept:
                kept.append(part)
            continue
        toks = _metric_set(part)
        if toks and any(t not in allowed for t in toks):
            skip_punct = True
            continue
        kept.append(part)
        skip_punct = False
    return "".join(kept)


def _strip_journal_gloss(find: str, replace: str) -> str:
    if not re.search(r"[A-Za-z]{4,}", find or ""):
        return replace
    out = replace
    for m in list(_ZH_JOURNAL_GLOSS.finditer(replace or "")):
        gloss = m.group(0)
        if gloss and gloss not in (find or ""):
            out = out.replace(gloss, "")
    return out


def sanitize_grounded_edits(edits: list, app_text: str, pool_text: str = "") -> tuple[list, list[str]]:
    """去掉原文/人才库没有的公司名、新量化数字、期刊中文译名和「显著」。"""
    allowed_blob = str(app_text or "") + "\n" + str(pool_text or "")
    issues: list[str] = []
    out = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        ne = dict(e)
        find = str(ne.get("find") or "")
        rep = str(ne.get("replace") or "")
        if not rep.strip():
            out.append(ne)
            continue
        allowed_metrics = _metric_set(find + "\n" + allowed_blob)
        new = _VAGUE_DEGREE.sub("", rep)
        if new != rep:
            issues.append("【空泛措辞】第 %d 条已去掉「显著」等程度词" % i)
            rep = new
        new = _strip_journal_gloss(find, rep)
        if new != rep:
            issues.append("【期刊名】第 %d 条已去掉原文没有的中文译名括注" % i)
            rep = new
        new = _strip_unknown_companies(rep, find + "\n" + allowed_blob)
        if new != rep:
            issues.append("【无关企业】第 %d 条已去掉申报书未出现的公司全称" % i)
            rep = new
        new = _strip_new_metrics(rep, allowed_metrics)
        if new != rep:
            issues.append("【无依据数据】第 %d 条已去掉原文/附件没有的量化数字" % i)
            rep = new
        rep = _tidy_grounded(rep)
        if not rep or _norm_find(rep) == _norm_find(find):
            issues.append("【无依据改写】第 %d 条清洗后与原文无实质差异，已跳过" % i)
            continue
        ne["replace"] = rep
        for key in ("opinionGemini", "opinionGrok", "opinionDoubao"):
            raw = str(ne.get(key) or "")
            if raw.strip():
                ne[key] = _tidy_grounded(_strip_new_metrics(
                    _strip_unknown_companies(_strip_journal_gloss(find, _VAGUE_DEGREE.sub("", raw)), find + "\n" + allowed_blob),
                    allowed_metrics,
                )) or raw
        out.append(ne)
    return out, issues


_BOARD_ASK = re.compile(r"编委|客座编辑|特约主编")
_BOARD_LIST = re.compile(r"列出|要列|补充|写上|体现")
_GUEST_TOPIC = re.compile(r"策划并主编([^。；;\n]{2,80}?专题)")
_NAMED_ROLE = re.compile(
    r"(?:担任|现任|曾任)[^。；;\n]{0,24}《[^》]{2,40}》[^。；;\n]{0,16}(?:编委|副主编|主编|客座编辑|特约主编)"
)
_OTHER_HEAD = re.compile(r"4\s*[\.、．]\s*其他[（(]包括在国际学术会议做重要报告等情况[）)]")
_BOARD_FIELDS = ("replace", "opinionGemini", "opinionGrok", "opinionDoubao")


def _compact_board(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _asks_editorial_board(text: str) -> bool:
    s = _compact_board(text)
    return bool(_BOARD_ASK.search(s) and _BOARD_LIST.search(s))


def _editorial_facts(*blobs: str) -> list[str]:
    text = "\n".join(str(x or "") for x in blobs)
    found: list[str] = []
    for m in _GUEST_TOPIC.finditer(text):
        fact = "策划并主编" + _compact_board(m.group(1))
        if fact not in found:
            found.append(fact)
    for m in _NAMED_ROLE.finditer(text):
        fact = _compact_board(m.group(0))
        if fact not in found:
            found.append(fact)
    return found[:4]


def _other_section_anchor(app_text: str) -> str:
    m = _OTHER_HEAD.search(str(app_text or ""))
    if not m:
        return ""
    window = str(app_text or "")[m.start(): m.start() + 180]
    lines = [ln.strip() for ln in window.splitlines() if ln.strip()]
    if not lines:
        return ""
    anchor = lines[0]
    if len(lines) > 1 and lines[1].lower().startswith("others"):
        anchor += "\n" + lines[1]
    return anchor


def _edit_has_fact(edit: dict, facts: list[str]) -> bool:
    blob = _compact_board("".join(str(edit.get(k) or "") for k in _BOARD_FIELDS))
    return any(_compact_board(f) in blob for f in facts)


def sanitize_editorial_board_edits(
    edits: list,
    clauses: list,
    app_text: str,
    pool_text: str = "",
    leftovers: list | None = None,
) -> tuple[list, list[str], list]:
    """意见要求列出核心期刊编委时，只用申报书或人才库里已写明的任职，并固定写入「4.其他」。"""
    issues: list[str] = []
    asks = [c for c in (clauses or []) if isinstance(c, dict) and _asks_editorial_board(
        str(c.get("opinion") or "") + str(c.get("clause") or "")
    )]
    lo = list(leftovers or [])
    if not asks:
        return list(edits or []), issues, lo
    facts = _editorial_facts(app_text, pool_text)
    cids = {str(c.get("cid") or "") for c in asks if c.get("cid")}
    kept = []
    dropped = False
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        blob = str(e.get("opinion") or "") + str(e.get("clause") or "") + str(e.get("clauseId") or "")
        if _asks_editorial_board(blob) or str(e.get("clauseId") or "") in cids:
            if facts and _edit_has_fact(e, facts):
                kept.append(e)
                continue
            if _BOARD_ASK.search(_compact_board(
                "".join(str(e.get(k) or "") for k in _BOARD_FIELDS) + blob
            )):
                dropped = True
                continue
        kept.append(e)
    if dropped:
        issues.append("【编委任职】已去掉申报书和人才库中没有的编委、主编或审稿身份")
    covered = any(_edit_has_fact(e, facts) for e in kept) if facts else False
    anchor = _other_section_anchor(app_text)
    if facts and not covered and anchor and anchor in str(app_text or ""):
        src = asks[0]
        body = "学术兼职：" + "；".join(facts) + "。"
        replace = anchor + "\n" + body
        item = {
            "find": anchor,
            "replace": replace,
            "clause": str(src.get("clause") or "列出核心期刊编委"),
            "opinion": str(src.get("opinion") or ""),
            "opinionGemini": replace,
            "opinionGrok": "",
            "opinionDoubao": "",
            "opName": str(src.get("opName") or ""),
            "clauseId": str(src.get("cid") or ""),
            "section": str(src.get("section") or "专长成果"),
            "_sec": str(src.get("section") or "专长成果"),
            "_k": _compact_board(anchor),
            "appNo": str(src.get("appNo") or ""),
        }
        kept.append(item)
        covered = True
        issues.append("【编委任职】已按已有材料写入「4.其他」：" + "；".join(facts))
    if covered:
        nxt = []
        seen = set()
        for line in lo:
            s = str(line)
            key = _compact_board(s)
            if key in seen:
                continue
            if any(cid and cid in s for cid in cids) and re.search(r"编委|主编|审稿", s):
                continue
            seen.add(key)
            nxt.append(line)
        lo = nxt
    return kept, issues, lo


def validate_structured_edits(
    edits: list,
    clauses: list,
    app_text: str,
    hj: bool = False,
) -> tuple[list, list[str]]:
    """计划/落盘前结构化校验：拆合并、剔串栏、检行覆盖与限字。"""
    issues: list[str] = []
    hj = hj or is_hj_app(app_text=app_text)

    edits, dup_issues = collapse_duplicate_find_edits(list(edits or []))
    issues.extend(dup_issues)

    edits, merge_issues = enforce_no_merge(edits, app_text)
    issues.extend(merge_issues)

    edits, contrib_issues = filter_contribution_edits(edits, clauses)
    issues.extend(contrib_issues)

    edits, q_issues = filter_question_only_edits(edits, clauses)
    issues.extend(q_issues)

    edits, head_issues = filter_heading_only_edits(edits)
    issues.extend(head_issues)

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
