# -*- coding: utf-8 -*-
"""构建 ChatInsight 并拷到 static/chatinsight，供 3777 /chatinsight/ 托管。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DST = ROOT / "static" / "chatinsight"


def find_web() -> Path:
    env = (os.environ.get("CHATINSIGHT_WEB") or "").strip()
    if env:
        p = Path(env)
        if (p / "package.json").is_file():
            return p
        raise SystemExit("CHATINSIGHT_WEB 不是有效的 ChatInsight web 目录：" + env)
    bundled = ROOT / "parser" / "web"
    if (bundled / "package.json").is_file():
        return bundled
    name = "企业微信聊天记录解析"
    cur = ROOT
    for _ in range(5):
        cand = cur.parent / name / "web"
        if (cand / "package.json").is_file():
            return cand
        cur = cur.parent
    raise SystemExit("找不到 ChatInsight 源码，请确认本仓库 parser/web 存在")


def npm_cmd() -> list[str]:
    exe = shutil.which("npm.cmd") or shutil.which("npm")
    if not exe:
        raise SystemExit("未找到 npm，请先安装 Node.js")
    return [exe]


def main() -> int:
    web = find_web()
    print("ChatInsight web:", web)
    if not (web / "node_modules").is_dir():
        print("npm install …")
        subprocess.check_call(npm_cmd() + ["install"], cwd=str(web))
    print("vite build …")
    vite = web / "node_modules" / ".bin" / ("vite.cmd" if os.name == "nt" else "vite")
    if vite.is_file():
        subprocess.check_call([str(vite), "build"], cwd=str(web))
    else:
        subprocess.check_call(npm_cmd() + ["exec", "--", "vite", "build"], cwd=str(web))
    dist = web / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("构建失败：没有 dist/index.html")
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(dist, DST)
    stamp = DST / "BUILD.txt"
    stamp.write_text(
        "built_at=" + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n"
        "source=" + str(web) + "\n",
        encoding="utf-8",
    )
    print("copied →", DST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
