# -*- coding: utf-8 -*-
"""扫描材料目录中的申报客户端，提取出生日期。"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .apply_docx import find_application_forms
from .patch_titles import build_client_plan
from .tms_sqlcipher import open_package_db

APP_DIR = Path(__file__).resolve().parent.parent.parent
INPUT_DIR_NAME = "client_inbox"
FOLDER_PREFIX = ""
TARGET_YEAR = 2027
TARGET_DATE = date(TARGET_YEAR, 12, 31)
AGE_LIMIT = 40
DATE_RE = re.compile(r"([0-9]{4})[.\-/年]([0-9]{1,2})(?:[.\-/月]([0-9]{1,2}))?")


def _s(v: Any) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in {"none", "null"} else t


def workspace_root() -> Path:
    return APP_DIR


def input_dir() -> Path:
    """系统根目录下的 client_inbox：把材料文件夹放进这里即可扫描。"""
    from ..config import load_config

    cfg = load_config()
    custom = str(cfg.get("clientInbox") or "").strip()
    path = Path(custom) if custom else (APP_DIR / INPUT_DIR_NAME)
    path.mkdir(parents=True, exist_ok=True)
    return path


_SKIP_DIR = {"__pycache__", "static"}


def find_case_folders(root: Path, prefix: str = FOLDER_PREFIX) -> list[Path]:
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name in _SKIP_DIR or p.name.startswith("."):
            continue
        if prefix and not p.name.startswith(prefix):
            continue
        out.append(p)
    return out


def looks_like_client_pkg(p: Path) -> bool:
    if not p.is_dir():
        return False
    if (p / "RCZX2026.exe").is_file() or (p / "plan-app.exe").is_file():
        return True
    if (p / "App" / "qsqlcipher.dll").is_file() and (p / "App" / "Database" / "db").is_file():
        return True
    return False


def find_client_pkgs(folder: Path) -> list[Path]:
    found: list[Path] = []
    for p in folder.rglob("*"):
        if p.is_dir() and looks_like_client_pkg(p):
            found.append(p)
    uniq: list[Path] = []
    for p in sorted(found, key=lambda x: len(str(x))):
        if any(str(p).startswith(str(u) + "\\") or str(p).startswith(str(u) + "/") for u in uniq):
            continue
        uniq.append(p)
    return uniq


def _first_row(db, table: str) -> dict[str, Any]:
    try:
        rows = db.query(f"SELECT * FROM [{table}] LIMIT 1")
    except Exception:
        return {}
    return rows[0] if rows else {}


def extract_from_client(pkg: Path) -> dict[str, Any]:
    """从 QM 客户端加密库或 HJ form.txt 抽出姓名与出生日期。"""
    if (pkg / "RCZX2026.exe").is_file() or (pkg / "App" / "Database" / "db").is_file():
        return _extract_qm(pkg)
    form = pkg / "resources" / "form.txt"
    if form.is_file():
        return _extract_hj(form)
    for child in pkg.iterdir() if pkg.is_dir() else []:
        if child.is_dir() and looks_like_client_pkg(child):
            return extract_from_client(child)
    raise FileNotFoundError(f"不是可识别的客户端包: {pkg}")


def _extract_qm(pkg: Path) -> dict[str, Any]:
    db, _ = open_package_db(pkg)
    try:
        info = _first_row(db, "ApplicationInfo")
        tables = db.tables()
        main_name = next((t for t in tables if t.endswith("_Applicant_Main")), "")
        main = _first_row(db, main_name) if main_name else {}
        name = _s(main.get("LegalNameOnCredential") or info.get("ApplicantName"))
        birth = _s(main.get("Birthdate") or main.get("DateOfBirth") or main.get("BirthDate"))
        mode = "QM"
        if any(t.startswith("HJ_") for t in tables):
            mode = "HJ"
        return {
            "name": name,
            "birth": birth,
            "mode": mode,
            "id_number": _s(
                main.get("PassportNumber")
                or main.get("ResidentIdentityCardNumber")
                or main.get("HongKongMacauTravelPermitNumber")
            ),
            "company": _s(info.get("EmployerName")),
            "sub_category": _s(info.get("SubCategoryName")),
        }
    finally:
        db.close()


def _extract_hj(form: Path) -> dict[str, Any]:
    data = json.loads(form.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"form.txt 不是 JSON 对象: {form}")
    en = " ".join(x for x in (_s(data.get("name")), _s(data.get("surname"))) if x).strip()
    cn = "".join(x for x in (_s(data.get("nameCn")), _s(data.get("surnameCn"))) if x)
    return {
        "name": en or cn,
        "birth": _s(data.get("dateOfBirth")),
        "mode": "HJ",
        "id_number": _s(data.get("IdNumber")),
        "company": _s(data.get("title3")),
        "sub_category": "火炬",
    }


def parse_birth(raw: str) -> Optional[date]:
    match = DATE_RE.search(raw or "")
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def age_on(birth: date, as_of: date) -> int:
    years = as_of.year - birth.year
    if (as_of.month, as_of.day) < (birth.month, birth.day):
        years -= 1
    return years


def attach_age(row: dict[str, Any]) -> dict[str, Any]:
    birth = parse_birth(str(row.get("birth") or ""))
    if birth is None:
        row["age_in_2027"] = None
        row["need_modify"] = None
        return row
    age = age_on(birth, TARGET_DATE)
    row["age_in_2027"] = age
    row["need_modify"] = age >= AGE_LIMIT
    return row


def extract_folder(folder: Path, workspace: Optional[Path] = None) -> list[dict[str, Any]]:
    root = workspace or workspace_root()
    pkgs = find_client_pkgs(folder)
    source_docs = [str(p) for p in find_application_forms(folder)]
    rows: list[dict[str, Any]] = []
    if not pkgs:
        return [
            attach_age(
                {
                    "folder": folder.name,
                    "client": None,
                    "name": None,
                    "birth": None,
                    "mode": None,
                    "sourceDocs": source_docs,
                    "error": "未找到客户端",
                }
            )
        ]
    for pkg in pkgs:
        try:
            rel = str(pkg.relative_to(root))
        except ValueError:
            rel = str(pkg)
        try:
            data = extract_from_client(pkg)
            birth_d = parse_birth(str(data.get("birth") or ""))
            patch = build_client_plan(pkg, birth_d)
            row = {
                "folder": folder.name,
                "client": rel,
                "clientAbs": str(pkg),
                "name": data.get("name") or "",
                "birth": data.get("birth") or "",
                "mode": data.get("mode") or "",
                "company": data.get("company") or "",
                "sub_category": patch.get("sub_category") or data.get("sub_category") or "",
                "previous_position": patch.get("previous_position") or "",
                "last_work_period": patch.get("last_work_period") or "",
                "last_work_position": patch.get("last_work_position") or "",
                "patched": False,
                "edits": patch.get("edits") or [],
                "patch_changes": patch.get("patch_changes") or [],
                "patch_skip": patch.get("patch_skip"),
                "sourceDocs": source_docs,
                "error": None if data.get("birth") else "客户端无出生日期",
            }
        except Exception as exc:
            row = {
                "folder": folder.name,
                "client": rel,
                "name": None,
                "birth": None,
                "mode": None,
                "company": "",
                "sourceDocs": source_docs,
                "error": str(exc),
            }
        rows.append(attach_age(row))
    return rows


def extract_workspace(
    root: Optional[Path] = None,
    prefix: str = FOLDER_PREFIX,
) -> list[dict[str, Any]]:
    scan_root = Path(root) if root else input_dir()
    scan_root.mkdir(parents=True, exist_ok=True)
    folders = find_case_folders(scan_root, prefix)
    rows: list[dict[str, Any]] = []
    for folder in folders:
        rows.extend(extract_folder(folder, workspace=scan_root))
    return rows


def save_results(rows: list[dict[str, Any]], out: Optional[Path] = None) -> Path:
    path = out or (Path(__file__).resolve().parent / "出生年月提取结果.json")
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
