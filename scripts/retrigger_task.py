# -*- coding: utf-8 -*-
"""重置任务状态并触发重跑（本地运维）。"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:3777"
LOGIN = {"username": "admin", "password": "Admin@123456"}


def patch_meta(tid: str, *, mode: str = "retry") -> dict:
    mp = ROOT / "tasks" / tid / "meta.json"
    if not mp.exists():
        raise SystemExit("任务不存在：" + tid)
    t = json.loads(mp.read_text(encoding="utf-8"))
    if mode == "planned":
        t["status"] = "planned"
    else:
        t["status"] = "failed"
    t["error"] = None
    t["finishedAt"] = None
    t.pop("appliedBy", None)
    t.pop("appliedAt", None)
    t["outputs"] = []
    t["deliverables"] = []
    t["hasReport"] = False
    mp.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    return t


def restart_server():
    ps = (
        "$ports=3777,3778; foreach($port in $ports){ "
        "$cs=@(Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue); "
        "foreach($c in $cs){ try{ $p=Get-Process -Id $c.OwningProcess -EA Stop; "
        "if($p.ProcessName -match 'python|uvicorn'){ Stop-Process -Id $p.Id -Force } } catch {} } }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
    time.sleep(1)
    py = sys.executable
    subprocess.Popen(
        [py, str(ROOT / "run.py")],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    for _ in range(40):
        try:
            if httpx.get(BASE + "/login", timeout=2).status_code == 200:
                print("server up")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("服务未在 20s 内启动")


def wait_planned(c: httpx.Client, tid: str, timeout_s: int = 600) -> dict:
    t0 = time.monotonic()
    last = ""
    while time.monotonic() - t0 < timeout_s:
        t = c.get(f"/api/tasks/{tid}").json()
        st = t.get("status") or ""
        log = t.get("log") or []
        if log:
            msg = log[-1].get("msg") or ""
            if msg != last:
                print(f"[{st}] {msg}")
                last = msg
        if st == "planned":
            return t
        if st == "failed":
            raise RuntimeError("任务失败：" + str(t.get("error") or "未知"))
        time.sleep(4)
    raise RuntimeError("等待 planned 超时")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tid", help="任务 ID")
    ap.add_argument("--mode", choices=("retry", "planned"), default="retry",
                    help="retry=重新生成计划；planned=仅回到待确认（保留 plan.json）")
    ap.add_argument("--no-restart", action="store_true")
    args = ap.parse_args()
    tid = str(args.tid).strip()
    patch_meta(tid, mode=args.mode)
    print("meta patched ->", args.mode)
    if not args.no_restart:
        restart_server()
    with httpx.Client(base_url=BASE, timeout=httpx.Timeout(600.0)) as c:
        r = c.post("/api/auth/login", json=LOGIN)
        if r.status_code != 200:
            raise SystemExit("登录失败：" + r.text[:200])
        if args.mode == "retry":
            r = c.post(f"/api/tasks/{tid}/retry")
            print("retry:", r.status_code, r.text[:200])
            if r.status_code != 200:
                raise SystemExit(1)
            t = wait_planned(c, tid)
        else:
            t = c.get(f"/api/tasks/{tid}").json()
        plan = c.get(f"/api/tasks/{tid}/plan").json()
        edits = plan.get("edits") or []
        print(f"\n任务 {tid} 状态：{t.get('status')}")
        print(f"计划：{len(edits)} 条编辑 / {len(plan.get('leftovers') or [])} 条遗留")


if __name__ == "__main__":
    main()
