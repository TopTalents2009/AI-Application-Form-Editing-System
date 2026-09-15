"""申报书 PDF 处理。数字版转 Word；扫描件仅 OCR 为文本（落盘时套 QM/HJ 模板，不先转 Word）。"""
from __future__ import annotations
import asyncio, os, re, shutil, sys, zipfile
from pathlib import Path

from .config import PYEXE, scan_pdf_settings

WORD_APP_EXT = {".docx", ".docm", ".wps"}
EXCEL_APP_EXT = {".xlsx", ".xlsm", ".xls"}
ALLOWED_APP_EXT = WORD_APP_EXT | EXCEL_APP_EXT | {".pdf"}
APP_EXT_HINT = ".docx / .docm / Excel（.xlsx .xlsm .xls）或 .pdf（数字版或扫描件）"
SCAN_MSG = (
    "该 PDF 没有足够的可复制文字，像扫描件。"
    "请改传 Word / Excel，或确保已在模型配置中填写 Gemini 密钥以启用扫描件识别。"
)
CONVERT_TIMEOUT_S = 240
SCAN_OCR_TIMEOUT_S = 1800
SCAN_OCR_PAGE_CONCURRENCY = 2
SCAN_OCR_DPI = 132
MAX_SCAN_PDF_PAGES = 120
MIN_CJK = 800

PYENV = dict(os.environ, PYTHONIOENCODING="utf-8")
ROOT = Path(__file__).resolve().parent.parent


def ext_of(name: str) -> str:
    s = str(name or "")
    i = s.rfind(".")
    return s[i:].lower() if i >= 0 else ""


def is_app_ext(name: str) -> bool:
    return ext_of(name) in ALLOWED_APP_EXT


def work_docx_name(app_name: str) -> str:
    """落盘用的源文件名：PDF 换成同名 .docx，其余保持原扩展名。"""
    n = str(app_name or "")
    if ext_of(n) == ".pdf":
        return n[: -len(".pdf")] + ".docx"
    return n


def edited_name(stem: str, src_ext: str) -> str:
    ext = str(src_ext or ".docx").lower()
    if ext not in WORD_APP_EXT | EXCEL_APP_EXT:
        ext = ".docx"
    return str(stem or "申报书") + "_修改后" + ext


def backup_name(stem: str, src_ext: str) -> str:
    ext = str(src_ext or ".docx").lower()
    if ext not in WORD_APP_EXT | EXCEL_APP_EXT:
        ext = ".docx"
    return str(stem or "申报书") + "_备份" + ext


def is_edited_output(name: str) -> bool:
    n = str(name or "").lower()
    return any(n.endswith("_修改后" + e) for e in (WORD_APP_EXT | EXCEL_APP_EXT))


def is_backup_output(name: str) -> bool:
    n = str(name or "").lower()
    return any(n.endswith("_备份" + e) for e in (WORD_APP_EXT | EXCEL_APP_EXT))


def sniff_pdf(data: bytes, name: str = "") -> None:
    head = (data or b"")[:8].lstrip()
    if not head.startswith(b"%PDF"):
        raise ValueError("不是有效的 PDF 文件" + (("：" + name) if name else ""))


def _cjk_count(s: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", str(s or "")))


def _open_pdf(path: Path):
    try:
        import pymupdf as fitz
    except ImportError as e:
        raise ValueError("服务器未安装 PyMuPDF，无法处理 PDF") from e
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise ValueError("无法打开 PDF：" + str(e)[:180]) from e
    if getattr(doc, "is_encrypted", False):
        unlocked = False
        try:
            unlocked = bool(doc.authenticate(""))
        except Exception:
            unlocked = False
        if not unlocked:
            doc.close()
            raise ValueError("PDF 已加密，无法读取")
    if (doc.page_count or 0) <= 0:
        doc.close()
        raise ValueError("PDF 没有页面")
    return doc


def _pdf_text_stats(path: Path) -> tuple[str, int, int, int]:
    """返回 (全文, 总CJK数, 疑似扫描页数, 页数)。"""
    doc = _open_pdf(path)
    try:
        n = doc.page_count or 0
        parts = []
        scan_pages = 0
        for page in doc:
            text = page.get_text("text") or ""
            parts.append(text)
            cjk = _cjk_count(text)
            images = page.get_images() or []
            if images and cjk < 120:
                scan_pages += 1
        full = "\n".join(parts)
        return full, _cjk_count(full), scan_pages, n
    finally:
        doc.close()


def is_scanned_pdf(path: Path) -> bool:
    """无足够可复制文字层时视为扫描件。"""
    _, cjk_all, scan_pages, n = _pdf_text_stats(path)
    if cjk_all < MIN_CJK:
        return True
    if n >= 2 and scan_pages / n >= 0.7 and cjk_all < 4000:
        return True
    return False


def pdf_kind(path: Path) -> str:
    """返回 'digital' 或 'scanned'。"""
    return "scanned" if is_scanned_pdf(path) else "digital"


def require_digital_pdf(path: Path) -> str:
    """有足够文字层才视为数字版。返回抽出的正文（供日志）。"""
    full, cjk_all, scan_pages, n = _pdf_text_stats(path)
    if cjk_all < MIN_CJK:
        raise ValueError(SCAN_MSG)
    if n >= 2 and scan_pages / n >= 0.7 and cjk_all < 4000:
        raise ValueError(SCAN_MSG)
    return full


def pdf_page_sizes_pt(path: Path) -> dict[int, tuple[float, float]]:
    """每页物理尺寸（point）。"""
    doc = _open_pdf(path)
    try:
        out: dict[int, tuple[float, float]] = {}
        for i, page in enumerate(doc):
            rect = page.rect
            out[i + 1] = (float(rect.width), float(rect.height))
        return out
    finally:
        doc.close()


def rasterize_pdf_pages(path: Path, dpi: int = SCAN_OCR_DPI) -> list[tuple[int, bytes, str, bytes]]:
    """将 PDF 每页渲染为图片。返回 [(页码, OCR用图, mime, 嵌入用PNG), ...]。"""
    from .opinion_extract import _prepare_image_bytes

    doc = _open_pdf(path)
    pages: list[tuple[int, bytes, str, bytes]] = []
    try:
        n = doc.page_count or 0
        if n > MAX_SCAN_PDF_PAGES:
            raise ValueError(
                "扫描 PDF 共 " + str(n) + " 页，超过上限 " + str(MAX_SCAN_PDF_PAGES)
                + " 页。请拆分后上传或改传 Word / Excel。"
            )
        scale = float(dpi) / 72.0
        mat = None
        try:
            import pymupdf as fitz
            mat = fitz.Matrix(scale, scale)
        except Exception:
            mat = None
        for i, page in enumerate(doc):
            if mat is not None:
                pix = page.get_pixmap(matrix=mat, alpha=False)
            else:
                pix = page.get_pixmap(dpi=dpi, alpha=False)
            embed = pix.tobytes("png")
            data, mime = _prepare_image_bytes(embed, "image/png")
            pages.append((i + 1, data, mime, embed))
    finally:
        doc.close()
    return pages


_PAGE_MARK = re.compile(r"^【第(\d+)页】\s*$")


def _usable_page_width(doc):
    from docx.shared import Emu

    sec = doc.sections[-1]
    return int(sec.page_width - sec.left_margin - sec.right_margin)


def _add_page_image(doc, png_bytes: bytes) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from io import BytesIO

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(BytesIO(png_bytes), width=_usable_page_width(doc))


def _add_table_rows(doc, rows: list[list[str]]) -> None:
    if not rows:
        return
    cols = max(len(r) for r in rows)
    if cols <= 0:
        return
    table = doc.add_table(rows=len(rows), cols=cols)
    table.style = "Table Grid"
    for ri, row in enumerate(rows):
        cells = row + [""] * (cols - len(row))
        for ci, val in enumerate(cells[:cols]):
            table.rows[ri].cells[ci].text = str(val or "")


def _append_text_blocks(doc, lines: list[str]) -> None:
    i = 0
    n = len(lines)
    while i < n:
        line = str(lines[i] or "")
        if "\t" in line:
            rows = []
            while i < n and "\t" in str(lines[i] or ""):
                rows.append([c.strip() for c in str(lines[i]).split("\t")])
                i += 1
            _add_table_rows(doc, rows)
            continue
        if line.strip():
            doc.add_paragraph(line)
        i += 1


def _deocr_work_text(text: str) -> str:
    from .scan_text_normalize import deocr_scanned_text

    return deocr_scanned_text(text)


def _docx_is_form_like(path: Path) -> bool:
    """docreconstruct 版式稿是否像申报书表格（多表且多行），否则宁可用清洗后的 OCR 文字稿。"""
    try:
        from docx import Document

        doc = Document(str(path))
        tables = list(doc.tables)
        if len(tables) < 8:
            return False
        multi = sum(1 for t in tables if len(t.rows) >= 3)
        return multi >= 6
    except Exception:
        return False


def _collapse_cjk_in_docx(path: Path) -> None:
    """去掉 Tesseract 插在汉字之间的空格，保留表格结构。"""
    from docx import Document
    from docx.oxml.ns import qn
    from .scan_text_normalize import collapse_cjk_spaces

    doc = Document(str(path))
    changed = False
    for t_el in doc.element.body.iter(qn("w:t")):
        old = t_el.text or ""
        new = collapse_cjk_spaces(old)
        if new != old:
            t_el.text = new
            changed = True
    if changed:
        doc.save(str(path))


def _write_scanned_work_docx(text: str, dst: Path, layout_docx: Path | None = None) -> str:
    """写出扫描件工作稿：优先可用的版式 Word，否则清洗后的 OCR 文字稿。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if layout_docx and Path(layout_docx).is_file() and _is_docx(layout_docx) and _docx_is_form_like(layout_docx):
        shutil.copyfile(layout_docx, dst)
        _collapse_cjk_in_docx(dst)
        return "layout"
    build_docx_from_text(_deocr_work_text(text), dst)
    return "text"


def _try_layout_preserving_docx(
    dst: Path,
    src_pdf: Path,
    pages: list[tuple[int, bytes, str, bytes]],
    evidence_dir: Path | None,
    log_fn=None,
) -> dict | None:
    """按原 PDF 页图 + OCR 定位框生成 Word。失败返回 None。"""
    from .docreconstruct_adapt import extract_page_layout_from_evidence
    from .layout_docx import build_layout_preserving_docx

    pngs = [(pg, embed) for pg, _d, _m, embed in pages if embed]
    if not pngs:
        return None
    try:
        sizes = pdf_page_sizes_pt(src_pdf)
    except Exception:
        sizes = {}
    layout_pages = extract_page_layout_from_evidence(evidence_dir) if evidence_dir else {}
    try:
        info = build_layout_preserving_docx(dst, pngs, sizes, layout_pages)
    except Exception as e:
        if log_fn:
            log_fn("原版式 Word 生成失败：" + str(e)[:160])
        if dst.exists():
            try:
                dst.unlink()
            except Exception:
                pass
        return None
    if not info.get("ok") or not dst.exists() or not _is_docx(dst):
        return None
    if log_fn:
        log_fn(
            "工作稿已按原 PDF 页版式生成（"
            + str(info.get("pages") or 0)
            + " 页图，"
            + str(info.get("textboxes") or 0)
            + " 个定位文本框）"
        )
    return info


def build_docx_from_text(text: str, dst: Path) -> None:
    """把 OCR 全文写入最小可用 docx（每行一段，供后续提取与 find/replace）。"""
    from docx import Document

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    doc = Document()
    _append_text_blocks(doc, str(text or "").splitlines())
    if not doc.paragraphs and not doc.tables:
        doc.add_paragraph("")
    doc.save(str(dst))


def build_scanned_docx_from_pages(page_items: list[tuple[int, bytes, str]], dst: Path) -> None:
    """扫描件版式 docx：每页嵌入原 PDF 截图 + 下方结构化 OCR 文本（表格按制表符还原）。"""
    from docx import Document

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    doc = Document()
    items = sorted(page_items, key=lambda x: x[0])
    for idx, (pg, png_bytes, ocr_text) in enumerate(items):
        doc.add_paragraph("【第" + str(pg) + "页】")
        _add_page_image(doc, png_bytes)
        body = str(ocr_text or "").strip()
        if body:
            _append_text_blocks(doc, body.splitlines())
        else:
            doc.add_paragraph("（本页未识别到文字）")
        if idx < len(items) - 1:
            doc.add_page_break()
    doc.save(str(dst))


def read_docx_page_lines(docx_path: Path) -> dict[int, list[str]]:
    """按【第N页】标记拆分 docx 段落文本。"""
    from docx import Document

    doc = Document(str(docx_path))
    pages: dict[int, list[str]] = {}
    cur = 1
    for p in doc.paragraphs:
        t = str(p.text or "")
        m = _PAGE_MARK.match(t.strip())
        if m:
            cur = int(m.group(1))
            pages.setdefault(cur, [])
            continue
        pages.setdefault(cur, []).append(t)
    for table in doc.tables:
        for row in table.rows:
            pages.setdefault(cur, []).append("\t".join(str(c.text or "").strip() for c in row.cells))
    return pages


def finalize_scanned_docx(pdf_path: Path, edited_docx: Path, dst: Path) -> None:
    """落盘后把扫描件输出重排为「每页原图 + 修改后文字层」，保留申报书版式观感。"""
    page_images = {pg: png for pg, _a, _b, png in rasterize_pdf_pages(pdf_path)}
    text_by_page = read_docx_page_lines(edited_docx)
    items = []
    for pg in sorted(page_images.keys()):
        lines = list(text_by_page.get(pg) or [])
        if not lines and pg in text_by_page:
            lines = []
        items.append((pg, page_images[pg], "\n".join(lines)))
    if not items:
        raise ValueError("扫描件版式重排失败：未读取到页面内容")
    build_scanned_docx_from_pages(items, dst)


async def _ocr_pages_gemini(
    src: Path,
    pages: list[tuple[int, bytes, str, bytes]],
    page_filter: set[int] | None = None,
    log_fn=None,
    should_continue=None,
) -> dict[int, str]:
    """逐页 Gemini OCR，返回 {页码: 文本}。"""
    from .opinion_extract import APP_PDF_OCR_PROMPT, ocr_image_bytes

    alive = should_continue or (lambda: True)
    targets = [
        (pg, data, mime)
        for pg, data, mime, _embed in pages
        if page_filter is None or pg in page_filter
    ]
    if not targets:
        return {}
    sem = asyncio.Semaphore(SCAN_OCR_PAGE_CONCURRENCY)
    done = 0
    total = len(targets)

    async def _one(pg: int, data: bytes, mime: str) -> tuple[int, str]:
        nonlocal done
        if not alive():
            raise asyncio.CancelledError()
        async with sem:
            if not alive():
                raise asyncio.CancelledError()
            text = await ocr_image_bytes(
                data, mime, src.name + " 第" + str(pg) + "页",
                prompt=APP_PDF_OCR_PROMPT,
            )
            if not alive():
                raise asyncio.CancelledError()
            done += 1
            if log_fn:
                log_fn("Gemini 补扫 " + str(done) + "/" + str(total) + " 页")
            return pg, str(text or "").strip()

    tasks = [asyncio.create_task(_one(pg, data, mime)) for pg, data, mime in targets]
    try:
        results = await asyncio.gather(*tasks)
    except Exception:
        for tk in tasks:
            if not tk.done():
                tk.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return {pg: text for pg, text in results}


def _write_ocr_report(dst: Path, payload: dict) -> None:
    try:
        from .docreconstruct_bridge import write_report

        write_report(dst.with_suffix(".ocr-report.json"), payload)
    except Exception:
        pass


async def _run_scanned_ocr(
    src: Path,
    log_fn=None,
    should_continue=None,
    *,
    gemini_fallback: bool = True,
    force_gemini: bool = False,
) -> dict:
    """扫描 PDF 混合 OCR，返回页文本与元数据（不写 Word）。"""
    import asyncio as _asyncio
    from .docreconstruct_adapt import fill_empty_pages_from_bulk, page_needs_gemini, prefer_page_text
    from .docreconstruct_bridge import availability_status, is_available, run_convert

    src = Path(src)
    cfg = scan_pdf_settings()
    pages = rasterize_pdf_pages(src)
    if not pages:
        raise ValueError("PDF 没有可识别的页面")
    total = len(pages)

    if force_gemini or not is_available():
        if log_fn and not force_gemini:
            log_fn("docreconstruct 不可用，回退 Gemini OCR：" + availability_status())
        page_texts = await _ocr_pages_gemini(src, pages, log_fn=log_fn, should_continue=should_continue)
        return {
            "page_texts": page_texts,
            "pages": pages,
            "total": total,
            "engine": "ocr-gemini",
            "need_gemini": sorted(page_texts),
            "dr_result": None,
            "cfg": cfg,
        }

    work_dir = src.parent / "tmp" / "docreconstruct"
    dr_docx = work_dir / (src.stem + ".dr.docx")
    if log_fn:
        log_fn("扫描 PDF：docreconstruct convert 开始（" + str(cfg.get("languages") or "chi_sim+eng") + "）")
    dr_result = await _asyncio.to_thread(run_convert, src, dr_docx, work_dir, cfg, total)

    page_texts: dict[int, str] = {}
    engine = "ocr-hybrid"
    if dr_result.ok and dr_result.page_texts:
        page_texts = {int(k): str(v or "").strip() for k, v in dr_result.page_texts.items()}
        page_texts = fill_empty_pages_from_bulk(page_texts, total)
        if log_fn:
            log_fn(
                "docreconstruct 完成，QA="
                + f"{dr_result.qa_score * 100:.1f}%"
                + (("，引擎 " + ", ".join(dr_result.providers)) if dr_result.providers else "")
            )
    else:
        if log_fn:
            log_fn("docreconstruct 失败，将全部使用 Gemini：" + str(dr_result.error or "")[:160])
        engine = "ocr-hybrid+gemini-full"

    need_gemini: set[int] = set()
    if gemini_fallback:
        if engine == "ocr-hybrid+gemini-full":
            need_gemini = {pg for pg, _d, _m, _e in pages}
        else:
            for pg, _d, _m, _e in pages:
                if pg == 1:
                    continue
                if page_needs_gemini(pg, page_texts.get(pg, ""), cfg, total):
                    need_gemini.add(pg)
            if int(cfg.get("geminiMaxPages") or 12) < len(need_gemini):
                ordered = sorted(need_gemini)
                need_gemini = set(ordered[: int(cfg.get("geminiMaxPages") or 12)])
    elif not page_texts:
        if log_fn:
            log_fn("docreconstruct 未产出有效页文本，回退全量 Gemini OCR")
        return await _run_scanned_ocr(
            src, log_fn=log_fn, should_continue=should_continue, force_gemini=True,
        )

    if need_gemini:
        if log_fn:
            log_fn("Gemini 补扫页码：" + ", ".join(str(x) for x in sorted(need_gemini)))
        gemini_texts = await _ocr_pages_gemini(
            src, pages, page_filter=need_gemini, log_fn=log_fn, should_continue=should_continue,
        )
        for pg, text in gemini_texts.items():
            page_texts[pg] = prefer_page_text(page_texts.get(pg, ""), text)
        if gemini_texts and engine == "ocr-hybrid":
            engine = "ocr-hybrid+gemini"

    return {
        "page_texts": page_texts,
        "pages": pages,
        "total": total,
        "engine": engine,
        "need_gemini": sorted(need_gemini),
        "dr_result": dr_result,
        "cfg": cfg,
    }


def _finalize_scanned_ocr_text(ocr: dict) -> str:
    full = _build_paged_text_from_map(ocr["page_texts"], ocr["total"])
    full = _deocr_work_text(full)
    if _cjk_count(full) < 200:
        raise ValueError("扫描 PDF 识别结果过短，可能不是申报书或图片不清晰")
    return full


async def ocr_scanned_pdf_to_text(
    src: Path,
    dst_txt: Path,
    log_fn=None,
    should_continue=None,
) -> str:
    """扫描 PDF：OCR 识别为纯文本，不生成 Word 工作稿。"""
    src, dst_txt = Path(src), Path(dst_txt)
    dst_txt.parent.mkdir(parents=True, exist_ok=True)
    ocr = await _run_scanned_ocr(src, log_fn=log_fn, should_continue=should_continue)
    try:
        full = _finalize_scanned_ocr_text(ocr)
    except ValueError:
        if log_fn:
            log_fn("混合 OCR 结果过短，回退全量 Gemini OCR")
        ocr = await _run_scanned_ocr(
            src, log_fn=log_fn, should_continue=should_continue, force_gemini=True,
        )
        full = _finalize_scanned_ocr_text(ocr)
    dst_txt.write_text(full, encoding="utf-8")
    dr_result = ocr.get("dr_result")
    cfg = ocr.get("cfg") or scan_pdf_settings()
    report = {
        "engine": ocr.get("engine"),
        "workKind": "text-only",
        "outputMode": "template",
        "docreconstructOk": bool(dr_result and dr_result.ok),
        "docreconstructQa": getattr(dr_result, "qa_score", 0) if dr_result else 0,
        "docreconstructProviders": getattr(dr_result, "providers", []) if dr_result else [],
        "docreconstructError": getattr(dr_result, "error", "") if dr_result else "",
        "geminiPages": ocr.get("need_gemini") or [],
        "pageChars": {
            str(pg): len(str((ocr.get("page_texts") or {}).get(pg) or ""))
            for pg in range(1, int(ocr.get("total") or 0) + 1)
        },
    }
    if cfg.get("keepIntermediates") and dr_result and getattr(dr_result, "work_dir", None):
        report["workDir"] = str(dr_result.work_dir)
    _write_ocr_report(dst_txt, report)
    return str(ocr.get("engine") or "ocr-text")


async def ocr_scanned_pdf_to_docx(
    src: Path,
    dst: Path,
    log_fn=None,
    should_continue=None,
) -> str:
    """扫描 PDF：逐页 Gemini OCR → 合成 docx。返回引擎标识。"""
    src, dst = Path(src), Path(dst)
    ocr = await _run_scanned_ocr(
        src, log_fn=log_fn, should_continue=should_continue, force_gemini=True,
    )
    full = _finalize_scanned_ocr_text(ocr)
    layout_info = _try_layout_preserving_docx(dst, src, ocr["pages"], None, log_fn=log_fn)
    if not (layout_info and int(layout_info.get("textboxes") or 0) > 0):
        _write_scanned_work_docx(full, dst)
    if not dst.exists() or not _is_docx(dst):
        raise ValueError("扫描 PDF 识别后未得到有效 .docx")
    _write_ocr_report(dst, {"engine": "ocr-gemini", "geminiPages": sorted(ocr.get("page_texts") or {})})
    return "ocr-gemini"


def _build_paged_text_from_map(page_texts: dict[int, str], total_pages: int) -> str:
    from .docreconstruct_adapt import build_paged_work_text

    merged = {pg: str(page_texts.get(pg) or "").strip() for pg in range(1, total_pages + 1)}
    return build_paged_work_text(merged)


async def hybrid_scanned_pdf_to_docx(
    src: Path,
    dst: Path,
    log_fn=None,
    should_continue=None,
    gemini_fallback: bool = True,
) -> str:
    """扫描 PDF：docreconstruct 主路径 + 可选 Gemini 按页补扫 → Word（测试/兼容用）。"""
    from .docreconstruct_bridge import write_report

    src, dst = Path(src), Path(dst)
    try:
        ocr = await _run_scanned_ocr(
            src, log_fn=log_fn, should_continue=should_continue, gemini_fallback=gemini_fallback,
        )
        full = _finalize_scanned_ocr_text(ocr)
    except ValueError:
        if log_fn:
            log_fn("混合 OCR 结果过短，回退全量 Gemini OCR")
        return await ocr_scanned_pdf_to_docx(src, dst, log_fn=log_fn, should_continue=should_continue)

    pages = ocr["pages"]
    dr_result = ocr.get("dr_result")
    cfg = ocr.get("cfg") or scan_pdf_settings()
    engine = str(ocr.get("engine") or "ocr-hybrid")
    evidence_dir = dr_result.evidence_dir if dr_result and (dr_result.ok or dr_result.evidence_dir) else None
    layout_info = _try_layout_preserving_docx(dst, src, pages, evidence_dir, log_fn=log_fn)
    if layout_info and int(layout_info.get("textboxes") or 0) > 0:
        work_kind = "layout"
    else:
        dr_docx = (src.parent / "tmp" / "docreconstruct" / (src.stem + ".dr.docx"))
        layout_src = dr_result.docx_path if (dr_result and dr_result.ok and dr_result.docx_path) else dr_docx
        work_kind = _write_scanned_work_docx(full, dst, layout_docx=layout_src)
        if work_kind == "layout" and log_fn:
            log_fn("工作稿沿用 docreconstruct 版式 Word（保留表格结构）")
    if not dst.exists() or not _is_docx(dst):
        raise ValueError("扫描 PDF 混合识别后未得到有效 .docx")

    report = {
        "engine": engine,
        "workKind": work_kind,
        "layoutPages": (layout_info or {}).get("pages") if work_kind == "layout" else 0,
        "layoutTextboxes": (layout_info or {}).get("textboxes") if work_kind == "layout" else 0,
        "outputMode": str(cfg.get("outputMode") or "convert"),
        "docreconstructOk": bool(dr_result and dr_result.ok),
        "docreconstructQa": getattr(dr_result, "qa_score", 0) if dr_result else 0,
        "docreconstructProviders": getattr(dr_result, "providers", []) if dr_result else [],
        "docreconstructError": getattr(dr_result, "error", "") if dr_result else "",
        "geminiPages": ocr.get("need_gemini") or [],
        "pageChars": {
            str(pg): len(str((ocr.get("page_texts") or {}).get(pg) or ""))
            for pg in range(1, int(ocr.get("total") or 0) + 1)
        },
    }
    if cfg.get("keepIntermediates") and dr_result and getattr(dr_result, "work_dir", None):
        report["workDir"] = str(dr_result.work_dir)
    write_report(dst.with_suffix(".ocr-report.json"), report)
    return engine


def _is_docx(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return "word/document.xml" in z.namelist()
    except Exception:
        return False


def _word_convert(src: Path, dst: Path) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    doc = None
    src_abs = str(src.resolve())
    dst_abs = str(dst.resolve())
    if dst.exists():
        dst.unlink()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(
            src_abs,
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        doc.SaveAs2(dst_abs, FileFormat=16)  # wdFormatXMLDocument
        doc.Close(False)
        doc = None
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _pdf2docx_convert(src: Path, dst: Path) -> None:
    from pdf2docx import Converter

    if dst.exists():
        dst.unlink()
    cv = Converter(str(src))
    try:
        cv.convert(str(dst), start=0, end=None)
    finally:
        cv.close()


def _scanned_pdf_engine() -> str:
    return str(scan_pdf_settings().get("converter") or "hybrid").lower()


async def _scanned_pdf_to_docx(
    src: Path,
    dst: Path,
    log_fn=None,
    should_continue=None,
) -> str:
    from .docreconstruct_bridge import is_available, availability_status

    mode = _scanned_pdf_engine()
    if mode == "gemini":
        return await ocr_scanned_pdf_to_docx(src, dst, log_fn=log_fn, should_continue=should_continue)
    if mode == "docreconstruct" and not is_available():
        if log_fn:
            log_fn("docreconstruct 不可用，回退 Gemini OCR：" + availability_status())
        return await ocr_scanned_pdf_to_docx(src, dst, log_fn=log_fn, should_continue=should_continue)
    if mode == "docreconstruct":
        return await hybrid_scanned_pdf_to_docx(
            src, dst, log_fn=log_fn, should_continue=should_continue, gemini_fallback=False,
        )
    return await hybrid_scanned_pdf_to_docx(src, dst, log_fn=log_fn, should_continue=should_continue)


def convert_pdf_to_docx_sync(src: Path, dst: Path) -> str:
    """同步转换。数字版：Word COM / pdf2docx；扫描件：混合 OCR。"""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_scanned_pdf(src):
        return asyncio.run(_scanned_pdf_to_docx(src, dst))
    require_digital_pdf(src)
    errors = []
    for name, fn in (("word", _word_convert), ("pdf2docx", _pdf2docx_convert)):
        try:
            fn(src, dst)
            if dst.exists() and dst.stat().st_size > 64 and _is_docx(dst):
                return name
            errors.append(name + "：未得到有效 docx")
        except Exception as e:
            errors.append(name + "：" + str(e)[:160])
            if dst.exists():
                try:
                    dst.unlink()
                except Exception:
                    pass
    raise ValueError("PDF 转 Word 失败：" + "；".join(errors)[:300])


async def ensure_app_docx(src: Path, dst: Path, log_fn=None, should_continue=None) -> str:
    """Word/Excel 原样复制；PDF 转为 Word 工作稿。返回 copy/word/pdf2docx/ocr-gemini/ocr-hybrid 等。"""
    src, dst = Path(src), Path(dst)
    if src.suffix.lower() in WORD_APP_EXT | EXCEL_APP_EXT:
        if src.resolve() != dst.resolve():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        return "copy"
    if src.suffix.lower() != ".pdf":
        raise ValueError("申报书必须为 " + APP_EXT_HINT)
    if is_scanned_pdf(src):
        raise ValueError(
            "扫描 PDF 请使用 ocr_scanned_pdf_to_text 识别文本；落盘时套 QM/HJ 模板生成申报书，不再先转 Word"
        )
    proc = await asyncio.create_subprocess_exec(
        PYEXE, "-m", "app.pdf_app", str(src), str(dst),
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=PYENV,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), CONVERT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise ValueError("PDF 转 Word 超时（" + str(int(CONVERT_TIMEOUT_S)) + "s）")
    if (proc.returncode or 0) != 0:
        msg = (err or out or b"").decode("utf-8", "replace").strip()[:300]
        raise ValueError(msg or "PDF 转 Word 失败")
    if not dst.exists() or not _is_docx(dst):
        raise ValueError("PDF 转 Word 后未得到有效 .docx")
    engine = (out or b"").decode("utf-8", "replace").strip() or "ok"
    return engine.splitlines()[-1] if engine else "ok"


def main(argv: list) -> int:
    if len(argv) < 3:
        print("usage: python -m app.pdf_app <in.pdf> <out.docx>", file=sys.stderr)
        return 2
    engine = convert_pdf_to_docx_sync(Path(argv[1]), Path(argv[2]))
    print(engine)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
