# -*- coding: utf-8 -*-
"""从申报书标注栏（Word 批注 / Excel 批注 / PDF 批注）抽出修改意见。"""
from __future__ import annotations
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NO_OPINION_MSG = "未找到修改意见"
INLINE_OP_NAME = "申报书标注意见.txt"
_MARK = re.compile(r"^<<<标注\s+\d+>>>", re.M)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WORD_EXT = {".docx", ".docm", ".wps"}
EXCEL_EXT = {".xlsx", ".xlsm"}
PDF_EXT = {".pdf"}

# 点在表头上的批注 = 该列每一行都要改
COL_HEADERS = (
    "申报人角色和任务", "项目成果", "简述个人贡献", "角色与贡献",
    "经费总额", "完成人排序", "发表载体", "作者排序",
)
_COL_ALL = re.compile(r"这一栏|本栏|该栏|每一项|各项|全部条目|每一行")


def _local(tag: str) -> str:
    return tag.split("}")[-1] if tag else ""


def _w_id(el) -> str:
    return str(el.get(W + "id") or el.get("id") or "")


def _para_text(pnode) -> str:
    parts = []
    for node in pnode.iter():
        name = _local(node.tag)
        if name == "t":
            parts.append(node.text or "")
        elif name in ("br", "cr"):
            parts.append("\n")
        elif name == "tab":
            parts.append("\t")
    return "".join(parts)


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def is_column_header(text: str) -> str:
    """若文本是已知列表头，返回规范表头，否则空串。"""
    c = _compact(text)
    if not c or len(c) > 24:
        return ""
    for h in COL_HEADERS:
        if c == _compact(h) or c.endswith(_compact(h)):
            return h
    return ""


def is_column_wide_opinion(opinion: str, anchor: str = "") -> bool:
    """批注针对整列（「这一栏」或标注原文就是表头）。"""
    if _COL_ALL.search(str(opinion or "")):
        return True
    return bool(is_column_header(anchor))


def count_project_rows(app_text: str) -> int:
    """工作成果及业绩表的项目条数（用于「这一栏」必须逐项改）。"""
    raw = str(app_text or "")
    start = raw.find("工作成果及业绩")
    if start < 0:
        start = raw.find("四、工作成果")
    if start < 0:
        return 0
    end = -1
    for mark in ("五、代表性", "五、技术专长", "代表性论著", "六、工作计划"):
        end = raw.find(mark, start + 8)
        if end > start:
            break
    chunk = raw[start:end if end > start else min(len(raw), start + 14000)]
    n = 0
    for m in re.finditer(r"(?m)^[ \t]*(\d{1,2})[ \t]*$", chunk):
        nxt = chunk[m.end(): m.end() + 120]
        if re.search(r"20\d{2}", nxt):
            n += 1
    if n >= 2:
        return n
    n2 = len(re.findall(r"(?m)^[ \t]*\d{1,2}[ \t]+\d{4}[.\-/]", chunk))
    return n2 if n2 >= 2 else n


def _cell_text(tc) -> str:
    return " ".join(_para_text(p).strip() for p in tc.iter(W + "p") if _para_text(p).strip())


def _anchor_too_short(s: str) -> bool:
    """点选/末字批注只有「产」「16MPa」时，改用所在段落，避免定位到「产业」。"""
    n = re.sub(r"\s+", "", str(s or ""))
    if not n:
        return True
    if len(n) <= 4:
        return True
    if len(n) <= 8 and re.fullmatch(r"[\d.]+[A-Za-zμµ%]{0,4}", n):
        return True
    return False


def _word_ref_anchors(doc) -> dict[str, dict]:
    """WPS/Word 点批注只有 commentReference、没有 commentRange 时，用所在单元格/段落当锚点。"""
    parent = {c: p for p in doc.iter() for c in list(p)}

    def ancestors(el):
        out = []
        cur = el
        while cur in parent:
            cur = parent[cur]
            out.append(cur)
        return out

    found: dict[str, dict] = {}
    for el in doc.iter():
        if _local(el.tag) != "commentReference":
            continue
        cid = _w_id(el)
        if not cid or cid in found:
            continue
        ancs = ancestors(el)
        pnode = next((a for a in ancs if _local(a.tag) == "p"), None)
        tc = next((a for a in ancs if _local(a.tag) == "tc"), None)
        para = re.sub(r"\s+", " ", _para_text(pnode)).strip() if pnode is not None else ""
        cell = re.sub(r"\s+", " ", _cell_text(tc)).strip() if tc is not None else ""
        header = is_column_header(para) or is_column_header(cell[:40])
        if len(para) >= 6:
            anchor = para
        elif cell:
            anchor = cell
        else:
            anchor = para
        found[cid] = {
            "anchor": anchor[:240],
            "column": header,
            "scope": "column" if header else "",
        }
    return found


def _word_comments(path: Path) -> list[dict]:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "word/comments.xml" not in names:
                return []
            comments_root = ET.fromstring(z.read("word/comments.xml"))
            doc = None
            if "word/document.xml" in names:
                doc = ET.fromstring(z.read("word/document.xml"))
    except Exception:
        return []

    items = []
    by_id = {}
    for c in comments_root:
        if _local(c.tag) != "comment":
            continue
        cid = _w_id(c)
        body = "\n".join(_para_text(p).strip() for p in c.iter(W + "p") if _para_text(p).strip())
        body = re.sub(r"[ \t]+\n", "\n", body).strip()
        if not body:
            continue
        rec = {
            "id": cid,
            "author": str(c.get(W + "author") or "").strip(),
            "text": body,
            "anchor": "",
        }
        by_id[cid] = rec
        items.append(rec)
    if not items or doc is None:
        return items

    active: dict[str, list[str]] = {}
    for el in doc.iter():
        name = _local(el.tag)
        if name == "commentRangeStart":
            cid = _w_id(el)
            if cid in by_id:
                active[cid] = active.get(cid) or []
        elif name == "commentRangeEnd":
            cid = _w_id(el)
            buf = active.pop(cid, None)
            if buf is not None and cid in by_id and not by_id[cid]["anchor"]:
                by_id[cid]["anchor"] = "".join(buf)
        elif name == "t" and el.text and active:
            for buf in active.values():
                buf.append(el.text)
        elif name in ("br", "cr") and active:
            for buf in active.values():
                buf.append("\n")
        elif name == "tab" and active:
            for buf in active.values():
                buf.append("\t")
    for cid, buf in active.items():
        if cid in by_id and not by_id[cid]["anchor"]:
            by_id[cid]["anchor"] = "".join(buf)
    refs = _word_ref_anchors(doc)
    for rec in items:
        rec["anchor"] = re.sub(r"\s+", " ", rec.get("anchor") or "").strip()
        extra = refs.get(str(rec.get("id") or "")) or {}
        para = str(extra.get("anchor") or "").strip()
        if para and (not rec["anchor"] or _anchor_too_short(rec["anchor"])):
            rec["anchor"] = para
        if extra.get("column"):
            rec["column"] = extra["column"]
            rec["scope"] = "column"
        elif is_column_header(rec.get("anchor") or ""):
            rec["column"] = is_column_header(rec.get("anchor") or "")
            rec["scope"] = "column"
    return items


def _excel_comments(path: Path) -> list[dict]:
    path = Path(path)
    try:
        from openpyxl import load_workbook
    except ImportError:
        return []
    try:
        wb = load_workbook(path, data_only=False, read_only=False, keep_vba=False)
    except Exception:
        return []
    items = []
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    cmt = getattr(cell, "comment", None)
                    if cmt is None:
                        continue
                    body = str(getattr(cmt, "text", "") or "").strip()
                    if not body:
                        continue
                    author = str(getattr(cmt, "author", "") or "").strip()
                    if author and body.startswith(author):
                        rest = body[len(author):].lstrip(":\n")
                        if rest:
                            body = rest
                    val = cell.value
                    if val is None:
                        anchor = ""
                    elif isinstance(val, float) and val == int(val):
                        anchor = str(int(val))
                    else:
                        anchor = str(val).strip()
                    items.append({
                        "id": ws.title + "!" + str(cell.coordinate),
                        "author": author,
                        "text": body,
                        "anchor": re.sub(r"\s+", " ", anchor)[:240],
                    })
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return items


def _pdf_comments(path: Path) -> list[dict]:
    """抽出 PDF 批注（高亮/便签）。转换 Word 后批注常丢失，需从原 PDF 读。"""
    path = Path(path)
    try:
        import pymupdf as fitz
    except ImportError:
        return []
    try:
        doc = fitz.open(path)
    except Exception:
        return []
    items = []
    try:
        for pi, page in enumerate(doc, 1):
            annots = page.annots() or []
            for i, annot in enumerate(annots):
                try:
                    info = annot.info or {}
                except Exception:
                    info = {}
                body = str(info.get("content") or info.get("subject") or "").strip()
                if not body:
                    continue
                author = str(info.get("title") or info.get("author") or "").strip()
                if author and body.startswith(author):
                    rest = body[len(author):].lstrip(":\n")
                    if rest:
                        body = rest
                anchor = ""
                try:
                    rect = annot.rect
                    clip = fitz.Rect(rect.x0 - 8, rect.y0 - 18, rect.x1 + 80, rect.y1 + 18)
                    nearby = page.get_text("text", clip=clip) or ""
                    anchor = re.sub(r"\s+", " ", nearby).strip()[:240]
                except Exception:
                    anchor = ""
                rec = {
                    "id": "p" + str(pi) + "-" + str(i),
                    "author": author,
                    "text": body,
                    "anchor": anchor,
                }
                header = is_column_header(anchor) or is_column_header(body)
                if header:
                    rec["column"] = header
                    rec["scope"] = "column"
                items.append(rec)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return items


def extract_comment_items(path: Path) -> list[dict]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in WORD_EXT:
        return _word_comments(path)
    if ext in EXCEL_EXT:
        return _excel_comments(path)
    if ext in PDF_EXT:
        return _pdf_comments(path)
    return []


def merge_comment_items(*groups: list) -> list[dict]:
    """合并 PDF/Word 批注，按正文去重（保留锚点更完整的一条）。"""
    out: list[dict] = []
    seen: dict[str, int] = {}
    for group in groups:
        for rec in group or []:
            body = _compact(rec.get("text") or "")
            if not body:
                continue
            if body in seen:
                i = seen[body]
                if len(str(rec.get("anchor") or "")) > len(str(out[i].get("anchor") or "")):
                    out[i] = rec
                continue
            seen[body] = len(out)
            out.append(rec)
    return out


def format_inline_opinions(items: list[dict]) -> str:
    lines = []
    n = 0
    for rec in items or []:
        body = str(rec.get("text") or "").strip()
        if not body:
            continue
        n += 1
        lines.append("<<<标注 " + str(n) + ">>>")
        lines.append(str(n) + ". " + body)
        anchor = str(rec.get("anchor") or "").strip()
        if anchor:
            lines.append("标注原文：" + anchor)
        col = str(rec.get("column") or "") or is_column_header(anchor)
        if rec.get("scope") == "column" or col:
            lines.append("适用范围：本栏（" + (col or "该列") + "）全部条目，须逐项各改一条")
        lines.append("")
    return "\n".join(lines).strip()


def extract_inline_opinion_text(path: Path) -> tuple[str, int]:
    items = extract_comment_items(path)
    text = format_inline_opinions(items)
    n = len(re.findall(r"^<<<标注\s+\d+>>>", text, re.M)) if text else 0
    return text, n


def split_inline_units(text: str) -> list[str]:
    s = str(text or "").strip()
    if not s or not _MARK.search(s):
        return []
    parts = _MARK.split(s)
    out = []
    for p in parts:
        t = p.strip()
        if t:
            out.append(t)
    return out
