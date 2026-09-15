# -*- coding: utf-8 -*-
"""docreconstruct CLI 桥接：扫描 PDF → 结构化 docx / Markdown / evidence。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import PYEXE, scan_pdf_settings
from .docreconstruct_adapt import (
    extract_page_texts_from_docx,
    extract_page_texts_from_evidence,
    extract_page_texts_from_markdown,
    merge_page_maps,
)

_QA_RE = re.compile(r"QA gates:\s*([\d.]+)%", re.I)


@dataclass
class ConvertResult:
    ok: bool
    docx_path: Path | None = None
    markdown_path: Path | None = None
    evidence_dir: Path | None = None
    work_dir: Path | None = None
    qa_score: float = 0.0
    page_texts: dict[int, str] = field(default_factory=dict)
    providers: list[str] = field(default_factory=list)
    error: str = ""
    stdout: str = ""
    stderr: str = ""


def is_docreconstruct_importable() -> bool:
    try:
        import docreconstruct  # noqa: F401

        return True
    except Exception:
        return False


def is_tesseract_available() -> bool:
    try:
        from docreconstruct.providers.tesseract_local import _find_tesseract

        _find_tesseract(None)
        return True
    except Exception:
        return False


def is_available() -> bool:
    return is_docreconstruct_importable() and is_tesseract_available()


def availability_status() -> str:
    if not is_docreconstruct_importable():
        return "docreconstruct 未安装（可执行 pip install -e tools/docreconstruct[hybrid]）"
    if not is_tesseract_available():
        return "Tesseract OCR 未安装或未加入 PATH（docreconstruct 扫描件需要 chi_sim+eng）"
    return "ok"


def _tessdata_usable(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not (path / "chi_sim.traineddata").is_file():
        return False
    if not (path / "eng.traineddata").is_file():
        return False
    # docreconstruct 需要 configs/tsv 才能输出几何信息
    return (path / "configs" / "tsv").is_file()


def _resolve_tessdata_dir(cfg: dict | None = None) -> Path | None:
    """返回可用于 chi_sim+eng 且支持 TSV 的 tessdata 目录。"""
    cfg = dict(cfg or scan_pdf_settings())
    raw = str(cfg.get("tessdataPrefix") or "").strip()
    candidates: list[Path] = []
    if raw:
        p = Path(raw).expanduser()
        candidates.append(p)
        if (p / "tessdata").is_dir():
            candidates.append(p / "tessdata")
    candidates.append(Path(r"C:\Program Files\Tesseract-OCR\tessdata"))
    local_root = Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tessdata"
    candidates.append(local_root)
    seen: set[str] = set()
    for cand in candidates:
        try:
            resolved = cand.resolve(strict=True)
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if _tessdata_usable(resolved):
            return resolved
    return None


def _apply_tesseract_env(env: dict, cfg: dict | None = None) -> None:
    """为 docreconstruct 子进程注入 Tesseract 路径与 tessdata。"""
    cfg = dict(cfg or scan_pdf_settings())
    tess_cmd = str(cfg.get("tesseractCmd") or "").strip()
    if tess_cmd and Path(tess_cmd).is_file():
        env["TESSERACT_CMD"] = tess_cmd
    tessdata = _resolve_tessdata_dir(cfg)
    if tessdata is not None:
        # Tesseract 在 Windows 上要求 TESSDATA_PREFIX 指向 tessdata 目录本身
        env["TESSDATA_PREFIX"] = str(tessdata)
    program = Path(r"C:\Program Files\Tesseract-OCR")
    if program.is_dir():
        env["PATH"] = str(program) + ";" + str(env.get("PATH") or "")


def _resolve_python(cfg: dict) -> str:
    custom = str(cfg.get("python") or "").strip()
    if custom and Path(custom).is_file():
        return custom
    return str(PYEXE)


def _parse_qa_score(stdout: str) -> float:
    m = _QA_RE.search(str(stdout or ""))
    if not m:
        return 0.0
    try:
        return float(m.group(1)) / 100.0
    except ValueError:
        return 0.0


def _parse_languages(raw: str) -> list[str]:
    text = str(raw or "chi_sim+eng").strip() or "chi_sim+eng"
    parts = re.split(r"[+,]", text)
    return [p.strip() for p in parts if p.strip()]


def _provider_timeout(cfg: dict) -> float:
    try:
        value = float(cfg.get("timeoutSec") or 900)
    except (TypeError, ValueError):
        value = 900.0
    return min(600.0, max(180.0, value))


def _run_convert_api(
    pdf_path: Path,
    out_docx: Path,
    work_dir: Path,
    cfg: dict,
    page_count: int,
) -> ConvertResult | None:
    """优先走 docreconstruct Python API（可调 OCR 超时与 tessdata）。"""
    try:
        from docreconstruct.extraction import ExtractionMode, extract_to_markdown
        from docreconstruct.providers import registry
        from docreconstruct.reconstruction.hybrid_job import run_hybrid_job
    except Exception:
        return None

    import os

    env_keys = ("TESSERACT_CMD", "TESSDATA_PREFIX", "PATH")
    env_backup = {k: os.environ.get(k) for k in env_keys}
    try:
        _apply_tesseract_env(os.environ, cfg)
        intermediates = work_dir / "convert"
        intermediates.mkdir(parents=True, exist_ok=True)
        markdown_path = intermediates / f"{pdf_path.stem}.ocr.md"
        langs = _parse_languages(str(cfg.get("languages") or "chi_sim+eng"))
        timeout = _provider_timeout(cfg)
        tessdata = _resolve_tessdata_dir(cfg)
        tess_cmd = str(cfg.get("tesseractCmd") or "").strip()
        tess_opts: dict[str, object] = {"timeout_seconds": timeout}
        if tessdata is not None:
            tess_opts["tessdata_dir"] = str(tessdata)
        if tess_cmd and Path(tess_cmd).is_file():
            tess_opts["executable"] = tess_cmd
        extraction = extract_to_markdown(
            pdf_path,
            output=markdown_path,
            mode=ExtractionMode.LOCAL,
            providers=["tesseract_local"],
            languages=langs,
            require_geometry=True,
            provider_options={"tesseract_local": tess_opts},
            provider_timeout_seconds=timeout,
            evidence_directory=intermediates / "evidence",
            registry=registry,
        )
        if not extraction.evidence_outputs:
            return ConvertResult(
                ok=False,
                work_dir=intermediates,
                error="OCR 未产出 geometry evidence",
            )
        evidence_paths = tuple(path.resolve() for path in extraction.evidence_outputs)
        job = run_hybrid_job(
            markdown_path,
            pdf_path,
            evidence=evidence_paths,
            evidence_provider_hints={str(path): "json" for path in evidence_paths},
            output=out_docx,
        )
        validation = job.validation
        md_pages = (
            extract_page_texts_from_markdown(markdown_path, page_count)
            if markdown_path.is_file()
            else {}
        )
        ev_pages = (
            extract_page_texts_from_evidence(intermediates / "evidence", page_count)
            if (intermediates / "evidence").is_dir()
            else {}
        )
        dx_pages = extract_page_texts_from_docx(out_docx, page_count) if out_docx.is_file() else {}
        page_texts = merge_page_maps(md_pages, ev_pages, dx_pages, page_count=page_count)
        providers = list(extraction.manifest.successful_providers or [])
        qa_score = float(validation.score or 0.0)
        min_qa = float(cfg.get("qaMinScore") or 0.0)
        if qa_score < min_qa and not any(str(v).strip() for v in page_texts.values()):
            return ConvertResult(
                ok=False,
                docx_path=out_docx if out_docx.is_file() else None,
                markdown_path=markdown_path,
                evidence_dir=intermediates / "evidence",
                work_dir=intermediates,
                qa_score=qa_score,
                page_texts=page_texts,
                providers=providers,
                error=f"QA 分数过低（{qa_score:.2f}）且无有效页文本",
            )
        return ConvertResult(
            ok=True,
            docx_path=out_docx,
            markdown_path=markdown_path,
            evidence_dir=intermediates / "evidence",
            work_dir=intermediates,
            qa_score=qa_score,
            page_texts=page_texts,
            providers=providers,
        )
    except Exception as e:
        return ConvertResult(ok=False, work_dir=work_dir, error=str(e)[:500])
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _parse_providers(stdout: str) -> list[str]:
    for ln in str(stdout or "").splitlines():
        if ln.strip().startswith("OCR:"):
            part = ln.split(";", 1)[0].replace("OCR:", "").strip()
            return [x.strip() for x in part.split(",") if x.strip()]
    return []


def run_convert(
    pdf_path: Path,
    out_docx: Path,
    work_dir: Path,
    cfg: dict | None = None,
    page_count: int = 0,
) -> ConvertResult:
    """调用 docreconstruct convert；失败时 ok=False，不抛异常。"""
    cfg = dict(cfg or scan_pdf_settings())
    pdf_path = Path(pdf_path).resolve()
    out_docx = Path(out_docx).resolve()
    work_dir = Path(work_dir).resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not is_docreconstruct_importable():
        return ConvertResult(ok=False, error=availability_status())

    api_result = _run_convert_api(pdf_path, out_docx, work_dir, cfg, page_count)
    if api_result is not None:
        return api_result

    py = _resolve_python(cfg)
    langs = str(cfg.get("languages") or "chi_sim+eng").strip() or "chi_sim+eng"
    timeout = int(cfg.get("timeoutSec") or 900)
    cmd = [
        py,
        "-m",
        "docreconstruct.cli",
        "convert",
        str(pdf_path),
        str(out_docx),
        "--keep-intermediates",
        "--languages",
        langs,
    ]
    import os

    env = dict(os.environ)
    _apply_tesseract_env(env, cfg)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(pdf_path.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ConvertResult(ok=False, work_dir=work_dir, error="docreconstruct convert 超时")
    except Exception as e:
        return ConvertResult(ok=False, work_dir=work_dir, error=str(e)[:240])

    stdout = str(proc.stdout or "")
    stderr = str(proc.stderr or "")
    qa_score = _parse_qa_score(stdout)
    providers = _parse_providers(stdout)

    if proc.returncode not in (0, 3) or not out_docx.is_file():
        err = (stderr or stdout or "docreconstruct convert 失败").strip()[:500]
        return ConvertResult(
            ok=False,
            work_dir=work_dir,
            qa_score=qa_score,
            providers=providers,
            error=err,
            stdout=stdout,
            stderr=stderr,
        )

    inter_dir = out_docx.parent / f"{out_docx.stem}.convert"
    md_path = inter_dir / f"{pdf_path.stem}.ocr.md"
    evidence_dir = inter_dir / "evidence"
    if not md_path.is_file():
        alt = list(inter_dir.glob("*.ocr.md"))
        md_path = alt[0] if alt else md_path

    md_pages = extract_page_texts_from_markdown(md_path, page_count) if md_path.is_file() else {}
    ev_pages = extract_page_texts_from_evidence(evidence_dir, page_count) if evidence_dir.is_dir() else {}
    dx_pages = extract_page_texts_from_docx(out_docx, page_count)
    page_texts = merge_page_maps(md_pages, ev_pages, dx_pages, page_count=page_count)

    min_qa = float(cfg.get("qaMinScore") or 0.0)
    if qa_score < min_qa and not any(str(v).strip() for v in page_texts.values()):
        return ConvertResult(
            ok=False,
            docx_path=out_docx,
            markdown_path=md_path if md_path.is_file() else None,
            evidence_dir=evidence_dir if evidence_dir.is_dir() else None,
            work_dir=inter_dir if inter_dir.is_dir() else work_dir,
            qa_score=qa_score,
            page_texts=page_texts,
            providers=providers,
            error=f"QA 分数过低（{qa_score:.2f}）且无有效页文本",
            stdout=stdout,
            stderr=stderr,
        )

    return ConvertResult(
        ok=True,
        docx_path=out_docx,
        markdown_path=md_path if md_path.is_file() else None,
        evidence_dir=evidence_dir if evidence_dir.is_dir() else None,
        work_dir=inter_dir if inter_dir.is_dir() else work_dir,
        qa_score=qa_score,
        page_texts=page_texts,
        providers=providers,
        stdout=stdout,
        stderr=stderr,
    )


def write_report(report_path: Path, payload: dict) -> None:
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
