"""申报书 PDF → Word 工作稿。数字版直接转换；扫描件（无文字层）走 Gemini 视觉 OCR。"""
from __future__ import annotations
import asyncio, os, re, shutil, sys, zipfile
from pathlib import Path

from .config import PYEXE

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


async def ocr_scanned_pdf_to_docx(
    src: Path,
    dst: Path,
    log_fn=None,
    should_continue=None,
) -> str:
    """扫描 PDF：逐页 Gemini OCR → 合成 docx。返回引擎标识。"""
    from .opinion_extract import APP_PDF_OCR_PROMPT, ocr_image_bytes

    src, dst = Path(src), Path(dst)
    pages = rasterize_pdf_pages(src)
    if not pages:
        raise ValueError("PDF 没有可识别的页面")
    sem = asyncio.Semaphore(SCAN_OCR_PAGE_CONCURRENCY)
    done = 0
    total = len(pages)
    alive = should_continue or (lambda: True)

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
                log_fn("扫描 PDF OCR " + str(done) + "/" + str(total) + " 页")
            return pg, text

    embed_by_pg = {pg: embed for pg, _data, _mime, embed in pages}
    tasks = [asyncio.create_task(_one(pg, data, mime)) for pg, data, mime, _embed in pages]
    try:
        results = await asyncio.gather(*tasks)
    except Exception:
        for tk in tasks:
            if not tk.done():
                tk.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    results.sort(key=lambda x: x[0])
    chunks = []
    page_items = []
    for pg, text in results:
        body = str(text or "").strip()
        chunks.append("【第" + str(pg) + "页】")
        chunks.append(body if body else "（本页未识别到文字）")
        page_items.append((pg, embed_by_pg[pg], body))
    full = "\n".join(chunks)
    if _cjk_count(full) < 200:
        raise ValueError("扫描 PDF 识别结果过短，可能不是申报书或图片不清晰")
    # 工作稿：纯文字层（便于 apply_edits 稳定替换）；版式重排在落盘时 finalize_scanned_docx 完成
    build_docx_from_text(full, dst)
    if not dst.exists() or not _is_docx(dst):
        raise ValueError("扫描 PDF 识别后未得到有效 .docx")
    return "ocr-gemini"


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


def convert_pdf_to_docx_sync(src: Path, dst: Path) -> str:
    """同步转换。数字版：Word COM / pdf2docx；扫描件：Gemini OCR。"""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if is_scanned_pdf(src):
        return asyncio.run(ocr_scanned_pdf_to_docx(src, dst))
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
    """Word/Excel 原样复制；PDF 转为 Word 工作稿。返回 'copy' / 'word' / 'pdf2docx' / 'ocr-gemini'。"""
    src, dst = Path(src), Path(dst)
    if src.suffix.lower() in WORD_APP_EXT | EXCEL_APP_EXT:
        if src.resolve() != dst.resolve():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        return "copy"
    if src.suffix.lower() != ".pdf":
        raise ValueError("申报书必须为 " + APP_EXT_HINT)
    if is_scanned_pdf(src):
        from .opinion_extract import resolve_ocr_timeout

        _, _, _, n_pages = _pdf_text_stats(src)
        per_page = resolve_ocr_timeout()
        batches = max(1, (int(n_pages or 1) + SCAN_OCR_PAGE_CONCURRENCY - 1) // SCAN_OCR_PAGE_CONCURRENCY)
        timeout = max(SCAN_OCR_TIMEOUT_S, per_page * batches)
        try:
            return await asyncio.wait_for(
                ocr_scanned_pdf_to_docx(src, dst, log_fn=log_fn, should_continue=should_continue),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise ValueError("扫描 PDF 识别超时，请减少页数或改传 Word / Excel")
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
