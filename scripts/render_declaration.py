# -*- coding: utf-8 -*-
"""从申报书正文（OCR/抽取 txt）按根目录 QM.docx / HJ.docx 模板生成申报书。

用法: python render_declaration.py <text_file> <QM|HJ> <out.docx>
stdout: JSON {"ok": true, "template": "...", "talent": "...", ...}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.scan_text_normalize import normalize_scanned_declaration_text
from app.pdf_parsers import parse_hj, parse_qm
from app.template_fill import fill_declaration_template


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: render_declaration.py <text_file> <QM|HJ> <out.docx>", file=sys.stderr)
        return 2
    text_path, mode, out_path = Path(argv[1]), str(argv[2]).upper(), Path(argv[3])
    raw = text_path.read_text(encoding="utf-8")
    text = normalize_scanned_declaration_text(raw, mode=mode)
    data = parse_hj(text) if mode == "HJ" else parse_qm(text)
    info = fill_declaration_template(mode, data, raw, out_path)
    print(json.dumps({
        "ok": True,
        "bytes": out_path.stat().st_size if out_path.exists() else 0,
        "talent": info.get("name") or "",
        "enterprise": info.get("enterprise") or "",
        "template": info.get("template") or "",
        "fields": info.get("fields") or 0,
        "scalarRows": info.get("scalarRows") or 0,
        "eduRows": info.get("eduRows") or 0,
        "workRows": info.get("workRows") or 0,
        "paperRows": info.get("paperRows") or 0,
        "hasExpertise": bool(info.get("hasExpertise")),
        "hasWorkPlan": bool(info.get("hasWorkPlan")),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
