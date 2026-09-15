#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地测试扫描 PDF 混合 OCR（docreconstruct + Gemini 补扫）。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.docreconstruct_bridge import availability_status, is_available
from app.pdf_app import ensure_app_docx, is_scanned_pdf


async def main() -> int:
    ap = argparse.ArgumentParser(description="测试扫描 PDF 混合 OCR")
    ap.add_argument("pdf", type=Path, help="扫描 PDF 路径")
    ap.add_argument("-o", "--out", type=Path, default=None, help="输出 docx（默认与 PDF 同目录）")
    args = ap.parse_args()

    pdf = args.pdf.resolve()
    if not pdf.is_file():
        print("PDF 不存在:", pdf, file=sys.stderr)
        return 2
    out = args.out or pdf.with_name(pdf.stem + ".hybrid.docx")
    out = out.resolve()

    print("docreconstruct:", "可用" if is_available() else availability_status())
    print("扫描件:", is_scanned_pdf(pdf))

    def _log(msg: str) -> None:
        text = "[log] " + str(msg)
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                sys.stdout.encoding or "utf-8", errors="replace"
            ))

    engine = await ensure_app_docx(pdf, out, log_fn=_log)
    report = out.with_suffix(".ocr-report.json")
    print("engine:", engine)
    print("docx:", out, "size=", out.stat().st_size if out.is_file() else 0)
    if report.is_file():
        print("report:", json.dumps(json.loads(report.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
    return 0 if out.is_file() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
