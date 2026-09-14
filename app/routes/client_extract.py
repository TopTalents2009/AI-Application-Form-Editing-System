"""客户端提取：计划 → 人工确认 → 写出输入文件夹中的新申报书。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ..config import STATIC_DIR
from ..client_patch.apply_docx import apply_forms
from ..client_patch.extract_birth import (
    AGE_LIMIT,
    TARGET_YEAR,
    extract_workspace,
    find_case_folders,
    input_dir,
    save_results,
)


def _result_path(root: Path) -> Path:
    return root / "提取结果.json"


def _plan_path(root: Path) -> Path:
    return root / "plan.json"


def _ok(data: dict):
    return JSONResponse(data, media_type="application/json; charset=utf-8")


def _load_json(p: Path):
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _flatten_edits(rows: list) -> list:
    out = []
    for row in rows or []:
        for e in row.get("edits") or []:
            item = dict(e)
            item.setdefault("client", row.get("clientAbs") or row.get("client"))
            item.setdefault("appNo", row.get("name") or row.get("folder") or "")
            item.setdefault("folder", row.get("folder") or "")
            if row.get("sourceDocs") and "sourceDocs" not in item:
                item["sourceDocs"] = row.get("sourceDocs")
            out.append(item)
    return out


def create_router():
    router = APIRouter()

    @router.get("/client-extract")
    def page():
        return FileResponse(
            STATIC_DIR / "client-extract.html",
            headers={"Cache-Control": "no-cache"},
        )

    @router.get("/api/client-extract/status")
    def status():
        root = input_dir()
        folders = find_case_folders(root)
        last = _load_json(_result_path(root))
        plan = _load_json(_plan_path(root))
        return _ok({
            "inbox": str(root),
            "folderCount": len(folders),
            "folders": [p.name for p in folders],
            "targetYear": TARGET_YEAR,
            "ageLimit": AGE_LIMIT,
            "cutoff": "1987-01-01",
            "lastCount": len(last) if isinstance(last, list) else 0,
            "lastResults": last if isinstance(last, list) else [],
            "plan": plan,
        })

    @router.post("/api/client-extract/plan")
    def plan():
        """扫描并生成编辑计划，不改 Word。"""
        root = input_dir()
        try:
            rows = extract_workspace(root)
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
        edits = _flatten_edits(rows)
        payload = {
            "status": "planned",
            "edits": edits,
            "leftovers": [],
            "rows": rows,
        }
        _plan_path(root).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        save_results(rows, _result_path(root))
        return _ok({
            "inbox": str(root),
            "status": "planned",
            "count": len(rows),
            "editCount": len(edits),
            "plan": payload,
            "results": rows,
        })

    @router.post("/api/client-extract/run")
    def run():
        """兼容旧按钮：等同生成计划，不写入。"""
        return plan()

    def _case_folder(root: Path, e: dict) -> Path:
        name = str(e.get("folder") or "").strip()
        if name:
            p = (root / name).resolve()
            root_r = root.resolve()
            if p.is_dir() and (p == root_r or root_r in p.parents):
                return p
        client = Path(str(e.get("client") or e.get("clientAbs") or "").strip())
        cur = client.resolve() if str(client) else None
        if cur and cur.is_file():
            cur = cur.parent
        root_r = root.resolve()
        while cur and cur != cur.parent:
            if cur.parent == root_r:
                return cur
            cur = cur.parent
        raise HTTPException(400, "无法定位该条编辑对应的输入文件夹")

    @router.post("/api/client-extract/apply")
    def apply(body: dict):
        raw = body.get("edits") or []
        edits = [e for e in raw if isinstance(e, dict)]
        if not edits:
            raise HTTPException(400, "没有可应用的编辑（请至少勾选一条）")
        root = input_dir()
        grouped: dict[str, list] = defaultdict(list)
        for e in edits:
            folder = _case_folder(root, e)
            grouped[str(folder)].append(e)

        applied = []
        for key, group in grouped.items():
            folder = Path(key)
            try:
                result = apply_forms(folder, group)
            except Exception as exc:
                raise HTTPException(500, f"{folder}: {exc}") from exc
            applied.append(result)

        leftover = [str(x) for x in (body.get("leftovers") or []) if str(x).strip()]
        rec = {
            "status": "done",
            "applied": applied,
            "leftovers": leftover,
        }
        (root / "apply_result.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return _ok(rec)

    return router
