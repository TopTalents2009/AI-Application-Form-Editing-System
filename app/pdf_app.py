"""数字版申报书 PDF → Word 工作稿。扫描件（无足够文字层）直接拒绝。"""
from __future__ import annotations
import asyncio, os, re, shutil, sys, zipfile
from pathlib import Path

from .config import PYEXE

WORD_APP_EXT = {".docx", ".docm", ".wps"}
EXCEL_APP_EXT = {".xlsx", ".xlsm", ".xls"}
ALLOWED_APP_EXT = WORD_APP_EXT | EXCEL_APP_EXT | {".pdf"}
APP_EXT_HINT = ".docx / .docm / Excel（.xlsx .xlsm .xls）或数字版 .pdf"
SCAN_MSG = (
    "该 PDF 没有足够的可复制文字，像扫描件。"
    "当前只支持数字版 PDF（可复制选中文字），扫描件暂不支持。请改传 Word / Excel 或数字 PDF。"
)
CONVERT_TIMEOUT_S = 240
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


def require_digital_pdf(path: Path) -> str:
    """有足够文字层才视为数字版。返回抽出的正文（供日志）。"""
    path = Path(path)
    try:
        import pymupdf as fitz
    except ImportError as e:
        raise ValueError("服务器未安装 PyMuPDF，无法检查 PDF") from e
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise ValueError("无法打开 PDF：" + str(e)[:180]) from e
    try:
        if getattr(doc, "is_encrypted", False):
            unlocked = False
            try:
                unlocked = bool(doc.authenticate(""))
            except Exception:
                unlocked = False
            if not unlocked:
                raise ValueError("PDF 已加密，无法读取")
        n = doc.page_count or 0
        if n <= 0:
            raise ValueError("PDF 没有页面")
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
        cjk_all = _cjk_count(full)
        if cjk_all < MIN_CJK:
            raise ValueError(SCAN_MSG)
        if n >= 2 and scan_pages / n >= 0.7 and cjk_all < 4000:
            raise ValueError(SCAN_MSG)
        return full
    finally:
        doc.close()


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
    """同步转换。先 Word COM，失败再用 pdf2docx。返回使用的转换器名。"""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
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


async def ensure_app_docx(src: Path, dst: Path) -> str:
    """Word/Excel 原样复制；pdf 转为 Word 工作稿。返回 'copy' / 'word' / 'pdf2docx'。"""
    src, dst = Path(src), Path(dst)
    if src.suffix.lower() in WORD_APP_EXT | EXCEL_APP_EXT:
        if src.resolve() != dst.resolve():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        return "copy"
    if src.suffix.lower() != ".pdf":
        raise ValueError("申报书必须为 " + APP_EXT_HINT)
    require_digital_pdf(src)
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
