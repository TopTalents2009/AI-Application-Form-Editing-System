# -*- coding: utf-8 -*-
"""通读全部关注群聊天记录，用 Gemini 配对申报书与修改意见，并输出报告。"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time

os.environ.setdefault("PYTHONUNBUFFERED", "1")
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from app.config import resolve_gemini
from app.wecom_ai_read import ai_read_watched
from app.wecom_ai_store import save_ai_read
from app.wecom_groups import list_watch_groups

REPORT_DIR = ROOT / "data" / "wecom_ai_batch"


def _dates(args) -> tuple[str, str]:
    start = str(args.start_date or "").strip()[:10]
    end = str(args.end_date or "").strip()[:10]
    if not start and not end and int(args.days or 0) > 0:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=int(args.days))).strftime("%Y-%m-%d")
    return start, end


def _write_md(result: dict, path: Path):
    sumo = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    lines = [
        "# 关注群 AI 解读报告",
        "",
        f"- 时间：{result.get('at') or ''}",
        f"- 模型：`{result.get('model') or '—'}`",
        f"- 关注群配置：{result.get('watchGroupCount') or 0} 个",
        f"- 实际分析：{result.get('groupCount') or 0} 个",
        f"- 日期窗：{result.get('start_date') or '—'} ~ {result.get('end_date') or '—'}",
        f"- 消息总数：{result.get('messageCount') or 0}",
        f"- 可上传配对：{result.get('readyCount') or 0}",
        "",
        "## 摘要",
        "",
        str(sumo.get("summary") or "—"),
        "",
    ]
    highlights = sumo.get("highlights") or []
    if highlights:
        lines += ["## 要点", ""]
        lines += [f"- {x}" for x in highlights]
        lines.append("")
    risks = sumo.get("risks") or []
    if risks or sumo.get("suggestedAction"):
        lines += ["## 待办与建议", ""]
        lines += [f"- {x}" for x in risks]
        if sumo.get("suggestedAction"):
            lines.append(f"- 建议：{sumo['suggestedAction']}")
        lines.append("")
    groups = result.get("groups") or []
    if groups:
        lines += ["## 各群扫描", ""]
        for g in groups:
            scan = g.get("scan") or {}
            lines.append(
                f"### {g.get('session_name') or g.get('session_id') or '未命名'}"
            )
            lines.append(
                f"- 消息 {g.get('messageCount') or 0} · "
                f"申报书 {scan.get('appFileCount') or 0} · "
                f"意见文档 {scan.get('opinionFileCount') or 0} · "
                f"可上传 {g.get('readyCount') or 0}"
            )
            if g.get("geminiNote"):
                lines.append(f"- Gemini：{g['geminiNote']}")
            cases = g.get("cases") or []
            for c in cases[:8]:
                ops = "、".join(
                    (o.get("filename") or ("群文本" if o.get("kind") == "text" else "意见"))
                    for o in (c.get("opinions") or [])
                )
                st = "可上传" if c.get("ready") and not c.get("existing") else (
                    "已有任务" if c.get("existing") else "未就绪"
                )
                lines.append(
                    f"  - [{st}] {c.get('filename') or c.get('id') or '—'}"
                    + (f" ← {ops}" if ops else "（无配对意见）")
                )
            lines.append("")
    cases = result.get("cases") or []
    if cases:
        lines += ["## 全部配对（汇总）", ""]
        for c in cases[:30]:
            ops = "、".join(
                (o.get("filename") or ("群文本" if o.get("kind") == "text" else "意见"))
                for o in (c.get("opinions") or [])
            )
            grp = c.get("session_name") or ""
            lines.append(
                f"- **{grp}** · {c.get('filename') or c.get('id') or '—'}"
                + (f" · 意见：{ops}" if ops else " · 无配对意见")
            )
        lines.append("")
    errors = result.get("errors") or []
    if errors:
        lines += ["## 错误", ""]
        lines += [f"- {e}" for e in errors]
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run(args) -> dict:
    allow = list_watch_groups()
    if not allow:
        raise SystemExit("尚未配置关注群：请在看板群列表点 ★，或编辑 config.json → wecomChat.groups")

    start, end = _dates(args)
    try:
        model = str(resolve_gemini().get("id") or "")
    except ValueError as e:
        raise SystemExit(str(e)) from e

    def log(*parts):
        print(*parts, flush=True)

    log("=== 关注群 AI 解读 ===")
    log("关注群数量:", len(allow))
    for i, g in enumerate(allow[:20], 1):
        log(f"  {i}. {g}")
    if len(allow) > 20:
        log(f"  … 另有 {len(allow) - 20} 个")
    log("日期窗:", (start or "默认"), "~", (end or "默认"))
    log("配对窗口:", int(args.window_hours or 48), "小时")
    log("模型:", model)
    log()

    t0 = time.monotonic()

    def on_group(done: int, total: int, one: dict):
        scan = one.get("scan") or {}
        log(
            f"[{done}/{total}] {one.get('session_name') or one.get('session_id')} "
            f"msgs={one.get('messageCount') or 0} "
            f"app={scan.get('appFileCount') or 0} "
            f"ready={one.get('readyCount') or 0}"
            + (f" · {one.get('geminiNote')}" if one.get("geminiNote") else "")
        )

    log("正在通读各群聊天记录并配对…")
    result = await ai_read_watched(
        start_date=start,
        end_date=end,
        window_hours=int(args.window_hours or 48),
        runner=None,
        on_group=on_group,
    )
    result["at"] = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    result["elapsedSec"] = round(time.monotonic() - t0, 1)

    log()
    sumo = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    log("=== 摘要 ===")
    log(sumo.get("summary") or "—")
    if sumo.get("suggestedAction"):
        log("建议:", sumo["suggestedAction"])
    log()
    log(
        f"群 {result.get('groupCount') or 0} · "
        f"消息 {result.get('messageCount') or 0} · "
        f"可上传 {result.get('readyCount') or 0} · "
        f"耗时 {result.get('elapsedSec')}s"
    )

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path_json = REPORT_DIR / f"read_{stamp}.json"
    path_md = REPORT_DIR / f"read_{stamp}.md"
    path_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_md(result, path_md)
    log("报告 JSON:", path_json)
    log("报告 MD:  ", path_md)

    if not args.no_store:
        meta = save_ai_read(result, user="script")
        log("已写入解读记录:", meta.get("id") or "")

    return result


def main():
    ap = argparse.ArgumentParser(description="通读关注群聊天记录并用 Gemini 配对申报书与修改意见")
    ap.add_argument("--start-date", default="", help="开始日期 YYYY-MM-DD")
    ap.add_argument("--end-date", default="", help="结束日期 YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=14, help="未指定日期时回溯天数（默认 14）")
    ap.add_argument("--window-hours", type=int, default=48, help="申报书与意见配对时间窗（小时）")
    ap.add_argument("--no-store", action="store_true", help="不写入 data/wecom_ai_reads 解读记录")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
