"""客户端提取：计划 → 人工确认 → 写入（与申报书任务同一流程）。"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ..config import STATIC_DIR
from ..client_patch.extract_birth import (
    AGE_LIMIT,
    TARGET_YEAR,
    extract_workspace,
    find_case_folders,
    input_dir,
    save_results,
)
from ..client_patch.patch_titles import apply_client_edits


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
        """扫描并生成编辑计划，不写客户端库。"""
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

    @router.post("/api/client-extract/apply")
    def apply(body: dict):
        raw = body.get("edits") or []
        edits = [e for e in raw if isinstance(e, dict)]
        if not edits:
            raise HTTPException(400, "没有可应用的编辑（请至少勾选一条）")
        root = input_dir()
        grouped: dict[str, list] = defaultdict(list)
        for e in edits:
            key = str(e.get("client") or "").strip()
            if not key:
                raise HTTPException(400, "编辑缺少客户端路径")
            grouped[key].append(e)

        applied = []
        for key, group in grouped.items():
            pkg = Path(key)
            if not pkg.is_absolute():
                pkg = (root / key).resolve()
            if not pkg.is_dir():
                raise HTTPException(400, f"客户端目录不存在：{pkg}")
            try:
                result = apply_client_edits(pkg, group)
            except Exception as exc:
                raise HTTPException(500, f"{pkg}: {exc}") from exc
            applied.append({"client": str(pkg), **result})

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
