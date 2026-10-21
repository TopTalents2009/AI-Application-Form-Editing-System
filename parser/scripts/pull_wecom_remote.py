"""SSH 到另一台 Windows 电脑：远程解密企业微信，再把库和聊天文件拉回本地。"""
import argparse
import getpass
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime

try:
    import paramiko
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
    import paramiko


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.wecom_ssh import write_live_manifest
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools", "wechat-decrypt")
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.jsonc")
LOCAL_DECRYPTED = os.path.join(PROJECT_ROOT, "export", "wxwork_decrypted")
LOCAL_REMOTE_ROOT = os.path.join(PROJECT_ROOT, "export", "wxwork_remote")
REMOTE_WORK_ROOT = r"C:\wxwork_pull"
REMOTE_TOOLS_DIR = rf"{REMOTE_WORK_ROOT}\tools"
REMOTE_OUTPUT_DIR = rf"{REMOTE_WORK_ROOT}\decrypted"
REMOTE_CACHE_TAR = rf"{REMOTE_WORK_ROOT}\wxwork_cache.tar"
CACHE_SUBDIRS = ("Image", "File", "Voice")
DEFAULT_KEY_FILE = os.path.join(os.path.expanduser("~"), ".ssh", "wecom_pull")


def _log(msg: str):
    text = str(msg)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"), flush=True)


def _strip_jsonc(text: str) -> str:
    result = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            result.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        result.append(ch)
        i += 1
    return re.sub(r",\s*([}\]])", r"\1", "".join(result))


def _load_remote_config() -> dict:
    defaults = {
        "host": "192.168.2.14",
        "user": "mache",
        "port": 22,
        "key_file": DEFAULT_KEY_FILE,
        "password": "",
        "include_cache": True,
    }
    if not os.path.exists(CONFIG_FILE):
        return defaults
    with open(CONFIG_FILE, encoding="utf-8") as f:
        settings = json.loads(_strip_jsonc(f.read()))
    cfg = dict(defaults)
    cfg.update(settings.get("wecom_remote") or {})
    return cfg


def _connect(host: str, user: str, password: str, port: int, key_file: str) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": host,
        "port": port,
        "username": user,
        "timeout": 30,
        "allow_agent": True,
        "look_for_keys": True,
    }
    if key_file and os.path.isfile(key_file):
        kwargs["key_filename"] = key_file
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
        _log(f"    Using SSH key: {key_file}")
    if password:
        kwargs["password"] = password
    ssh.connect(**kwargs)
    return ssh


def _run_remote(ssh: paramiko.SSHClient, command: str, timeout: int = 600) -> str:
    _log(f"    $ {command[:140]}")
    _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if exit_status != 0:
        raise RuntimeError(f"Remote command failed ({exit_status}):\n{out}\n{err}")
    return out


def _sftp_mkdirs(sftp: paramiko.SFTPClient, remote_path: str):
    parts = remote_path.replace("/", "\\").split("\\")
    current = parts[0] if parts[0].endswith(":") else ""
    start = 1 if current else 0
    for part in parts[start:]:
        if not part:
            continue
        current = f"{current}\\{part}" if current else part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def _download_dir(sftp: paramiko.SFTPClient, remote_dir: str, local_dir: str):
    import stat

    os.makedirs(local_dir, exist_ok=True)
    remote_dir = remote_dir.replace("\\", "/")

    def _walk(remote_path: str, local_path: str):
        for entry in sftp.listdir_attr(remote_path):
            name = entry.filename
            if name in (".", ".."):
                continue
            remote_item = f"{remote_path}/{name}"
            local_item = os.path.join(local_path, name)
            if stat.S_ISDIR(entry.st_mode):
                os.makedirs(local_item, exist_ok=True)
                _walk(remote_item, local_item)
            else:
                sftp.get(remote_item, local_item)

    _walk(remote_dir, local_dir)


def _sftp_get(sftp: paramiko.SFTPClient, remote_path: str, local_path: str):
    size = sftp.stat(remote_path).st_size
    last = {"pct": -1}

    def _cb(transferred, total):
        pct = int(transferred * 100 / total) if total else 100
        if pct != last["pct"] and (pct % 5 == 0 or pct == 100):
            last["pct"] = pct
            mb = transferred / (1024 * 1024)
            total_mb = total / (1024 * 1024)
            _log(f"    {mb:.1f}/{total_mb:.1f} MB ({pct}%)")

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    if size:
        _log(f"    Downloading {size / (1024 * 1024):.1f} MB ...")
    sftp.get(remote_path, local_path, callback=_cb)


def _parse_remote_output(output: str) -> dict:
    info = {}
    for line in output.splitlines():
        if "account_id=" in line:
            m = re.search(r"account_id=(\d+)", line)
            if m:
                info["account_id"] = m.group(1)
        if "account_root=" in line:
            m = re.search(r"account_root=(.+?) data_dir=", line)
            if m:
                info["account_root"] = m.group(1).strip()
    return info


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _pack_remote_cache(
    ssh: paramiko.SSHClient,
    sftp: paramiko.SFTPClient,
    account_root: str,
    full_cache: bool,
) -> bool:
    cache_root = rf"{account_root}\Cache"
    exists = _run_remote(
        ssh,
        "powershell -NoProfile -Command "
        f"\"if (Test-Path {_ps_quote(cache_root)}) {{ 'yes' }} else {{ 'no' }}\"",
        timeout=60,
    ).strip()
    if not exists.endswith("yes"):
        _log(f"    Cache folder not found: {cache_root}")
        return False

    local_script = os.path.join(os.path.dirname(__file__), "remote_pack_cache.ps1")
    sftp.put(local_script, rf"{REMOTE_WORK_ROOT}\remote_pack_cache.ps1")
    extra = " -FullCache" if full_cache else ""
    if not full_cache:
        _log("    Packing Cache subdirs: " + ",".join(CACHE_SUBDIRS))
    _run_remote(
        ssh,
        f'powershell -NoProfile -ExecutionPolicy Bypass -File "{REMOTE_WORK_ROOT}\\remote_pack_cache.ps1" '
        f'-AccountRoot "{account_root}" -OutputTar "{REMOTE_CACHE_TAR}"{extra}',
        timeout=1800,
    )
    return True


def _extract_cache_tar(tar_path: str, dest_dir: str):
    os.makedirs(dest_dir, exist_ok=True)
    with tarfile.open(tar_path, "r") as tf:
        try:
            tf.extractall(dest_dir, filter="data")
        except TypeError:
            tf.extractall(dest_dir)


def pull(
    host: str,
    user: str,
    password: str,
    port: int = 22,
    include_cache: bool = False,
    download_only: bool = False,
    key_file: str = "",
    full_cache: bool = False,
    live: bool = True,
):
    if not os.path.isdir(TOOLS_DIR):
        raise FileNotFoundError(f"Decrypt tools not found: {TOOLS_DIR}")

    _log("========================================")
    _log(f" Pull WeCom data from {user}@{host}")
    _log("========================================")

    _log("[*] Connecting SSH...")
    ssh = _connect(host, user, password, port, key_file)
    whoami = _run_remote(ssh, "whoami", timeout=30).strip()
    _log(f"    Connected as {whoami}")
    sftp = ssh.open_sftp()

    zip_path = None
    account_id = ""
    account_root = ""
    try:
        if download_only:
            _log("[*] Skipping remote decrypt, downloading existing data...")
            manifest_out = _run_remote(
                ssh,
                "powershell -NoProfile -Command "
                f"\"if (Test-Path '{REMOTE_OUTPUT_DIR}\\remote_manifest.json') "
                f"{{ Get-Content '{REMOTE_OUTPUT_DIR}\\remote_manifest.json' -Raw }} else {{ '' }}\"",
                timeout=60,
            ).strip()
            if manifest_out:
                manifest = json.loads(manifest_out)
                account_id = str(manifest.get("account_id", ""))
                account_root = manifest.get("account_root", "")
            if not account_id:
                raise RuntimeError("Remote decrypted data not found. Run without --download-only first.")
        else:
            _log("[*] Preparing remote workspace...")
            _run_remote(
                ssh,
                "powershell -NoProfile -Command "
                f"\"New-Item -ItemType Directory -Force -Path '{REMOTE_WORK_ROOT}','{REMOTE_TOOLS_DIR}' | Out-Null\"",
            )

            _log("[*] Uploading decrypt tools...")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                zip_path = tmp.name
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(TOOLS_DIR):
                    dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                    for name in files:
                        full = os.path.join(root, name)
                        zf.write(full, os.path.relpath(full, TOOLS_DIR))
            sftp.put(zip_path, rf"{REMOTE_WORK_ROOT}\tools.zip")
            _run_remote(
                ssh,
                "powershell -NoProfile -Command "
                f"\"Expand-Archive -Path '{REMOTE_WORK_ROOT}\\tools.zip' "
                f"-DestinationPath '{REMOTE_TOOLS_DIR}' -Force\"",
            )

            remote_script = os.path.join(os.path.dirname(__file__), "remote_wxwork_decrypt.ps1")
            sftp.put(remote_script, rf"{REMOTE_WORK_ROOT}\remote_wxwork_decrypt.ps1")

            _log("[*] Extracting keys and decrypting on remote PC...")
            _log(f"    (WeCom must be running and logged in on {host})")
            output = _run_remote(
                ssh,
                f'powershell -NoProfile -ExecutionPolicy Bypass -File "{REMOTE_WORK_ROOT}\\remote_wxwork_decrypt.ps1" '
                f'-ToolsDir "{REMOTE_TOOLS_DIR}" -OutputDir "{REMOTE_OUTPUT_DIR}"',
                timeout=900,
            )
            for line in output.splitlines():
                _log(f"    {line}")

            info = _parse_remote_output(output)
            account_id = info.get("account_id", "")
            account_root = info.get("account_root", "")
            if not account_id:
                raise RuntimeError("Remote decrypt did not return account_id.")

        if live and not download_only:
            live_path = write_live_manifest({
                "host": host,
                "user": user,
                "port": port,
                "decrypted_dir": REMOTE_OUTPUT_DIR,
                "account_root": account_root,
                "account_id": account_id,
            })
            find_script = os.path.join(os.path.dirname(__file__), "remote_find_cache.ps1")
            sftp.put(find_script, rf"{REMOTE_WORK_ROOT}\remote_find_cache.ps1")
            _log("")
            _log("========================================")
            _log(" Live mode: data stays on the remote PC")
            _log(f"   decrypted: {REMOTE_OUTPUT_DIR}")
            _log(f"   files:     {account_root}\\Cache")
            _log(f"   manifest:  {live_path}")
            _log("========================================")
            _log("本机不复制数据库和聊天文件。打开网页后按需通过 SSH 读取。")
            _log("Next: run .\\start.ps1 and open http://localhost:5173")
            _log("如需整包拉回本机: python scripts\\pull_wecom_remote.py --download")
            return

        _log("[*] Downloading decrypted databases...")
        with tempfile.TemporaryDirectory() as tmp:
            _download_dir(sftp, REMOTE_OUTPUT_DIR, tmp)
            os.makedirs(os.path.dirname(LOCAL_DECRYPTED), exist_ok=True)
            if os.path.isdir(LOCAL_DECRYPTED):
                backup = f"{LOCAL_DECRYPTED}.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                _log(f"    Backing up existing data to {backup}")
                try:
                    shutil.move(LOCAL_DECRYPTED, backup)
                except OSError:
                    shutil.copytree(LOCAL_DECRYPTED, backup, dirs_exist_ok=True)
                    shutil.rmtree(LOCAL_DECRYPTED, ignore_errors=True)
            if os.path.isdir(LOCAL_DECRYPTED):
                shutil.rmtree(LOCAL_DECRYPTED, ignore_errors=True)
            shutil.copytree(tmp, LOCAL_DECRYPTED)

        cache_dir = ""
        if include_cache or full_cache:
            _log("[*] Packing and downloading chat files (Image/File/Voice)...")
            if not account_root:
                account_root = rf"C:\Users\{user}\Documents\WXWork\{account_id}"
            if _pack_remote_cache(ssh, sftp, account_root, full_cache):
                with tempfile.TemporaryDirectory() as tmp:
                    local_tar = os.path.join(tmp, "wxwork_cache.tar")
                    _sftp_get(sftp, REMOTE_CACHE_TAR, local_tar)
                    cache_dir = os.path.join(LOCAL_REMOTE_ROOT, account_id, "Cache")
                    if os.path.isdir(cache_dir):
                        shutil.rmtree(cache_dir, ignore_errors=True)
                    _extract_cache_tar(local_tar, cache_dir)
                    _run_remote(
                        ssh,
                        "powershell -NoProfile -Command "
                        f"\"if (Test-Path '{REMOTE_CACHE_TAR}') {{ Remove-Item '{REMOTE_CACHE_TAR}' -Force }}\"",
                        timeout=60,
                    )

        manifest_src = os.path.join(LOCAL_DECRYPTED, "remote_manifest.json")
        if os.path.exists(manifest_src):
            os.makedirs(LOCAL_REMOTE_ROOT, exist_ok=True)
            shutil.copy2(manifest_src, os.path.join(LOCAL_REMOTE_ROOT, "manifest.json"))

        _log("")
        _log("========================================")
        _log(" Done! Data saved to:")
        _log(f"   {LOCAL_DECRYPTED}")
        if cache_dir:
            _log(f"   {cache_dir}")
        _log("========================================")
        _log("Next: run .\\start.ps1 and open http://localhost:5173")
    finally:
        sftp.close()
        ssh.close()
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)


def main():
    cfg = _load_remote_config()
    parser = argparse.ArgumentParser(
        description="SSH 到远程 Windows：解密企业微信并拉取聊天记录/文件"
    )
    parser.add_argument("--host", default=cfg.get("host") or "192.168.2.14")
    parser.add_argument("--user", default=cfg.get("user") or "mache")
    parser.add_argument(
        "--password",
        default=os.environ.get("WECOM_REMOTE_PASSWORD", cfg.get("password") or ""),
        help="SSH 密码；优先用密钥。也可用环境变量 WECOM_REMOTE_PASSWORD",
    )
    parser.add_argument("--port", type=int, default=int(cfg.get("port") or 22))
    parser.add_argument(
        "--key-file",
        default=cfg.get("key_file") or DEFAULT_KEY_FILE,
        help="SSH 私钥路径，默认 %USERPROFILE%\\.ssh\\wecom_pull",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="只拉解密后的数据库，不拉聊天图片/文件",
    )
    parser.add_argument(
        "--full-cache",
        action="store_true",
        help="拉取整个 Cache 目录（可能很大，默认只拉 Image/File/Voice）",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="跳过远程解密，只下载已有数据",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=None,
        help="数据留在远端，本机按需 SSH 读取（默认）",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="把解密库和聊天文件拉回本机（旧模式）",
    )
    args = parser.parse_args()

    key_file = args.key_file
    if key_file and not os.path.isfile(key_file):
        key_file = ""

    password = args.password
    if not key_file and not password:
        password = getpass.getpass(f"Password for {args.user}@{args.host}: ")

    live = bool(cfg.get("live", True))
    if args.download or args.download_only or args.full_cache:
        live = False
    if args.live:
        live = True

    include_cache = (not live) and bool(cfg.get("include_cache", False)) and not args.no_cache
    if args.full_cache:
        include_cache = True

    pull(
        args.host,
        args.user,
        password,
        args.port,
        include_cache=include_cache or args.full_cache,
        download_only=args.download_only,
        key_file=key_file,
        full_cache=args.full_cache,
        live=live,
    )


if __name__ == "__main__":
    main()
