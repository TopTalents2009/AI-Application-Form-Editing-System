# -*- coding: utf-8 -*-
"""1987-01-01 以前出生：改为创新人才，并把职务改到副高级以上。"""
from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .tms_sqlcipher import open_package_db, package_paths

APP_DIR = Path(__file__).resolve().parent.parent.parent
TITLES_FILE = APP_DIR / "副高级职位.txt"
CUTOFF = date(1987, 1, 1)
INNOVATION_NAME = "创新人才"
INNOVATION_ID = 272
BIO_TITLE = "高级研究院"

_TITLE_EN = {
    "高级工程师": "Senior Engineer",
    "副教授": "Associate Professor",
    "教授": "Professor",
    "技术总监": "Technical Director",
    "副总裁": "Vice President",
    "总裁": "President",
    "研发总监": "Research & Development Director",
}

_TITLE_TAIL = (
    r"(?:研发总监|技术总监|副总裁|高级工程师|副教授|"
    r"高级软件开发经理|软件开发经理|高级研究科学家|研究科学家|"
    r"高级研究员|人工智能研究员|"
    r"工程师|研究员|科学家|教授|讲师|总裁|经理|主任|主管|职员|"
    r"首席\w+)"
    r"(?:\s*[（(][^）)]*[）)])?"
)


def _s(v: Any) -> str:
    if v is None:
        return ""
    t = str(v).strip()
    return "" if t.lower() in {"none", "null"} else t


def load_senior_titles(path: Optional[Path] = None) -> list[str]:
    p = path or TITLES_FILE
    if not p.is_file():
        return list(_TITLE_EN.keys())
    raw = p.read_text(encoding="utf-8").strip()
    parts = [x.strip() for x in re.split(r"[、,，]", raw) if x.strip()]
    titles = []
    for part in parts:
        if "首席" in part:
            continue
        titles.append(part)
    return titles or list(_TITLE_EN.keys())


def is_senior_title(text: str, titles: Optional[list[str]] = None) -> bool:
    s = _s(text)
    if not s:
        return False
    if "首席" in s:
        return True
    for t in titles or load_senior_titles():
        if t and t in s:
            return True
    return False


def pick_senior_title(cn: str, en: str = "", titles: Optional[list[str]] = None) -> tuple[str, str]:
    titles = titles or load_senior_titles()
    blob = f"{cn} {en}"
    if is_senior_title(cn, titles) or is_senior_title(en, titles):
        chosen = next((t for t in titles if t in cn), None)
        if chosen:
            return chosen, _TITLE_EN.get(chosen, en or chosen)
        if "首席" in cn:
            return cn, en or cn
        return cn, en
    low = blob.lower()
    if any(k in blob for k in ("副教授", "Associate Professor")):
        cn_new = "副教授"
    elif any(k in blob for k in ("教授", "Professor")):
        cn_new = "教授"
    elif any(k in blob for k in ("总裁", "President")) and "副" not in cn:
        cn_new = "总裁"
    elif any(k in blob for k in ("副总裁", "Vice President")):
        cn_new = "副总裁"
    elif any(k in blob for k in ("研发", "R&D", "Research")) or "director" in low:
        cn_new = "研发总监"
    elif any(k in blob for k in ("工程师", "Engineer")):
        cn_new = "高级工程师"
    else:
        cn_new = "技术总监" if "技术总监" in titles else (titles[0] if titles else "技术总监")
    if cn_new not in titles and titles:
        cn_new = "技术总监" if "技术总监" in titles else titles[0]
    return cn_new, _TITLE_EN.get(cn_new, en or cn_new)


def rewrite_bio_titles(text: str) -> str:
    """现于/现任/现任职于…担任（或句末职务）→ 高级研究院。"""
    if not text:
        return text
    out = text
    out = re.sub(
        r"((?:现于|现任职于)[^。\n]{0,80}?担任)\s*" + _TITLE_TAIL,
        r"\1" + BIO_TITLE,
        out,
    )
    out = re.sub(
        r"((?:现于)[^。\n]{0,80}?担任)([^，。；\n]{1,30})",
        r"\1" + BIO_TITLE,
        out,
    )
    out = re.sub(
        r"(现任职于[^。\n]{0,80}?)(" + _TITLE_TAIL + r")",
        r"\1" + BIO_TITLE,
        out,
    )
    out = re.sub(
        r"(现任职)(?!于)([^。\n]{0,80}?)(" + _TITLE_TAIL + r")",
        r"\1\2" + BIO_TITLE,
        out,
    )
    out = re.sub(
        r"(现任)(?!职)([^。\n]{0,80}?)(" + _TITLE_TAIL + r")",
        r"\1\2" + BIO_TITLE,
        out,
    )
    return out


def _parse_start(v: Any) -> str:
    return _s(v).replace(".", "-")


def pick_last_work(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not rows:
        return None
    current = [
        r
        for r in rows
        if _s(r.get("EndTime")) in {"至今", "现在", "今", "present", "Present", "Now"}
    ]
    pool = current or rows
    return max(pool, key=lambda r: _parse_start(r.get("StartTime")))


def domestic_for(cn: str) -> str:
    if any(k in cn for k in ("教授", "副教授")):
        return cn if cn in ("教授", "副教授") else "副教授"
    return "企业高级职务"


def needs_age_patch(birth: Optional[date]) -> bool:
    return birth is not None and birth < CUTOFF


def _backup_db(pkg: Path) -> Optional[str]:
    db_path = package_paths(pkg)["db"]
    bak = db_path.with_name(db_path.name + ".bak")
    if not bak.is_file():
        shutil.copy2(db_path, bak)
        return str(bak)
    return None


def _read_fields(pkg: Path) -> dict[str, Any]:
    db, _ = open_package_db(pkg, readonly=True)
    try:
        info = db.query("SELECT * FROM [ApplicationInfo] LIMIT 1")
        info = info[0] if info else {}
        tables = db.tables()
        main_name = next((t for t in tables if t.endswith("_Applicant_Main")), "")
        work_name = next((t for t in tables if t.endswith("_WorkExperience")), "")
        main = db.query(f"SELECT * FROM [{main_name}] LIMIT 1") if main_name else []
        main = main[0] if main else {}
        works = db.query(f"SELECT * FROM [{work_name}]") if work_name else []
        last = pick_last_work(works) or {}
        return {
            "main_name": main_name,
            "work_name": work_name,
            "prev_cn": _s(main.get("PreviousPositionChinese")),
            "prev_en": _s(main.get("PreviousPositionTitleEnglish")),
            "last": last,
            "last_cn": _s(last.get("PositionChinese")),
            "last_en": _s(last.get("PositionEnglish")),
            "bio": _s(main.get("PersonalStatement")),
            "sub_name": _s(info.get("SubCategoryName")),
            "period": f"{_s(last.get('StartTime'))}-{_s(last.get('EndTime'))}".strip("-"),
        }
    finally:
        db.close()


def _edit(
    pkg: Path,
    *,
    section: str,
    clause: str,
    opinion: str,
    find: str,
    replace: str,
    table: str,
    row_id: Any,
    fields: dict[str, Any],
) -> dict[str, Any]:
    return {
        "client": str(pkg),
        "section": section,
        "_sec": section,
        "clause": clause,
        "opinion": opinion,
        "find": find,
        "replace": replace,
        "table": table,
        "id": row_id,
        "fields": fields,
        "appNo": pkg.parent.name if pkg.parent else "",
    }


def build_client_plan(pkg: Path, birth: Optional[date]) -> dict[str, Any]:
    """只读扫描：生成与申报书相同结构的 edits，不写库。"""
    snap = _read_fields(pkg)
    result: dict[str, Any] = {
        "sub_category": snap["sub_name"],
        "previous_position": snap["prev_cn"],
        "previous_position_en": snap["prev_en"],
        "last_work_period": snap["period"],
        "last_work_position": snap["last_cn"],
        "last_work_position_en": snap["last_en"],
        "personal_statement": snap["bio"],
        "patched": False,
        "patch_changes": [],
        "edits": [],
    }
    if not needs_age_patch(birth):
        result["patch_skip"] = "出生不早于1987-01-01，不改类型/职务"
        return result

    titles = load_senior_titles()
    new_prev_cn, new_prev_en = pick_senior_title(snap["prev_cn"], snap["prev_en"], titles)
    new_last_cn, new_last_en = pick_senior_title(snap["last_cn"], snap["last_en"], titles)
    new_bio = rewrite_bio_titles(snap["bio"])
    edits: list[dict[str, Any]] = []

    if snap["sub_name"] != INNOVATION_NAME:
        edits.append(
            _edit(
                pkg,
                section="人才类型",
                clause="改为创新人才",
                opinion="出生早于1987-01-01，须改为创新人才（SubCategoryId=272）。",
                find=snap["sub_name"] or "(空)",
                replace=INNOVATION_NAME,
                table="ApplicationInfo",
                row_id=1,
                fields={"SubCategoryName": INNOVATION_NAME, "SubCategoryId": INNOVATION_ID},
            )
        )
    main_name = snap["main_name"]
    if main_name and (snap["prev_cn"] != new_prev_cn or snap["prev_en"] != new_prev_en):
        edits.append(
            _edit(
                pkg,
                section="回国前职务",
                clause="职务升至副高及以上",
                opinion="回国（来华）前职务须达到副高级以上。",
                find=snap["prev_cn"] or "(空)",
                replace=new_prev_cn,
                table=main_name,
                row_id=1,
                fields={
                    "PreviousPositionChinese": new_prev_cn,
                    "PreviousPositionTitleEnglish": new_prev_en,
                    "EquivalentDomesticPosition": domestic_for(new_prev_cn),
                },
            )
        )
    last_id = snap["last"].get("id")
    work_name = snap["work_name"]
    if work_name and last_id is not None and (snap["last_cn"] != new_last_cn or snap["last_en"] != new_last_en):
        edits.append(
            _edit(
                pkg,
                section="工作经历",
                clause="末段工作职务升至副高",
                opinion=f"末段工作 {snap['period']} 职务须达到副高级以上。",
                find=snap["last_cn"] or "(空)",
                replace=new_last_cn,
                table=work_name,
                row_id=int(last_id),
                fields={
                    "PositionChinese": new_last_cn,
                    "PositionEnglish": new_last_en,
                    "EquivalentDomesticPosition": domestic_for(new_last_cn),
                },
            )
        )
    if main_name and new_bio != snap["bio"]:
        edits.append(
            _edit(
                pkg,
                section="基本情况",
                clause=f"现任职务改为{BIO_TITLE}",
                opinion="基本情况中「现于/现任…担任」的现职改为高级研究院。",
                find=snap["bio"][:400] or "(空)",
                replace=new_bio,
                table=main_name,
                row_id=1,
                fields={"PersonalStatement": new_bio},
            )
        )

    result["edits"] = edits
    if not edits:
        result["patch_skip"] = "已是创新人才且职务已达副高，无需改写"
    else:
        result["patch_skip"] = None
        result["patch_changes"] = [f"{e['section']} {e['find']} → {e['replace'][:40]}" for e in edits]
    return result


def apply_client_edits(pkg: Path, edits: list[dict[str, Any]]) -> dict[str, Any]:
    """按人工确认后的 edits 写库（与申报书 apply 阶段对应）。"""
    if not edits:
        return {"patched": False, "patch_changes": [], "patch_skip": "未勾选任何编辑"}
    _backup_db(pkg)
    changes: list[str] = []
    wdb, _ = open_package_db(pkg, readonly=False)
    try:
        for e in edits:
            table = str(e.get("table") or "")
            if not table:
                continue
            fields = dict(e.get("fields") or {})
            replace = str(e.get("replace") or "")
            sec = str(e.get("section") or e.get("_sec") or "")
            if replace:
                if sec == "人才类型":
                    fields["SubCategoryName"] = replace
                    if replace == INNOVATION_NAME:
                        fields["SubCategoryId"] = INNOVATION_ID
                elif sec == "回国前职务":
                    fields["PreviousPositionChinese"] = replace
                    if "PreviousPositionTitleEnglish" not in fields:
                        fields["PreviousPositionTitleEnglish"] = _TITLE_EN.get(replace, replace)
                    fields["EquivalentDomesticPosition"] = domestic_for(replace)
                elif sec == "工作经历":
                    fields["PositionChinese"] = replace
                    if "PositionEnglish" not in fields:
                        fields["PositionEnglish"] = _TITLE_EN.get(replace, replace)
                    fields["EquivalentDomesticPosition"] = domestic_for(replace)
                elif sec == "基本情况":
                    fields["PersonalStatement"] = replace
            row_id = e.get("id", 1)
            cols = list(fields.keys())
            if not cols:
                continue
            set_sql = ", ".join(f"[{c}]=?" for c in cols)
            vals = [fields[c] for c in cols]
            wdb.execute(f"UPDATE [{table}] SET {set_sql} WHERE id=?", vals + [int(row_id)])
            find = str(e.get("find") or "")
            repl = str(e.get("replace") or "")
            sec = str(e.get("section") or e.get("_sec") or "")
            changes.append(f"{sec} {find[:40]} → {repl[:40]}")
    finally:
        wdb.close()
    return {"patched": bool(changes), "patch_changes": changes}


def apply_client_patch(pkg: Path, birth: Optional[date]) -> dict[str, Any]:
    """兼容旧调用：直接出计划并写入（一键脚本用）。网页走 plan → apply。"""
    plan = build_client_plan(pkg, birth)
    if not plan.get("edits"):
        return plan
    applied = apply_client_edits(pkg, plan["edits"])
    plan.update(applied)
    return plan
