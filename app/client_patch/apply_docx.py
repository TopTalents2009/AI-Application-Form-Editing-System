# -*- coding: utf-8 -*-
"""在材料文件夹中定位申报书 Word，按确认后的 find/replace 写出「原名_修改后.docx」。"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = APP_DIR / "scripts"

_SKIP_DIR = {
    "客户端",
    "申报系统",
    "App",
    "Database",
    "__pycache__",
    "上传系统附件",
    "上传附件",
}
_SKIP_NAME = re.compile(r"(模板|简历|意向|目录|缺失|邮件|论文|_修改后|_备份)")
_CODE_NAME = re.compile(r"^(25B[\-－_]|26-E[\-－_]|26E[\-－_])", re.I)
_EMPTY_FIND = {"", "(空)", "（空）"}


def _load_apply_edits():
    path = SCRIPTS_DIR / "apply_edits.py"
    spec = importlib.util.spec_from_file_location("sb_apply_edits", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def looks_like_application_form(path: Path) -> bool:
    name = path.name
    if path.suffix.lower() != ".docx" or name.startswith("~$"):
        return False
    if _SKIP_NAME.search(name):
        return False
    if "申报书" in name:
        return True
    if _CODE_NAME.search(name):
        return True
    return False


def find_application_forms(folder: Path) -> list[Path]:
    """输入材料文件夹内的申报书 Word（跳过客户端、模板、简历等）。"""
    if not folder.is_dir():
        return []
    found: list[Path] = []
    for p in folder.rglob("*.docx"):
        rel_parts = p.relative_to(folder).parts[:-1]
        if any(part in _SKIP_DIR or part.startswith(".") for part in rel_parts):
            continue
        if looks_like_application_form(p):
            found.append(p)
    for p in folder.glob("*.docx"):
        if p in found or p.name.startswith("~$"):
            continue
        if _SKIP_NAME.search(p.name):
            continue
        try:
            if p.stat().st_size >= 20_000:
                found.append(p)
        except OSError:
            continue
    seen: set[Path] = set()
    out: list[Path] = []
    for p in sorted(found, key=lambda x: (len(x.relative_to(folder).parts), x.name.lower())):
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _word_edits(edits: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for e in edits or []:
        find = str(e.get("find") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if find in _EMPTY_FIND:
            continue
        out.append({"find": find, "replace": str(e.get("replace") or "")})
    return out


def apply_forms(folder: Path, edits: list[dict[str, Any]]) -> dict[str, Any]:
    forms = find_application_forms(folder)
    if not forms:
        return {
            "folder": str(folder),
            "ok": False,
            "error": "未在该文件夹中找到申报书 Word（.docx）",
            "outputs": [],
        }
    word_edits = _word_edits(edits)
    if not word_edits:
        return {
            "folder": str(folder),
            "ok": False,
            "error": "没有可写入申报书的编辑（锚点为空）",
            "outputs": [],
            "sourceDocs": [str(p) for p in forms],
        }
    apply_file = _load_apply_edits().apply_file
    outputs = []
    for src in forms:
        out = src.with_name(src.stem + "_修改后.docx")
        bak = src.with_name(src.stem + "_备份.docx")
        results = apply_file(src, out, bak, word_edits)
        hits = sum(1 for r in results if str(r.get("status") or "").startswith("hit"))
        misses = sum(1 for r in results if r.get("status") == "miss")
        outputs.append({
            "src": str(src),
            "out": str(out),
            "backup": str(bak),
            "hits": hits,
            "misses": misses,
            "results": results,
        })
    return {
        "folder": str(folder),
        "ok": True,
        "outputs": outputs,
        "editCount": len(word_edits),
        "formCount": len(forms),
    }
