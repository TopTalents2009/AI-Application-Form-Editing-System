"""启动本机企业微信解析服务（默认 8767）。远程 baseUrl 不拉起。"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PARSER_NAME = "企业微信聊天记录解析"
BUNDLED_PARSER = ROOT / "parser"


def find_parser_root() -> Path | None:
    env = (os.environ.get("WECOM_PARSER_ROOT") or os.environ.get("CHATINSIGHT_ROOT") or "").strip()
    if env:
        p = Path(env)
        if (p / "api" / "server.py").is_file():
            return p
        return None
    if (BUNDLED_PARSER / "api" / "server.py").is_file():
        return BUNDLED_PARSER
    cur = ROOT
    for _ in range(5):
        cand = cur.parent / PARSER_NAME
        if (cand / "api" / "server.py").is_file():
            return cand
        cur = cur.parent
    return None


def local_parser_port() -> int | None:
    """仅当 wecomChat.baseUrl 指向本机时返回端口，否则不启动。"""
    try:
        from .config import load_config
        base = str(load_config().get("wecomChatBaseUrl") or "http://127.0.0.1:8767")
    except Exception:
        base = "http://127.0.0.1:8767"
    u = urlparse(base.strip() or "http://127.0.0.1:8767")
    host = (u.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return None
    return int(u.port or 8767)


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    try:
        s.settimeout(0.4)
        return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def _parser_python(root: Path) -> str:
    for cand in (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    ):
        if cand.is_file():
            return str(cand)
    return sys.executable


def _spawn_flags() -> int:
    if os.name != "nt":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return flags


def _alert_parser(got: dict) -> dict:
    """启动失败 / 超时进告警页，成功或本就在线不发。"""
    if not isinstance(got, dict):
        return got
    if got.get("skipped") or (got.get("ok") and not got.get("pending")):
        return got
    reason = str(got.get("reason") or "")
    if got.get("pending"):
        title = "解析服务启动超时"
        lines = ["本机端口在限定时间内未开始监听"]
    elif reason == "not_found":
        title = "解析服务未找到"
        lines = ["找不到 parser/api/server.py，请确认解析代码已放在本仓库 parser/"]
    elif reason == "remote":
        return got
    else:
        title = "解析服务启动失败"
        lines = [str(got.get("error") or reason or "未知错误")[:200]]
    try:
        from . import alert as AL
        AL.ops_fault(kind="parser", title=title, lines=lines)
    except Exception:
        pass
    return got


def ensure_parser(*, wait_sec: float = 8.0) -> dict:
    """若本机 8767 未监听则拉起解析项目 uvicorn。已在线则跳过。"""
    port = local_parser_port()
    if port is None:
        print("[parser] wecomChat.baseUrl 不是本机，不启动 8767", flush=True)
        return {"ok": True, "skipped": True, "reason": "remote"}
    if port_listening(port):
        print("[parser] 解析服务已在 http://127.0.0.1:%s" % port, flush=True)
        return {"ok": True, "skipped": True, "port": port, "reason": "already"}
    root = find_parser_root()
    if root is None:
        print("[parser] 找不到 parser/ 或「%s」，请确认本仓库 parser/api/server.py 存在" % PARSER_NAME, flush=True)
        return _alert_parser({"ok": False, "reason": "not_found"})
    py = _parser_python(root)
    log_dir = root / "logs"
    try:
        log_dir.mkdir(exist_ok=True)
        logf = open(log_dir / "uvicorn-8767.log", "a", encoding="utf-8")
    except OSError:
        logf = subprocess.DEVNULL
    args = [py, "-m", "uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", str(port)]
    print("[parser] 启动解析服务 %s  cwd=%s" % (" ".join(args[-6:]), root), flush=True)
    kw = {
        "cwd": str(root),
        "stdout": logf,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }
    flags = _spawn_flags()
    if flags:
        kw["creationflags"] = flags
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen(args, **kw)
    except OSError as e:
        print("[parser] 启动失败：" + str(e)[:200], flush=True)
        return _alert_parser({"ok": False, "reason": "spawn", "error": str(e)[:200]})
    deadline = time.time() + max(0.5, float(wait_sec or 0))
    while time.time() < deadline:
        if port_listening(port):
            print("[parser] 解析服务已就绪 http://127.0.0.1:%s" % port, flush=True)
            return {"ok": True, "port": port, "root": str(root)}
        time.sleep(0.35)
    print("[parser] 已发出启动命令，但 %ss 内尚未监听 %s，见 %s" % (int(wait_sec), port, log_dir / "uvicorn-8767.log"), flush=True)
    return _alert_parser({"ok": True, "pending": True, "port": port, "root": str(root)})
