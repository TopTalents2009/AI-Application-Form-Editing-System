# -*- coding: utf-8 -*-
"""将 docreconstruct 中间产物适配为带【第N页】标记的申报书 OCR 文本。"""
from __future__ import annotations

import json
import re
from pathlib import Path

_PAGE_MARK = re.compile(r"^<!--\s*page:\s*(\d+)\s*-->\s*$", re.I)
_PAGE_MARK_CN = re.compile(r"^【第(\d+)页】\s*$")
_PAGE_FOOTER = re.compile(r"^(\d+)\s*/\s*(\d+)\s*$")


def _cjk_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", str(text or "")))


def split_text_by_page_footers(text: str, page_count: int) -> dict[int, str]:
    """按申报书页脚 `N/total`（如 2/30）切分 OCR 文本。"""
    if page_count <= 0:
        return {}
    pages: dict[int, str] = {}
    buf: list[str] = []
    for ln in str(text or "").splitlines():
        m = _PAGE_FOOTER.match(ln.strip())
        if m:
            pg, total = int(m.group(1)), int(m.group(2))
            if total == page_count:
                body = "\n".join(buf).strip()
                if body and 1 <= pg <= page_count:
                    pages[pg] = body
                buf = []
                continue
        buf.append(ln)
    if buf:
        body = "\n".join(buf).strip()
        if body:
            last = max(pages) if pages else 0
            target = min(page_count, last + 1) if last else 1
            if target not in pages or len(body) > len(pages.get(target, "")):
                pages[target] = body
    return {pg: pages.get(pg, "") for pg in range(1, page_count + 1)}


def fill_empty_pages_from_bulk(page_texts: dict[int, str], page_count: int) -> dict[int, str]:
    """第 1 页保留完整 OCR（供字段解析）；仅向空页填入按页脚拆分的内容。"""
    if page_count <= 1:
        return page_texts
    out = {int(k): str(v or "") for k, v in page_texts.items()}
    bulk = str(out.get(1) or "").strip()
    if len(bulk) < 1500:
        return out
    split = split_text_by_page_footers(bulk, page_count)
    if sum(1 for v in split.values() if str(v).strip()) < 3:
        return out
    for pg in range(2, page_count + 1):
        body = str(split.get(pg) or "").strip()
        if not body:
            continue
        existing = str(out.get(pg) or "").strip()
        if not existing or len(body) > len(existing):
            out[pg] = body
    return out


def prefer_page_text(existing: str, new: str, *, min_gain: int = 48) -> str:
    """合并两路 OCR：避免 Gemini 短结果覆盖 docreconstruct 长结果。"""
    a, b = str(existing or "").strip(), str(new or "").strip()
    if not b:
        return a
    if not a:
        return b
    if len(b) >= len(a) + min_gain:
        return b
    if len(a) >= len(b) + min_gain:
        return a
    if _cjk_count(b) >= _cjk_count(a) + 8:
        return b
    return a


def extract_page_texts_from_markdown(md_path: Path, page_count: int) -> dict[int, str]:
    """按 `<!-- page: N -->` 或页脚 `N/total` 切分 docreconstruct Markdown。"""
    raw = Path(md_path).read_text(encoding="utf-8", errors="replace")
    pages: dict[int, list[str]] = {}
    cur = 1
    for ln in raw.splitlines():
        m = _PAGE_MARK.match(ln.strip())
        if m:
            cur = int(m.group(1))
            pages.setdefault(cur, [])
            continue
        pages.setdefault(cur, []).append(ln)
    out = {pg: "\n".join(lines).strip() for pg, lines in pages.items() if lines or pg in pages}
    if page_count > 0:
        for pg in range(1, page_count + 1):
            out.setdefault(pg, "")
    if page_count > 1 and not any(str(out.get(pg) or "").strip() for pg in range(2, page_count + 1)):
        footer_split = split_text_by_page_footers(raw, page_count)
        if sum(1 for v in footer_split.values() if str(v).strip()) >= 3:
            out[1] = raw.strip() if raw.strip() else str(out.get(1) or "")
            for pg in range(2, page_count + 1):
                if str(footer_split.get(pg) or "").strip():
                    out[pg] = footer_split[pg]
    return fill_empty_pages_from_bulk(out, page_count)


def extract_page_texts_from_docx(docx_path: Path, page_count: int) -> dict[int, str]:
    """从 docx 段落/表格提取文本；按分页符或 section 粗分页。"""
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(docx_path))
    pages: dict[int, list[str]] = {1: []}
    cur = 1

    def _append_line(text: str) -> None:
        s = str(text or "").strip()
        if s:
            pages.setdefault(cur, []).append(s)

    for p in doc.paragraphs:
        t = str(p.text or "")
        m = _PAGE_MARK_CN.match(t.strip())
        if m:
            cur = int(m.group(1))
            pages.setdefault(cur, [])
            continue
        xml = p._p.xml
        if "w:br" in xml and 'w:type="page"' in xml:
            cur += 1
            pages.setdefault(cur, [])
            continue
        _append_line(t)

    for table in doc.tables:
        for row in table.rows:
            line = "\t".join(str(c.text or "").strip() for c in row.cells if str(c.text or "").strip())
            if line:
                _append_line(line)

    if len(doc.sections) > 1 and page_count > 1 and len(pages) == 1:
        # section 数量接近页数时，仍可能全落在第 1 页；留给 markdown/evidence 优先
        pass

    out = {pg: "\n".join(lines).strip() for pg, lines in pages.items()}
    if page_count > 0:
        for pg in range(1, page_count + 1):
            out.setdefault(pg, "")
    return out


def extract_page_layout_from_evidence(evidence_dir: Path) -> dict[int, dict]:
    """从 evidence JSON 抽出每页尺寸与带 bbox 的 OCR 行，供版式 Word 定位。"""
    root = Path(evidence_dir)
    out: dict[int, dict] = {}
    if not root.is_dir():
        return out
    for fp in sorted(root.rglob("*.json")):
        try:
            obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        try:
            dpi = float(meta.get("pdf_dpi") or meta.get("dpi") or 144)
        except (TypeError, ValueError):
            dpi = 144.0
        pages = obj.get("pages")
        if not isinstance(pages, list):
            continue
        for pg in pages:
            if not isinstance(pg, dict):
                continue
            try:
                num = int(pg.get("number") or pg.get("page_number") or pg.get("page") or 0)
            except (TypeError, ValueError):
                num = 0
            if num <= 0:
                continue
            try:
                width = float(pg.get("width") or 0)
                height = float(pg.get("height") or 0)
            except (TypeError, ValueError):
                width, height = 0.0, 0.0
            elements = []
            for el in pg.get("elements") or []:
                if not isinstance(el, dict):
                    continue
                text = str(el.get("text") or "").strip()
                if not text:
                    continue
                bbox = el.get("bbox") if isinstance(el.get("bbox"), dict) else {}
                try:
                    x0, y0 = float(bbox.get("x0") or 0), float(bbox.get("y0") or 0)
                    x1, y1 = float(bbox.get("x1") or 0), float(bbox.get("y1") or 0)
                except (TypeError, ValueError):
                    continue
                if x1 <= x0 or y1 <= y0:
                    continue
                elements.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
            rec = out.setdefault(num, {"width": width, "height": height, "dpi": dpi, "elements": []})
            if width > 0:
                rec["width"] = width
            if height > 0:
                rec["height"] = height
            rec["dpi"] = dpi
            rec["elements"].extend(elements)
    return out


def extract_page_texts_from_evidence(evidence_dir: Path, page_count: int) -> dict[int, str]:
    """从 evidence/*.json 聚合每页 OCR 文本（按 page_index / page_number）。"""
    root = Path(evidence_dir)
    if not root.is_dir():
        return {}
    pages: dict[int, list[str]] = {}
    for fp in sorted(root.rglob("*.json")):
        try:
            obj = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        _collect_evidence_text(obj, pages)
    out = {pg: "\n".join(parts).strip() for pg, parts in pages.items()}
    if page_count > 0:
        for pg in range(1, page_count + 1):
            out.setdefault(pg, "")
    return out


def _collect_evidence_text(node, pages: dict[int, list[str]]) -> None:
    if isinstance(node, dict):
        text = str(node.get("text") or node.get("content") or node.get("rec_text") or "").strip()
        page = node.get("page_index", node.get("page_number", node.get("page")))
        if text and page is not None:
            try:
                pg = int(page)
                if pg >= 0:
                    pages.setdefault(pg if pg > 0 else 1, []).append(text)
            except (TypeError, ValueError):
                pass
        for v in node.values():
            _collect_evidence_text(v, pages)
    elif isinstance(node, list):
        for item in node:
            _collect_evidence_text(item, pages)


def merge_page_maps(*maps: dict[int, str], page_count: int) -> dict[int, str]:
    out: dict[int, str] = {}
    for pg in range(1, max(page_count, 1) + 1):
        for m in maps:
            val = str(m.get(pg) or "").strip()
            if val and (pg not in out or len(val) > len(out[pg])):
                out[pg] = val
        out.setdefault(pg, "")
    return out


def page_needs_gemini(page_no: int, text: str, cfg: dict, total_pages: int) -> bool:
    """判断该页是否需 Gemini 补扫。"""
    body = str(text or "").strip()
    min_chars = int(cfg.get("pageMinChars") or 80)
    if not body:
        return True
    if len(body) < min_chars:
        return True
    if len(body) < 24 and _cjk_count(body) < 4:
        return True
    keywords = [str(x).strip() for x in (cfg.get("criticalKeywords") or []) if str(x).strip()]
    if page_no <= min(6, total_pages) and keywords:
        hits = sum(1 for kw in keywords if kw in body)
        if hits == 0 and len(body) < min_chars * 2:
            return True
    return False


def build_paged_work_text(page_texts: dict[int, str]) -> str:
    chunks: list[str] = []
    for pg in sorted(page_texts):
        chunks.append("【第" + str(pg) + "页】")
        body = str(page_texts.get(pg) or "").strip()
        chunks.append(body if body else "（本页未识别到文字）")
    return "\n".join(chunks)
