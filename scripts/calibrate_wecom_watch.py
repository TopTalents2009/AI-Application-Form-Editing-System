# -*- coding: utf-8 -*-
"""值班判断校准：用标注集跑一轮 Gemini，输出命中率报告。"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import LLM_TIMEOUT_CLASSIFY, resolve_watch_llm
from app.llm import LlmError, chat
from app.wecom_jev import judgment_prompt
from app.wecom_watch_llm import parse_watch_json

FIXTURE = ROOT / "fixtures" / "wecom_watch_labeled.json"
REPORT_DIR = ROOT / "data" / "wecom_watch_calibration"


def _load_cases(limit: int = 0) -> list:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("标注集格式错误")
    if limit > 0:
        return rows[:limit]
    return rows


def _ids_match(got: list, want: list) -> bool:
    return set(got or []) == set(want or [])


async def _judge_one(prof: dict, case: dict) -> dict:
    t0 = time.monotonic()
    slim = case.get("messages") or []
    try:
        resp = await chat(
            [{"role": "user", "content": judgment_prompt(slim)}],
            json_mode=True,
            timeout_s=min(float(prof.get("timeoutSec") or 60), LLM_TIMEOUT_CLASSIFY),
            profile=prof,
            retries=1,
            apply_profile_timeout=False,
        )
        parsed = parse_watch_json(resp.get("content") or "")
        err = ""
    except (LlmError, ValueError, TypeError, json.JSONDecodeError) as e:
        parsed = {"hasOpinion": False, "opinions": [], "pairReady": False, "reason": ""}
        err = str(e)[:200]
    got_ids = [int(x.get("id")) for x in (parsed.get("opinions") or []) if x.get("id") is not None]
    exp = case.get("expect") or {}
    want_ids = [int(x) for x in (exp.get("opinionIds") or [])]
    confs = [str(x.get("confidence") or "") for x in (parsed.get("opinions") or [])]
    conf = confs[0] if confs else ""
    return {
        "id": case.get("id"),
        "ms": int((time.monotonic() - t0) * 1000),
        "error": err,
        "got": {
            "hasOpinion": bool(parsed.get("hasOpinion")),
            "opinionIds": got_ids,
            "pairReady": bool(parsed.get("pairReady")),
            "confidence": conf,
            "reason": parsed.get("reason") or "",
        },
        "expect": exp,
        "hit": {
            "hasOpinion": bool(parsed.get("hasOpinion")) == bool(exp.get("hasOpinion")),
            "opinionIds": _ids_match(got_ids, want_ids),
            "pairReady": bool(parsed.get("pairReady")) == bool(exp.get("pairReady")),
        },
    }


def _rate(rows: list, key: str) -> float:
    if not rows:
        return 0.0
    ok = sum(1 for r in rows if (r.get("hit") or {}).get(key))
    return ok / len(rows)


def _write_report(model: str, rows: list, path_json: Path, path_md: Path):
    summary = {
        "model": model,
        "count": len(rows),
        "hasOpinionAcc": round(_rate(rows, "hasOpinion") * 100, 1),
        "opinionIdsAcc": round(_rate(rows, "opinionIds") * 100, 1),
        "pairReadyAcc": round(_rate(rows, "pairReady") * 100, 1),
        "avgMs": round(sum(int(r.get("ms") or 0) for r in rows) / max(len(rows), 1), 1),
        "errors": sum(1 for r in rows if r.get("error")),
    }
    path_json.parent.mkdir(parents=True, exist_ok=True)
    path_json.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 值班判断校准报告",
        "",
        f"- 模型：`{model}`",
        f"- 样本数：{summary['count']}",
        f"- hasOpinion 命中率：{summary['hasOpinionAcc']}%",
        f"- opinionIds 命中率：{summary['opinionIdsAcc']}%",
        f"- pairReady 命中率：{summary['pairReadyAcc']}%",
        f"- 平均延迟：{summary['avgMs']} ms",
        f"- 请求失败：{summary['errors']}",
        "",
        "## 未命中样本",
        "",
    ]
    misses = [r for r in rows if not all((r.get("hit") or {}).values())]
    if not misses:
        lines.append("全部命中。")
    else:
        for r in misses:
            lines.append(f"### {r.get('id')}")
            if r.get("error"):
                lines.append(f"- 错误：{r['error']}")
            lines.append(f"- 期望：{json.dumps(r.get('expect') or {}, ensure_ascii=False)}")
            lines.append(f"- 实际：{json.dumps(r.get('got') or {}, ensure_ascii=False)}")
            lines.append("")
    path_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


async def run(limit: int = 0) -> dict:
    prof = resolve_watch_llm()
    cases = _load_cases(limit)
    rows = []
    for i, case in enumerate(cases):
        row = await _judge_one(prof, case)
        rows.append(row)
        print(f"[{i + 1}/{len(cases)}] {row['id']} has={row['hit']['hasOpinion']} ids={row['hit']['opinionIds']} pair={row['hit']['pairReady']} {row['ms']}ms")
        if row.get("error"):
            print("  err:", row["error"])
        await asyncio.sleep(0.35)
    stamp = time.strftime("%Y%m%d%H%M%S")
    path_json = REPORT_DIR / f"calibration_{stamp}.json"
    path_md = REPORT_DIR / f"calibration_{stamp}.md"
    summary = _write_report(str(prof.get("id") or ""), rows, path_json, path_md)
    print("\n=== summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("report:", path_md)
    return summary


def main():
    ap = argparse.ArgumentParser(description="值班判断校准（Gemini + 固定题目）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条，调试用")
    args = ap.parse_args()
    asyncio.run(run(limit=max(0, int(args.limit or 0))))


if __name__ == "__main__":
    main()
