# -*- coding: utf-8 -*-
"""给 start.cmd 调用：本机解析服务未起来时拉起 8767。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.parser_proc import ensure_parser

if __name__ == "__main__":
    ensure_parser(wait_sec=12)
    raise SystemExit(0)
