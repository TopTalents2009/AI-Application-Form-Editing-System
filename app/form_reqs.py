# -*- coding: utf-8 -*-
"""从申报书正文抽出填表须知与栏位限字/限项，供计划提示与落盘后质检。"""
from __future__ import annotations
import re

_SKIP_HEADINGS = {
    "重要经历", "重要履历", "主要技术能力", "标志性成果", "业务领域", "核心技术", "发展情况",
    "经营战略", "主要客户群", "产业链地位", "产业链位置和地位", "引进必要性", "岗位匹配", "工作基础",
    "思想道德品质", "专业能力水平", "从事技术领域重要性", "与国外差距及急需紧缺性分析",
}

_CHAR_TAIL = re.compile(
    r"(?P<title>[\u4e00-\u9fffA-Za-z0-9、，,（）()]{2,40}?)[（(](?:限\s*)?(?P<n>\d+)\s*字以内[）)]"
)
_CHAR_LIMIT = re.compile(
    r"(?P<title>[\u4e00-\u9fffA-Za-z0-9、，,（）()]{2,40}?)[（(]限\s*(?P<n>\d+)\s*字[）)]"
)
_ITEM_LIMIT = re.compile(
    r"(?P<title>[\u4e00-\u9fffA-Za-z0-9、，,（）()]{2,80}?)[（(]限\s*(?P<n>\d+)\s*项[）)]"
)
_BARE_CHAR = re.compile(r"^[（(](?:限\s*)?(\d+)\s*字(?:以内)?[）)]$")
_BARE_ITEM = re.compile(r"^[（(]限\s*(\d+)\s*项[）)]$")


def char_count(s: str) -> int:
    """与表单习惯一致：去掉空白后计字（汉字、字母、数字、标点均计 1）。"""
    return len(re.sub(r"\s+", "", str(s or "")))


def glue_limits(text: str) -> str:
    """把换行拆开的「标题 / （300字以内）」拼回一行。"""
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n[ \t]*[（(](\d+)\s*字以内[）)]", r"（\1字以内）", t)
    t = re.sub(r"\n[ \t]*[（(]限\s*(\d+)\s*字[）)]", r"（限\1字）", t)
    t = re.sub(r"\n[ \t]*[（(]限\s*(\d+)\s*项[）)]", r"（限\1项）", t)
    t = re.sub(r"\n[ \t]*[（(]限\s*(\d+)\s*字[）)]", r"（限\1字）", t)
    t = re.sub(r"项目成果\n[ \t]*[（(]限\s*(\d+)\s*字[）)]", r"项目成果（限\1字）", t)
    t = re.sub(r"简述个人贡献\n[ \t]*[（(]限\s*(\d+)\s*字[）)]", r"简述个人贡献（限\1字）", t)
    return t


def _uniq_fields(matches, kind):
    seen = set()
    out = []
    for m in matches:
        title = re.sub(r"\s+", "", m.group("title")).strip("：:，,")
        n = int(m.group("n"))
        key = (title, n, kind)
        if key in seen or not title:
            continue
        seen.add(key)
        out.append({"title": title, "n": n, "kind": kind})
    return out


def parse_fields(app_text: str):
    glued = glue_limits(app_text)
    fields = []
    fields.extend(_uniq_fields(_CHAR_TAIL.finditer(glued), "chars"))
    fields.extend(_uniq_fields(_CHAR_LIMIT.finditer(glued), "chars"))
    fields.extend(_uniq_fields(_ITEM_LIMIT.finditer(glued), "items"))
    return glued, fields


def _notice_block(text: str) -> str:
    m = re.search(r"填表须知\s*\n([\s\S]{20,4000}?)(?=\n一、基本信息)", text)
    if not m:
        m = re.search(r"填表须知\s*\n([\s\S]{20,4000}?)(?=\n一、)", text)
    if not m:
        return ""
    body = m.group(1).strip()
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    keep = []
    for ln in lines:
        if re.match(r"^[一二三四五六七八九十]、", ln) or re.match(r"^\d+、", ln):
            keep.append(ln)
        elif keep and len(ln) < 120:
            keep[-1] = keep[-1] + ln
        if len(keep) >= 12:
            break
    return "\n".join(keep)


def _band(n: int):
    return int(round(n * 0.8)), int(round(n * 0.9))


def extract_form_requirements(app_text: str) -> str:
    glued, fields = parse_fields(app_text)
    chunks = []
    notice = _notice_block(glued)
    if notice:
        chunks.append("填表须知（原文，必须遵守）：\n" + notice)
    if fields:
        lines = [
            "栏位印刷要求（最高优先级，覆盖 rules 规则包里的 500 字 / 400-450 字等口径；replace 去空白后不得超过上限）："
        ]
        for f in fields:
            if f["kind"] == "chars":
                lo, hi = _band(f["n"])
                lines.append(
                    "- 「%s」上限 %d 字，replace 写入后该栏全文不得超过 %d 字，合格带 %d–%d 字（上限的 80%%～90%%）。"
                    % (f["title"], f["n"], f["n"], lo, hi)
                )
            else:
                lines.append("- 「%s」上限 %d 项，不得增删栏位或超出项数。" % (f["title"], f["n"]))
        chunks.append("\n".join(lines))
    bio = next((f for f in fields if "申报人基本情况" in f["title"] and f["kind"] == "chars"), None)
    if bio:
        n = bio["n"]
        lo, hi = _band(n)
        chunks.append(
            "「申报人基本情况」以本表 %d 字为准，禁止套用规则包里的 500 字 / 400-450 字口径。"
            "三个小标题下正文合计（不含「重要经历」「主要技术能力」「标志性成果」标题本身）上限 %d 字，合格带 %d–%d 字；"
            "配比约 重要经历 24%%、主要技术能力 37%%、标志性成果 39%%。"
            % (n, n, lo, hi)
        )
    extra = []
    if "时间必须连续" in glued or "博士后属于工作经历" in glued:
        extra.append("工作经历须按时间顺序且时间连续；博士后计入工作经历。")
    if "①职务职责" in glued or "②贡献" in glued:
        extra.append("「职务职责和贡献」须保持原文「①职务职责 / ②贡献」分段，不得改成单段叙述。")
    if re.search(r"论文\s*1", glued):
        extra.append(
            "代表性论文条目须保持原文体例（如：完成人排序,中文题/英文题,期刊,卷（期）（年）,页码,引用,影响因子），"
            "只改对应字段内容，不得改成叙述句，不得新增本表没有的栏目。"
        )
    if "万元" in glued and "小数点后保留一位" in glued:
        extra.append("金额以人民币万元为单位，小数点后保留一位。")
    if extra:
        chunks.append("其它印刷体例：\n- " + "\n- ".join(extra))
    if not chunks:
        return "（未能从申报书解析出填表须知或限字栏位，仍须遵守申报书印刷的字数/项数/体例。）"
    return "\n\n".join(chunks)


def _slice_between(text: str, start_pat: str, end_pat: str) -> str:
    m = re.search(start_pat, text)
    if not m:
        return ""
    rest = text[m.end():]
    m2 = re.search(end_pat, rest)
    return rest[: m2.start()] if m2 else rest


def _content_chars(block: str) -> int:
    parts = []
    for ln in str(block or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s in _SKIP_HEADINGS:
            continue
        if _BARE_CHAR.match(s) or _BARE_ITEM.match(s):
            continue
        if re.match(r"^（[一二三四五六七八九十]+）", s) and char_count(s) <= 24:
            continue
        parts.append(s)
    return char_count("".join(parts))


def check_text_limits(app_text: str) -> list:
    """检查正文是否突破申报书印刷的字数上限。返回中文说明列表。"""
    glued, fields = parse_fields(app_text)
    issues = []
    by_title = {(f["title"], f["kind"]): f for f in fields}

    specs = [
        ("申报人基本情况", r"申报人基本情况（\d+字以内）", r"引进企业基本情况|二、"),
        ("引进企业基本情况", r"引进企业基本情况（\d+字以内）", r"二、|主要学历"),
        ("申报人推荐理由", r"（四）?申报人推荐理由（限\d+字）", r"（五）|拟提供申报人支持条件"),
        ("拟提供申报人支持条件", r"（五）?拟提供申报人支持条件（\d+字以内）", r"申报人和本企业|特推荐"),
        ("申报人拟实现工作目标及可行性论证", r"申报人拟实现工作目标及可行性论证（限\d+字）", r"六、|用人单位|企业基本情况"),
    ]
    for title, start, end in specs:
        f = by_title.get((title, "chars"))
        if not f:
            continue
        block = _slice_between(glued, start, end)
        n = _content_chars(block)
        if n > f["n"]:
            issues.append(
                "「%s」现 %d 字，超过申报书印刷上限 %d 字（合格带 %d–%d 字），须压缩后再落盘。"
                % (title, n, f["n"], _band(f["n"])[0], _band(f["n"])[1])
            )

    if ("职务职责和贡献", "chars") in by_title:
        lim = by_title[("职务职责和贡献", "chars")]["n"]
        chunks = re.findall(r"(①职务职责[\s\S]{0,800}?②贡献[\s\S]{0,800}?)(?=①职务职责|全职|兼职|四、|$)", glued)
        for i, ch in enumerate(chunks, 1):
            n = char_count(ch)
            if n > lim:
                issues.append("第 %d 段「职务职责和贡献」现 %d 字，超过上限 %d 字。" % (i, n, lim))

    for label, lim_title in (("项目成果", "项目成果"), ("简述个人贡献", "简述个人贡献")):
        f = by_title.get((lim_title, "chars")) or by_title.get((label, "chars"))
        if not f:
            continue
        # 取表格值段落：标题后到下一标题之间、排除标题本身
        # 简化：任何连续正文段 > 上限
    return issues


_BIO_PARTS = (
    ("重要经历", 0.24, ("重要经历", "重要履历")),
    ("主要技术能力", 0.37, ("主要技术能力",)),
    ("标志性成果", 0.39, ("标志性成果", "标志性的成果")),
)
_ENT_PARTS = (
    ("业务领域", 0.22, ("业务领域",)),
    ("核心技术", 0.26, ("核心技术",)),
    ("经营战略", 0.18, ("经营战略",)),
    ("主要客户群", 0.16, ("主要客户群", "客户群")),
    ("产业链地位", 0.18, ("产业链地位", "产业链位置")),
)
_PART_SPLIT = re.compile(r"(?=①|②|③|④|⑤)")

_SEC_FIELDS = {
    "基本信息": ("申报人基本情况", "引进企业基本情况"),
    "工作": ("职务职责和贡献",),
    "项目": ("项目成果", "简述个人贡献", "申报人拟实现工作目标及可行性论证"),
    "其他": ("申报人推荐理由", "拟提供申报人支持条件"),
}

_TEXT_KEYS = (
    ("replace", "修改后"),
    ("opinionGemini", "Gemini修改意见"),
)


def _field_by_title(fields, title: str):
    title = str(title or "")
    for f in fields or []:
        if f.get("kind") == "chars" and title in str(f.get("title") or ""):
            return f
    return None


def _part_hits(text: str, parts) -> tuple[list, float]:
    hits, share = [], 0.0
    blob = str(text or "")
    for name, frac, keys in parts:
        if any(k in blob for k in keys):
            hits.append(name)
            share += float(frac)
    return hits, share


def _heading_from_find(find: str, app_text: str, parts) -> tuple[list, float]:
    """小标题优先看 find；正文未写标题时，到申报书原文里找上一格标题。"""
    hits, share = _part_hits(find, parts)
    if hits:
        return hits, share
    find = str(find or "").strip()
    if len(find) < 8:
        return [], 0.0
    glued = glue_limits(app_text)
    idx = glued.find(find[:24])
    if idx < 0:
        compact_g = re.sub(r"\s+", "", glued)
        compact_f = re.sub(r"\s+", "", find)[:32]
        j = compact_g.find(compact_f)
        if j < 0:
            return [], 0.0
        seen = 0
        idx = 0
        for i, ch in enumerate(glued):
            if ch.isspace():
                continue
            if seen == j:
                idx = i
                break
            seen += 1
    window = glued[max(0, idx - 160): idx + 16]
    return _part_hits(window, parts)


def _limit_from_parts(field, hits, share) -> dict:
    n_all = int(field["n"])
    if share >= 0.95:
        n = n_all
        title = field["title"]
        names = []
    else:
        n = max(20, int(round(n_all * share)))
        title = field["title"] + "·" + "、".join(hits)
        names = list(hits)
    lo, hi = _band(n)
    return {"title": title, "n": n, "lo": lo, "hi": hi, "content": True, "parts": names}


def crop_to_parts(text: str, names: list) -> str:
    """火山常把①-⑤整栏写进一条：只留下 find 对应的小标题段。"""
    raw = str(text or "")
    names = [n for n in (names or []) if n]
    if not raw.strip() or not names:
        return raw
    chunks = [p.strip() for p in _PART_SPLIT.split(raw) if str(p or "").strip()]
    if len(chunks) <= 1:
        return raw
    keep = []
    for p in chunks:
        head = p[:48]
        if any(n in head for n in names):
            keep.append(p)
    return "\n".join(keep) if keep else raw


def resolve_limit(find="", replace="", section="", app_text="") -> dict | None:
    """根据锚点/改写/章节，落到申报书印刷的限字栏位。find 里的小标题优先于 replace 整栏。"""
    _, fields = parse_fields(app_text)
    char_fields = [f for f in fields if f.get("kind") == "chars"]
    if not char_fields:
        return None
    find, replace, section = str(find or ""), str(replace or ""), str(section or "")
    blob_n = re.sub(r"\s+", "", find + replace + section)

    bio = _field_by_title(char_fields, "申报人基本情况")
    if bio:
        hits, share = _heading_from_find(find, app_text, _BIO_PARTS)
        if not hits:
            hits, share = _part_hits(replace, _BIO_PARTS)
        if hits:
            return _limit_from_parts(bio, hits, share)

    ent = _field_by_title(char_fields, "引进企业基本情况")
    if ent:
        hits, share = _heading_from_find(find, app_text, _ENT_PARTS)
        if not hits:
            hits, share = _part_hits(replace, _ENT_PARTS)
        if hits:
            return _limit_from_parts(ent, hits, share)

    best = None
    for f in char_fields:
        t = str(f.get("title") or "")
        if t and t in blob_n:
            if best is None or len(t) > len(str(best.get("title") or "")):
                best = f
    if best is None and "引进企业基本情况" in blob_n:
        best = ent
    if best is None:
        for cand in _SEC_FIELDS.get(section) or ():
            best = _field_by_title(char_fields, cand)
            if best:
                break
    if not best:
        return None
    n = int(best["n"])
    lo, hi = _band(n)
    return {"title": best["title"], "n": n, "lo": lo, "hi": hi, "content": "基本情况" in str(best["title"]), "parts": []}


def limit_hint(section, clause, opinion, app_text: str) -> str:
    lim = resolve_limit(clause, opinion, section, app_text)
    if not lim:
        return ""
    return "【限字】写入「%s」时，replace 去空白后不得超过 %d 字（建议 %d–%d 字）。超过上限必须自行删减，禁止超限。" % (
        lim["title"], lim["n"], lim["lo"], lim["hi"]
    )


def compress_to_chars(text: str, max_n: int, content_only: bool = False) -> str:
    """按申报书口径压缩到不超过 max_n 字；尽量在句号处截断，保留换行与栏目标题。"""
    raw = str(text or "")
    if max_n <= 0 or not raw.strip():
        return raw
    count = _content_chars if content_only else char_count
    if count(raw) <= max_n:
        return raw
    out, used = [], 0
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s:
            if out and out[-1] != "":
                out.append("")
            continue
        skip = s in _SKIP_HEADINGS or _BARE_CHAR.match(s) or _BARE_ITEM.match(s)
        if skip and content_only:
            out.append(s)
            continue
        if used >= max_n:
            break
        c = char_count(s)
        if used + c <= max_n:
            out.append(s)
            used += c
            continue
        chunk = ""
        for sent in re.split(r"(?<=[。！？；;])", s):
            if not sent:
                continue
            sc = char_count(sent)
            if used + char_count(chunk) + sc <= max_n:
                chunk += sent
                continue
            rem = max_n - used - char_count(chunk)
            if rem > 0:
                solid = re.sub(r"\s+", "", sent)
                chunk += solid[:rem]
            used = max_n
            break
        if chunk:
            out.append(chunk)
            used += char_count(chunk)
        if used >= max_n:
            break
    result = "\n".join(out).rstrip()
    if count(result) > max_n:
        solid = re.sub(r"\s+", "", result)
        result = solid[:max_n]
    return result


def _count_for_limit(text: str, lim: dict) -> int:
    if lim.get("content"):
        return _content_chars(text)
    return char_count(text)


def enforce_edit_limits(edits, app_text: str) -> list:
    """超印刷上限的修改意见/修改后当场压缩，返回中文说明。"""
    notes = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        find = e.get("find") or ""
        lim = resolve_limit(
            find,
            e.get("replace") or "",
            e.get("section") or e.get("_sec") or "",
            app_text,
        )
        if not lim:
            continue
        cap = int(lim["n"])
        names = list(lim.get("parts") or [])
        changed = []
        for key, label in _TEXT_KEYS:
            val = str(e.get(key) or "")
            if not val.strip():
                continue
            cropped = crop_to_parts(val, names)
            n0 = _count_for_limit(val, lim)
            n_crop = _count_for_limit(cropped, lim)
            if n_crop <= cap:
                if cropped != val:
                    e[key] = cropped
                    changed.append("%s 整栏拆出「%s」%d→%d 字" % (label, "、".join(names), n0, n_crop))
                continue
            e[key] = compress_to_chars(cropped, cap, content_only=bool(lim.get("content")))
            n1 = _count_for_limit(e[key], lim)
            changed.append("%s %d→%d 字" % (label, n0, n1))
        if changed:
            notes.append(
                "第 %d 条「%s」超过印刷上限 %d 字，已压缩（%s）。"
                % (i, lim["title"], cap, "，".join(changed))
            )
    return notes


def check_replace_limits(edits, app_text: str) -> list:
    """单条 replace 若自身已超过对应栏位上限，提前记遗留。"""
    issues = []
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        rep = str(e.get("replace") or "")
        if not rep.strip():
            continue
        lim = resolve_limit(e.get("find") or "", rep, e.get("section") or e.get("_sec") or "", app_text)
        if not lim:
            continue
        n = _count_for_limit(rep, lim)
        if n > lim["n"]:
            issues.append(
                "第 %d 条编辑「%s」replace 现 %d 字，已超过栏位上限 %d 字，须压缩。"
                % (i, lim["title"], n, lim["n"])
            )
    return issues
