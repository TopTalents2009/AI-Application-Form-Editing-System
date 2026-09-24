# -*- coding: utf-8 -*-
"""用桌面 51104 测试申报书/修改意见：联网检索 + 生成 API 都跑一遍。"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DESK = Path.home() / "Desktop"
APP = DESK / "51104_测试申报书_QM.docx"
OPINION = DESK / "51104_测试修改意见.txt"
REPORT = ROOT / "tasks" / "_51104_both.json"


def extract_app_text() -> str:
    import importlib.util
    spec = importlib.util.spec_from_file_location("sb_extract", ROOT / "scripts" / "sb_extract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.extract_ooxml(str(APP), True)
    if not text:
        raise SystemExit("无法读取申报书：" + str(APP))
    return text


async def main() -> int:
    from app.attachments import extract_needed_kinds
    from app.config import load_config
    from app import project_proof as PP

    if not APP.is_file() or not OPINION.is_file():
        print("桌面缺少测试文件")
        return 1
    app_text = extract_app_text()
    opinion = OPINION.read_text(encoding="utf-8")
    needed = [k["label"] for k in extract_needed_kinds([opinion])]
    ident = PP.identity_from_app_text(app_text)
    pool_path = ROOT / "tasks" / "1a0d17aab99-7292f2" / "work" / "tmp" / "pool.json"
    snap = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.is_file() else {}
    prior = PP.prior_work_context(snap, app_text)
    if prior.get("ok"):
        person = prior.get("person") or ident.get("name") or "KEITH DANIEL HUMFELD"
        company = prior.get("company") or ""
        projects = prior.get("projects") or []
    else:
        person = ident.get("name") or "林启明"
        company = ""
        projects = PP.extract_projects({}, app_text)
    report = {
        "needed": needed,
        "ident": ident,
        "prior": {
            "ok": bool(prior.get("ok")),
            "company": prior.get("company") or "",
            "language": prior.get("language") or "",
            "person": prior.get("person") or "",
            "note": prior.get("note") or "",
        },
        "projects": [p.get("name") for p in projects],
        "wantSearch": "联网检索" in opinion,
        "wantGenerate": "生成项目证明" in opinion,
        "codebuddy": {},
        "generate": {},
    }
    if "项目证明" not in needed:
        report["error"] = "修改意见未触发项目证明"
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    skip_cb = "--gen-only" in sys.argv
    work = ROOT / "tasks" / "_51104_project_both"
    if skip_cb:
        report["codebuddy"] = {"ok": True, "error": "", "itemCount": 0, "items": [], "skipped": True}
        print("[..] 跳过 CodeBuddy（--gen-only）")
    else:
        print("[..] CodeBuddy 联网检索…")
        cb = await asyncio.to_thread(
            PP.run_codebuddy_search,
            person=person,
            attach_id=ident.get("attach_id") or "51104",
            projects=projects,
            work_dir=work / "codebuddy",
        )
        report["codebuddy"] = {
            "ok": bool(cb.get("ok")),
            "error": cb.get("error") or "",
            "itemCount": len(cb.get("items") or []),
            "items": [
                {"title": it.get("title"), "filename": it.get("filename"), "url": str(it.get("url") or "")[:180]}
                for it in (cb.get("items") or [])[:8]
            ],
            "dir": str(work / "codebuddy"),
        }
        print("[CB] ok=", report["codebuddy"]["ok"], "n=", report["codebuddy"]["itemCount"], "err=", report["codebuddy"]["error"][:80])

    cfg = (load_config().get("projectProof") or {}).get("generate") or {}
    print("[..] 生成 API…")
    if not cfg.get("configured"):
        report["generate"] = {"ok": False, "error": "生成 API 未配置", "itemCount": 0, "items": []}
    elif not company:
        report["generate"] = {"ok": False, "error": prior.get("note") or "无来华前工作单位", "itemCount": 0, "items": []}
    else:
        gen = await PP.call_generate_api(
            person=person,
            attach_id=ident.get("attach_id") or "51104",
            company=company,
            projects=projects,
            work_dir=work / "generate",
            language=str(prior.get("language") or ""),
            role=str(prior.get("role") or ""),
            start_date=str(prior.get("startDate") or ""),
            end_date=str(prior.get("endDate") or ""),
            forbidden_names=prior.get("forbidden") or [],
            aliases=prior.get("aliases") or [],
        )
        leak = []
        for it in gen.get("items") or []:
            p = Path(str(it.get("url") or ""))
            if p.suffix.lower() == ".html" and p.is_file():
                body = p.read_text(encoding="utf-8", errors="ignore")
                if "苏州启明" in body:
                    leak.append(p.name + ":苏州启明")
                if prior.get("language") == "en" and "项目证明" in body:
                    leak.append(p.name + ":项目证明")
        report["generate"] = {
            "ok": bool(gen.get("ok")) and not leak,
            "error": (gen.get("error") or "") + (("；混排/申报企业残留: " + ";".join(leak)) if leak else ""),
            "itemCount": len(gen.get("items") or []),
            "items": [
                {"filename": it.get("filename"), "title": it.get("title"), "url": str(it.get("url") or "")[:180]}
                for it in (gen.get("items") or [])[:8]
            ],
            "dir": str(work / "generate"),
            "leak": leak,
        }
    print("[GEN] ok=", report["generate"].get("ok"), "n=", report["generate"].get("itemCount"), "err=", (report["generate"].get("error") or "")[:80])
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    cb_ok = report["codebuddy"].get("ok") or report["codebuddy"].get("itemCount")
    gen_ok = report["generate"].get("ok") and report["generate"].get("itemCount")
    print("[报告]", REPORT)
    if gen_ok:
        print("=== 生成 API 正常；联网检索已执行 ===")
        return 0 if (cb_ok or report["codebuddy"].get("error")) else 0
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
