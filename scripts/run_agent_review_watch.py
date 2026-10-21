# -*- coding: utf-8 -*-
"""把 AI 审核关注群聊全部分析一遍（已完成的走增量，未完成的全量/续跑）。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent_pipeline import analyze_session
from app.agent_review import get_run
from app.wecom_groups import group_is_watched, list_watch_groups
from app import wecom_client as W


async def list_watch_group_chats() -> list[dict]:
    names = list_watch_groups()
    data = await W.list_merged_groups(kind="group", watch_only=True, limit=1000)
    rows = []
    seen = set()
    for it in data.get("items") or []:
        if not isinstance(it, dict) or not it.get("is_group"):
            continue
        name = str(it.get("display_name") or it.get("username") or "")
        sid = str(it.get("username") or it.get("session_id") or "")
        if not sid or sid in seen:
            continue
        if names and not group_is_watched(name, sid):
            continue
        seen.add(sid)
        run = get_run(sid) or {}
        rows.append({
            "name": name,
            "session_id": sid,
            "source_id": str(it.get("source_id") or "*"),
            "msg_count": int(it.get("msg_count") or 0),
            "full_done": bool(int(run.get("full_done") or 0)),
            "progress_done": int(run.get("progress_done") or 0),
            "progress_total": int(run.get("progress_total") or 0),
        })
    rows.sort(key=lambda r: (1 if r["full_done"] else 0, r["msg_count"], r["name"]))
    return rows


async def run_one(i: int, total: int, row: dict) -> dict:
    sid = row["session_id"]
    name = row["name"]
    last_pct = [-1]

    async def progress(ev: dict) -> None:
        pct = int(ev.get("percent") or 0)
        phase = str(ev.get("phase") or "")
        if phase in ("load", "ready", "done", "error") or pct >= last_pct[0] + 15:
            last_pct[0] = pct
            label = str(ev.get("label") or phase)
            print("    [%s%%] %s" % (pct, label), flush=True)

    t0 = time.time()
    print("[%d/%d] %s  msgs=%s  sid=%s" % (i, total, name, row["msg_count"], sid), flush=True)
    try:
        out = await analyze_session(
            sid,
            source_id=row["source_id"],
            name_hint=name,
            force=False,
            progress=progress,
        )
        dt = time.time() - t0
        stats = out.get("stats") or {}
        print(
            "    OK mode=%s jev=%s pending=%s queue=%s %.1fs"
            % (out.get("mode"), out.get("jevCalls"), out.get("pending"), stats.get("queue"), dt),
            flush=True,
        )
        return {"ok": True, "name": name, "sid": sid, "mode": out.get("mode"), "jev": out.get("jevCalls"), "queue": stats.get("queue")}
    except Exception as e:
        print("    FAIL %s" % (str(e)[:240],), flush=True)
        return {"ok": False, "name": name, "sid": sid, "error": str(e)[:240]}


async def main() -> int:
    rows = await list_watch_group_chats()
    print("关注群聊 %d 个" % len(rows), flush=True)
    if not rows:
        print("没有可分析的关注群聊")
        return 1
    results = []
    for i, row in enumerate(rows, 1):
        results.append(await run_one(i, len(rows), row))
    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    print("---")
    print("完成 %d  失败 %d" % (len(ok), len(bad)), flush=True)
    for r in bad:
        print("失败: %s  %s" % (r.get("name"), r.get("error")), flush=True)
    print("ALL DONE", flush=True)
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
