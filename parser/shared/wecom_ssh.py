"""SSH access to a remote WeCom PC. Data can stay on the remote machine."""
import json
import os
import re
import tempfile
from typing import Optional

try:
    import paramiko
except ImportError:
    paramiko = None


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.jsonc")
LIVE_MANIFEST = os.path.join(PROJECT_ROOT, "export", "wxwork_remote", "live.json")
DEFAULT_KEY_FILE = os.path.join(os.path.expanduser("~"), ".ssh", "wecom_pull")
REMOTE_OUTPUT_DIR = r"C:\wxwork_pull\decrypted"


def strip_jsonc(text: str) -> str:
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


def load_remote_config() -> dict:
    defaults = {
        "host": "192.168.2.14",
        "user": "mache",
        "port": 22,
        "key_file": DEFAULT_KEY_FILE,
        "password": "",
        "include_cache": False,
        "live": True,
    }
    if not os.path.exists(CONFIG_FILE):
        return defaults
    with open(CONFIG_FILE, encoding="utf-8") as f:
        settings = json.loads(strip_jsonc(f.read()))
    cfg = dict(defaults)
    cfg.update(settings.get("wecom_remote") or {})
    return cfg


def load_live_manifest() -> Optional[dict]:
    if not os.path.exists(LIVE_MANIFEST):
        return None
    with open(LIVE_MANIFEST, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("mode") != "live":
        return None
    if not data.get("decrypted_dir"):
        return None
    return data


def write_live_manifest(info: dict) -> str:
    os.makedirs(os.path.dirname(LIVE_MANIFEST), exist_ok=True)
    payload = {
        "mode": "live",
        "host": info.get("host", ""),
        "user": info.get("user", ""),
        "port": int(info.get("port") or 22),
        "decrypted_dir": info.get("decrypted_dir") or REMOTE_OUTPUT_DIR,
        "account_root": info.get("account_root", ""),
        "account_id": str(info.get("account_id") or ""),
    }
    with open(LIVE_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return LIVE_MANIFEST


def connect_ssh(host: str, user: str, password: str = "", port: int = 22, key_file: str = ""):
    if paramiko is None:
        raise RuntimeError("paramiko 未安装，请执行: pip install paramiko")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": host,
        "port": int(port or 22),
        "username": user,
        "timeout": 30,
        "allow_agent": True,
        "look_for_keys": True,
    }
    if key_file and os.path.isfile(key_file):
        kwargs["key_filename"] = key_file
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
    if password:
        kwargs["password"] = password
    ssh.connect(**kwargs)
    return ssh


def run_remote(ssh, command: str, timeout: int = 600) -> str:
    _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if exit_status != 0:
        raise RuntimeError(f"Remote command failed ({exit_status}):\n{out}\n{err}")
    return out


class WeComRemoteSession:
    """Keep one SSH session and fetch remote DBs / files on demand."""

    def __init__(self, manifest: dict):
        cfg = load_remote_config()
        self.manifest = manifest
        self.host = manifest.get("host") or cfg.get("host")
        self.user = manifest.get("user") or cfg.get("user")
        self.port = int(manifest.get("port") or cfg.get("port") or 22)
        self.decrypted_dir = manifest.get("decrypted_dir") or REMOTE_OUTPUT_DIR
        self.account_root = manifest.get("account_root") or ""
        key_file = cfg.get("key_file") or DEFAULT_KEY_FILE
        if key_file and not os.path.isfile(key_file):
            key_file = ""
        password = os.environ.get("WECOM_REMOTE_PASSWORD", "") or (cfg.get("password") or "")
        self._ssh = connect_ssh(self.host, self.user, password, self.port, key_file)
        self._sftp = self._ssh.open_sftp()
        self._tmp = tempfile.mkdtemp(prefix="wecom_live_")
        self._db_cache = {}
        self._file_cache = {}

    def close(self):
        try:
            self._sftp.close()
        except Exception:
            pass
        try:
            self._ssh.close()
        except Exception:
            pass

    def _remote_join(self, *parts: str) -> str:
        path = self.decrypted_dir
        for part in parts:
            path = path.rstrip("\\/") + "\\" + part.lstrip("\\/")
        return path

    def fetch_db(self, name: str) -> str:
        if name in self._db_cache and os.path.exists(self._db_cache[name]):
            return self._db_cache[name]
        remote = self._remote_join(name)
        local = os.path.join(self._tmp, os.path.basename(name))
        self._sftp.get(remote, local)
        self._db_cache[name] = local
        return local

    def db_exists(self, name: str) -> bool:
        try:
            self._sftp.stat(self._remote_join(name))
            return True
        except OSError:
            return False

    def find_cache_file(self, name: str, md5: str = "") -> str:
        if not self.account_root:
            return ""
        name = os.path.basename((name or "").replace("\\", "/"))
        md5 = (md5 or "").replace("'", "")
        if not name and not md5:
            return ""
        script = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "scripts", "remote_find_cache.ps1")
        remote_script = r"C:\wxwork_pull\remote_find_cache.ps1"
        try:
            self._sftp.stat(remote_script)
        except OSError:
            if os.path.exists(script):
                self._sftp.put(script, remote_script)
        md5_arg = f" -Md5 '{md5}'" if md5 else ""
        out = run_remote(
            self._ssh,
            f'powershell -NoProfile -ExecutionPolicy Bypass -File "{remote_script}" '
            f'-AccountRoot "{self.account_root}" -Name "{name}"{md5_arg}',
            timeout=180,
        ).strip()
        for line in reversed(out.splitlines()):
            line = line.strip().strip('"')
            if line and ":\\" in line:
                cache_root = os.path.normcase(self.account_root.rstrip("\\") + "\\cache\\")
                if os.path.normcase(line).startswith(cache_root):
                    return line
        return ""

    def fetch_attachment(self, remote_path: str) -> str:
        if remote_path in self._file_cache and os.path.exists(self._file_cache[remote_path]):
            return self._file_cache[remote_path]
        name = os.path.basename(remote_path.replace("\\", "/"))
        local = os.path.join(self._tmp, "att_" + re.sub(r"[^\w.\-]+", "_", name))
        self._sftp.get(remote_path, local)
        self._file_cache[remote_path] = local
        return local


_session: Optional[WeComRemoteSession] = None


def get_live_session() -> Optional[WeComRemoteSession]:
    global _session
    manifest = load_live_manifest()
    if not manifest:
        return None
    if _session is None:
        _session = WeComRemoteSession(manifest)
    return _session
