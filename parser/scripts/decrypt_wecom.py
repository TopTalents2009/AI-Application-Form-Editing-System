"""企业微信一键解密：提取密钥 + 解密数据库（含 WAL 增量）。"""
import argparse
import importlib.util
import os
import sqlite3
import subprocess
import sys
from datetime import datetime


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools", "wechat-decrypt")
EXPORT_DIR = os.path.join(PROJECT_ROOT, "export")
KEYS_FILE = os.path.join(EXPORT_DIR, "wxwork_keys.json")
DECRYPTED_DIR = os.path.join(EXPORT_DIR, "wxwork_decrypted")
SQLITE_MAGIC = b"SQLite format 3\x00"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _step(msg: str) -> None:
    _log("")
    _log(f"==> {msg}")


def _wxwork_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WXWork.exe", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return "WXWork.exe" in (out.stdout or "")
    except OSError:
        return False


def _run_tool(script: str, extra_args=None) -> None:
    cmd = [sys.executable, script] + (extra_args or [])
    _log(f"    $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=TOOLS_DIR)
    if result.returncode != 0:
        raise RuntimeError(f"命令失败 (exit {result.returncode}): {script}")


def _verify_message_db() -> None:
    msg_db = os.path.join(DECRYPTED_DIR, "message.db")
    if not os.path.exists(msg_db):
        raise RuntimeError(f"解密完成但未找到 message.db: {msg_db}")
    with open(msg_db, "rb") as f:
        if f.read(16) != SQLITE_MAGIC:
            raise RuntimeError("message.db 仍是加密状态，请启动企业微信后重新运行")


def _show_summary() -> None:
    spec = importlib.util.spec_from_file_location(
        "wecom", os.path.join(PROJECT_ROOT, "core-wecom", "chat_platform.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    plat = mod.WeComPlatform()
    sessions = plat.list_sessions()
    msg_db = os.path.join(DECRYPTED_DIR, "message.db")
    msgs = 0
    latest = ""
    if os.path.exists(msg_db):
        conn = sqlite3.connect(msg_db)
        try:
            msgs = conn.execute("SELECT COUNT(*) FROM message_table").fetchone()[0]
            ts = conn.execute("SELECT MAX(send_time) FROM message_table").fetchone()[0]
            if ts:
                latest = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        finally:
            conn.close()

    _log(f"解密目录: {mod.DECRYPTED_DIR}")
    _log(f"会话数量: {len(sessions)}")
    _log(f"消息总数: {msgs}")
    if latest:
        _log(f"最新消息: {latest}")


def main() -> int:
    parser = argparse.ArgumentParser(description="企业微信一键解密")
    parser.add_argument("--skip-keys", action="store_true", help="跳过密钥提取，仅用已有密钥解密")
    parser.add_argument("--scan-bare-hex", action="store_true", help="密钥扫描启用裸 32-hex 模式（更慢）")
    args = parser.parse_args()

    _log("========================================")
    _log(" 企业微信一键解密")
    _log("========================================")

    os.makedirs(EXPORT_DIR, exist_ok=True)

    if not args.skip_keys:
        if _wxwork_running():
            _step("步骤 1/2：从企业微信进程提取密钥")
            key_args = []
            if args.scan_bare_hex:
                key_args.append("--scan-bare-hex")
            os.environ["WECHAT_DECRYPT_NONINTERACTIVE"] = "1"
            _run_tool("find_wxwork_keys.py", key_args)
        else:
            _log("")
            _log("[!] 企业微信未运行，跳过密钥提取")
            if not os.path.exists(KEYS_FILE):
                raise RuntimeError(
                    f"未找到密钥文件 {KEYS_FILE}，请先启动并登录企业微信 PC 版后重试"
                )
            _log(f"    将使用已有密钥: {KEYS_FILE}")
    else:
        _log("")
        _log("[*] 已指定 --skip-keys，跳过密钥提取")
        if not os.path.exists(KEYS_FILE):
            raise RuntimeError(f"未找到密钥文件 {KEYS_FILE}")

    _step("步骤 2/2：解密数据库（含 WAL 增量）")
    _run_tool("decrypt_wxwork_db.py")

    _verify_message_db()

    _log("")
    _log("========================================")
    _log(" 解密完成")
    _log("========================================")
    _show_summary()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
