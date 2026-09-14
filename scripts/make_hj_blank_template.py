# -*- coding: utf-8 -*-
"""从正式 HJ 申报书生成空白模板（保留版式与标签，清除样例数据）。

默认以项目上级目录 HJ申报书模板.docx 为底稿，输出到项目根目录 HJ.docx。

用法:
  python scripts/make_hj_blank_template.py
  python scripts/make_hj_blank_template.py <source.docx> <out.docx>
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HJ_REF_DIR = Path(r"C:\Users\1\Desktop\work\HJ")
DEFAULT_SRC = ROOT.parent / "HJ申报书模板.docx"
FALLBACK_SRC = HJ_REF_DIR / "23516+仙湖实验室+申报书.docx"

sys.path.insert(0, str(ROOT))
from app.hj_template_blank import make_blank_template


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        src = Path(argv[1])
    elif DEFAULT_SRC.is_file():
        src = DEFAULT_SRC
    elif FALLBACK_SRC.is_file():
        src = FALLBACK_SRC
    else:
        src = DEFAULT_SRC
    out = Path(argv[2]) if len(argv) > 2 else ROOT / "HJ.docx"
    backup = out.with_suffix(".docx.bak")
    if out.is_file() and not backup.is_file():
        shutil.copyfile(out, backup)
    path = make_blank_template(src, out)
    print(f"blank template written: {path}")
    if backup.is_file():
        print(f"previous template backed up: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
