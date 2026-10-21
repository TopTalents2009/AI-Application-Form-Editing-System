"""企业微信同步助手：安装在远端电脑，选择会话后推送到本机 ChatInsight。"""
import hashlib
import importlib.util
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, simpledialog, ttk
from urllib import error, request
from urllib.parse import quote


def _bundle_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _work_dir() -> str:
    root = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WeComSyncAgent")
    os.makedirs(root, exist_ok=True)
    return root


BUNDLE_DIR = _bundle_dir()
WORK_DIR = _work_dir()
SETTINGS_FILE = os.path.join(WORK_DIR, "settings.json")
LOCK_FILE = os.path.join(WORK_DIR, "agent.lock")
SHOW_FLAG = os.path.join(WORK_DIR, "show_ui.flag")
LOG_FILE = os.path.join(WORK_DIR, "agent.log")
DECRYPTED_DIR = os.path.join(WORK_DIR, "wxwork_decrypted")
KEYS_FILE = os.path.join(WORK_DIR, "wxwork_keys.json")
TOOLS_DIR = os.path.join(BUNDLE_DIR, "tools", "wechat-decrypt")
if not os.path.isdir(TOOLS_DIR):
    TOOLS_DIR = os.path.join(BUNDLE_DIR, "wechat-decrypt")


DEFAULT_SERVER_URL = "http://192.168.2.25:8767"
AUTO_SYNC_INTERVAL_MS = 5 * 60 * 1000
HEARTBEAT_INTERVAL_MS = 5 * 1000
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_AGENT_BYTES = 120 * 1024 * 1024
UPDATE_RETRY_SEC = 30 * 60
SYNC_BATCH_SIZE = 25
MESSAGE_EXPORT_LIMIT = 0
MESSAGE_TABLES = ("message_table", "message_small_table", "kf_message_tableV1")
AUTOSTART_NAME = "WeComSyncAgent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _read_agent_version() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(here, "VERSION"),
        os.path.join(BUNDLE_DIR, "agent", "VERSION"),
        os.path.join(BUNDLE_DIR, "VERSION"),
    ):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = (f.read() or "").strip()
            if text:
                return text
        except OSError:
            continue
    return "0"


AGENT_VERSION = _read_agent_version()
PENDING_UPDATE_FILE = os.path.join(WORK_DIR, "update", "pending.json")
_CHANGELOG_HEADING = re.compile(r"^##\s+(\S+)")
_CHANGELOG_BULLET = re.compile(r"^[-*]\s+(.+)$")


def _read_changelog_text() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(here, "CHANGELOG.md"),
        os.path.join(BUNDLE_DIR, "agent", "CHANGELOG.md"),
        os.path.join(BUNDLE_DIR, "CHANGELOG.md"),
    ):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = (f.read() or "").strip()
            if text:
                return text
        except OSError:
            continue
    return ""


def parse_changelog(text: str) -> list:
    entries = []
    current = None
    for raw in (text or "").splitlines():
        heading = _CHANGELOG_HEADING.match(raw.strip())
        if heading:
            current = {"version": heading.group(1).strip(), "notes": []}
            entries.append(current)
            continue
        if current is None:
            continue
        bullet = _CHANGELOG_BULLET.match(raw.strip())
        if bullet:
            note = bullet.group(1).strip()
            if note:
                current["notes"].append(note)
    return entries


def changelog_since(entries: list, last_seen: str, current: str) -> list:
    last_parts = parse_version(last_seen or "0")
    current_parts = parse_version(current or "0")
    out = []
    for item in entries:
        ver = parse_version(item.get("version") or "0")
        if last_parts < ver <= current_parts:
            out.append(item)
    out.sort(key=lambda item: parse_version(item.get("version") or "0"), reverse=True)
    return out


def format_update_notice(current: str, entries: list) -> str:
    lines = [f"助手已更新到 {current}", ""]
    for item in entries:
        version = item.get("version") or ""
        notes = item.get("notes") or []
        if version:
            lines.append(version)
        for note in notes:
            lines.append(f"• {note}")
        if notes or version:
            lines.append("")
    return "\n".join(lines).strip()


def looks_like_existing_install(settings: dict) -> bool:
    if not settings:
        return False
    if settings.get("setup_done"):
        return True
    if looks_like_real_name(str(settings.get("operator_name") or "")):
        return True
    if str(settings.get("token") or "").strip():
        return True
    if settings.get("auto_sync_ids"):
        return True
    return False


def save_pending_update(info: dict) -> None:
    os.makedirs(os.path.dirname(PENDING_UPDATE_FILE), exist_ok=True)
    payload = {
        "version": str((info or {}).get("version") or "").strip(),
        "notes": str((info or {}).get("notes") or "").strip(),
        "changelog": (info or {}).get("changelog") or [],
    }
    with open(PENDING_UPDATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_pending_update() -> dict:
    if not os.path.isfile(PENDING_UPDATE_FILE):
        return {}
    try:
        with open(PENDING_UPDATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def clear_pending_update() -> None:
    try:
        if os.path.isfile(PENDING_UPDATE_FILE):
            os.remove(PENDING_UPDATE_FILE)
    except OSError:
        pass


def _load_settings() -> dict:
    data = {
        "server_url": DEFAULT_SERVER_URL,
        "token": "",
        "auto_sync": True,
        "auto_sync_ids": [],
        "seen_conversation_ids": [],
        "operator_name": "",
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    if not str(data.get("server_url") or "").strip():
        data["server_url"] = DEFAULT_SERVER_URL
    # 远端 exe 连 127.0.0.1 会连到自己，自动改成服务器地址。
    if data["server_url"].rstrip("/") in ("http://127.0.0.1:8767", "http://localhost:8767"):
        data["server_url"] = DEFAULT_SERVER_URL
    if "auto_sync" not in data:
        data["auto_sync"] = True
    name = str(data.get("operator_name") or "").strip()
    if not looks_like_real_name(name):
        data["operator_name"] = ""
    return data


def looks_like_real_name(name: str) -> bool:
    text = (name or "").strip()
    if len(text) < 2:
        return False
    win_user = (os.environ.get("USERNAME") or "").strip()
    if win_user and text.lower() == win_user.lower():
        return False
    return True


def _save_settings(data: dict) -> None:
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def app_executable() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(__file__)


def autostart_command() -> str:
    exe = app_executable()
    if getattr(sys, "frozen", False):
        return f'"{exe}" --background'
    pythonw = sys.executable
    lower = pythonw.lower()
    if lower.endswith("python.exe"):
        candidate = pythonw[:-10] + "pythonw.exe"
        if os.path.isfile(candidate):
            pythonw = candidate
    return f'"{pythonw}" "{exe}" --background'


def enable_autostart() -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())


def disable_autostart() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
    except FileNotFoundError:
        return
    except OSError as exc:
        # 2 = 找不到该值，视为已关闭。
        if getattr(exc, "winerror", None) != 2:
            raise


def _no_window_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_hidden(args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_no_window_flags(),
    )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        out = _run_hidden(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        return str(pid) in (out.stdout or "")
    except OSError:
        return False


def request_existing_instance_to_show() -> bool:
    if not os.path.isfile(LOCK_FILE):
        return False
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    if not _pid_alive(pid):
        return False
    os.makedirs(WORK_DIR, exist_ok=True)
    with open(SHOW_FLAG, "w", encoding="utf-8") as f:
        f.write("1")
    return True


def acquire_instance_lock() -> bool:
    os.makedirs(WORK_DIR, exist_ok=True)
    if os.path.isfile(LOCK_FILE):
        try:
            with open(LOCK_FILE, encoding="utf-8") as f:
                pid = int(f.read().strip())
            if _pid_alive(pid) and pid != os.getpid():
                return False
        except (OSError, ValueError):
            pass
    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def release_instance_lock() -> None:
    try:
        if os.path.isfile(LOCK_FILE):
            os.remove(LOCK_FILE)
    except OSError:
        pass


def _wxwork_running() -> bool:
    try:
        out = _run_hidden(["tasklist", "/FI", "IMAGENAME eq WXWork.exe", "/NH"])
        return "WXWork.exe" in (out.stdout or "")
    except OSError:
        return False


def _prepare_decrypt_config():
    os.environ["WECHAT_DECRYPT_APP_DIR"] = WORK_DIR
    os.environ["WECHAT_DECRYPT_NONINTERACTIVE"] = "1"
    cfg_path = os.path.join(WORK_DIR, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["wxwork_keys_file"] = KEYS_FILE
    cfg["wxwork_decrypted_dir"] = DECRYPTED_DIR
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class _LineCapture:
    """Tee decrypt tool prints so failures show up in agent.log."""

    def __init__(self, orig, lines, limit=80):
        self.orig = orig
        self.lines = lines
        self.limit = limit
        self._buf = ""

    def write(self, text):
        if self.orig:
            try:
                self.orig.write(text)
            except Exception:
                pass
        self._buf += str(text or "")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            self.lines.append(line)
            if len(self.lines) > self.limit:
                del self.lines[: len(self.lines) - self.limit]

    def flush(self):
        if self.orig:
            try:
                self.orig.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def decrypt_failure_message(prefix: str, lines=None, code=None) -> str:
    head = prefix if code in (0, None) else f"{prefix}（退出码 {code}）"
    tail = [str(ln).strip() for ln in (lines or []) if str(ln).strip()][-4:]
    if not tail:
        return head
    return head + "。" + " ".join(tail)


def _guard_systemexit(fn, prefix: str, lines=None):
    try:
        return fn()
    except SystemExit as exc:
        if exc.code in (0, None):
            return None
        raise RuntimeError(decrypt_failure_message(prefix, lines, exc.code)) from None


def _run_decrypt():
    if not os.path.isdir(TOOLS_DIR):
        raise RuntimeError(f"找不到解密工具目录: {TOOLS_DIR}")
    _prepare_decrypt_config()
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    old_cwd = os.getcwd()
    os.chdir(TOOLS_DIR)
    captured = []
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        import find_wxwork_keys
        import decrypt_wxwork_db
        sys.stdout = _LineCapture(old_stdout, captured)
        sys.stderr = _LineCapture(old_stderr, captured)
        _guard_systemexit(find_wxwork_keys.main, "提取密钥失败", captured)
        code = _guard_systemexit(
            lambda: decrypt_wxwork_db.main([]),
            "解密失败",
            captured,
        )
        if code not in (0, None):
            raise RuntimeError(decrypt_failure_message("解密失败", captured, code))
    except Exception as exc:
        text = str(exc)
        if captured and captured[-1] not in text:
            raise RuntimeError(decrypt_failure_message(text, captured)) from None
        raise
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        os.chdir(old_cwd)


def _open_db(name: str):
    path = os.path.join(DECRYPTED_DIR, name)
    if not os.path.exists(path):
        return None
    return sqlite3.connect(path)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return bool(row)


def _user_map():
    mapping = {}
    conn = _open_db("user.db")
    if not conn:
        return mapping
    try:
        if _table_exists(conn, "user_table"):
            for uid, name, real_name, account in conn.execute(
                "SELECT id, name, real_name, account FROM user_table"
            ):
                display = real_name or name or account or ""
                if display:
                    mapping[int(uid)] = display
        if _table_exists(conn, "external_user_relation_v3"):
            for uid, remarks, real_remarks, corp_remark in conn.execute(
                "SELECT user_id, remarks, real_remarks, corp_remark FROM external_user_relation_v3"
            ):
                display = real_remarks or remarks or corp_remark or ""
                if display:
                    mapping[int(uid)] = display
    finally:
        conn.close()
    return mapping


def _room_nick_map():
    mapping = {}
    conn = _open_db("session.db")
    if not conn:
        return mapping
    try:
        if _table_exists(conn, "conversation_member_nickname_table"):
            for room_id, userid, nickname in conn.execute(
                "SELECT room_id, userid, nickname FROM conversation_member_nickname_table"
            ):
                if nickname and userid is not None and room_id is not None:
                    mapping[(int(room_id), int(userid))] = nickname
    finally:
        conn.close()
    return mapping


def _self_user_id(conversations=None):
    account = _account_id()
    if account.isdigit():
        return int(account)
    counts = {}
    session_n = 0
    for item in conversations or []:
        cid = str(item.get("id") or "")
        if not cid.startswith("S:"):
            continue
        ids = [int(x) for x in cid[2:].split("_") if x.isdigit()]
        if len(ids) < 2:
            continue
        session_n += 1
        for uid in set(ids):
            counts[uid] = counts.get(uid, 0) + 1
    for uid, n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        if session_n >= 2 and n == session_n:
            return uid
    return None


def _name_from_conversation_id(cid: str, users: dict, self_id=None) -> str:
    if not cid:
        return ""
    if cid.startswith("S:"):
        ids = [int(x) for x in cid[2:].split("_") if x.isdigit()]
        other = [uid for uid in ids if self_id is None or uid != self_id]
        for uid in other or ids:
            if uid in users:
                return users[uid]
    if ":" in cid:
        tail = cid.split(":", 1)[1]
        if tail.isdigit() and int(tail) in users:
            return users[int(tail)]
    return cid


def _resolve_sender(sender_raw, conversation_id: str, users: dict, room_nicks: dict) -> str:
    uid = None
    if isinstance(sender_raw, int) or (isinstance(sender_raw, str) and str(sender_raw).isdigit()):
        uid = int(sender_raw)
    if uid in (None, 0):
        return "系统" if uid == 0 else ""
    if str(conversation_id).startswith("R:"):
        tail = str(conversation_id)[2:]
        if tail.isdigit():
            nick = room_nicks.get((int(tail), uid))
            if nick:
                return nick
    return users.get(uid, str(uid))


def _load_message_decode():
    decode_path = os.path.join(BUNDLE_DIR, "core-wecom", "message_decode.py")
    if not os.path.exists(decode_path):
        decode_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "core-wecom",
            "message_decode.py",
        )
    spec = importlib.util.spec_from_file_location("wecom_message_decode", decode_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_decode_mod = None


def _decoder():
    global _decode_mod
    if _decode_mod is None:
        _decode_mod = _load_message_decode()
    return _decode_mod


def list_conversations():
    users = _user_map()
    room_nicks = _room_nick_map()
    counts = {}
    last_times = {}
    msg_conn = _open_db("message.db")
    if msg_conn:
        try:
            for table in ("message_table", "message_small_table", "kf_message_tableV1"):
                if not _table_exists(msg_conn, table):
                    continue
                for cid, c, t in msg_conn.execute(
                    f'SELECT conversation_id, COUNT(*), MAX(send_time) FROM "{table}" GROUP BY conversation_id'
                ):
                    if not cid:
                        continue
                    counts[cid] = counts.get(cid, 0) + int(c or 0)
                    last_times[cid] = max(last_times.get(cid, 0), int(t or 0))
        finally:
            msg_conn.close()

    conversations = {}
    session_conn = _open_db("session.db")
    if session_conn:
        try:
            if _table_exists(session_conn, "conversation_table"):
                for cid, name, remark, last_msg_time, _mid in session_conn.execute(
                    "SELECT id, name, roomname_remark, last_message_time, last_message_id FROM conversation_table"
                ):
                    if not cid:
                        continue
                    display = remark or name or cid
                    ts = max(int(last_msg_time or 0), last_times.get(cid, 0))
                    conversations[cid] = {
                        "id": cid,
                        "display_name": display,
                        "session_type": 2 if str(cid).startswith("R:") else 1,
                        "msg_count": counts.get(cid, 0),
                        "last_time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
                    }
        finally:
            session_conn.close()

    self_id = _self_user_id(conversations.values())
    for item in conversations.values():
        cid = item["id"]
        if not item["display_name"] or item["display_name"] == cid:
            resolved = _name_from_conversation_id(cid, users, self_id)
            if resolved:
                item["display_name"] = resolved

    for cid, count in counts.items():
        if cid not in conversations:
            ts = last_times.get(cid, 0)
            conversations[cid] = {
                "id": cid,
                "display_name": _name_from_conversation_id(cid, users, self_id) or cid,
                "session_type": 2 if str(cid).startswith("R:") else 1,
                "msg_count": count,
                "last_time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
            }
        else:
            conversations[cid]["msg_count"] = count

    rows = [c for c in conversations.values() if c["msg_count"] > 0]
    rows.sort(key=lambda x: x["last_time"], reverse=True)
    return rows, users, room_nicks


def is_group_chat(item: dict) -> bool:
    if int(item.get("session_type") or 0) == 2:
        return True
    cid = str(item.get("id") or item.get("username") or "")
    return cid.startswith("R:") or cid.endswith("@chatroom")


def split_conversations(rows: list):
    groups = [item for item in rows if is_group_chat(item)]
    singles = [item for item in rows if not is_group_chat(item)]
    return groups, singles


def sessions_for_auto_sync(conversations: list, checked_ids: list, seen_ids: list = None):
    """Sync groups and direct chats.

    Empty selection on a fresh install means everything. Newly seen chats are
    included even if the previous checkbox list only had group chats.
    """
    wanted = {str(cid) for cid in (checked_ids or []) if cid}
    seen = {str(cid) for cid in (seen_ids or []) if cid}
    if not wanted and not seen:
        return list(conversations or [])
    selected = []
    for item in conversations or []:
        cid = str(item.get("id") or "")
        if not cid:
            continue
        if cid in wanted or cid not in seen:
            selected.append(item)
    return selected


def checked_ids_for_render(conversations: list, saved_ids: list, seen_ids: list = None):
    """Default-check all chats, including newly appeared direct chats."""
    saved = {str(cid) for cid in (saved_ids or []) if cid}
    seen = {str(cid) for cid in (seen_ids or []) if cid}
    checked = []
    for item in conversations or []:
        cid = str(item.get("id") or "")
        if not cid:
            continue
        if cid in saved or cid not in seen:
            checked.append(cid)
    return checked


def fetch_ingest_info(server_url: str, timeout: int = 8) -> dict:
    url = server_url.rstrip("/") + "/api/ingest/info"
    req = request.Request(url, method="GET")
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def client_identity(operator_name: str = "") -> dict:
    host = ""
    try:
        host = socket.gethostbyname(socket.gethostname())
    except OSError:
        pass
    name = (operator_name or "").strip()
    return {
        "computer_name": socket.gethostname(),
        "operator_name": name,
        "username": name or os.environ.get("USERNAME") or "",
        "account_id": _account_id(),
        "host": host,
    }


def _post_json(server_url: str, path: str, payload: dict, token: str, timeout: int = 12) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    url = server_url.rstrip("/") + path
    req = request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8",
        "X-Ingest-Token": token,
    })
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_heartbeat(server_url: str, token: str, operator_name: str = "", status: str = "online") -> dict:
    payload = {
        "token": token,
        "agent_version": AGENT_VERSION,
        "status": status or "online",
        **client_identity(operator_name),
    }
    return _post_json(server_url, "/api/ingest/heartbeat", payload, token, timeout=8)


def send_offline(server_url: str, token: str, operator_name: str = "") -> dict:
    payload = {"token": token, **client_identity(operator_name)}
    return _post_json(server_url, "/api/ingest/offline", payload, token, timeout=4)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def parse_version(text: str) -> tuple:
    parts = []
    for item in str(text or "").strip().split("."):
        if not item.isdigit():
            break
        parts.append(int(item))
    return tuple(parts) if parts else (0,)


def version_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_exe_sha256 = ""


def current_exe_sha256() -> str:
    global _exe_sha256
    if _exe_sha256:
        return _exe_sha256
    path = app_executable()
    if not is_frozen() or not os.path.isfile(path):
        return ""
    _exe_sha256 = hash_file(path)
    return _exe_sha256


def should_apply_update(local_version: str, info: dict, current_sha256: str = "") -> bool:
    if not info:
        return False
    remote = str(info.get("version") or "").strip()
    sha256 = str(info.get("sha256") or "").strip().lower()
    size = int(info.get("size") or 0)
    if not remote or not sha256 or size <= 0:
        return False
    if current_sha256 and sha256 == current_sha256.strip().lower():
        return False
    if version_newer(remote, local_version):
        return True
    return (
        parse_version(remote) == parse_version(local_version)
        and bool(current_sha256)
        and sha256 != current_sha256.strip().lower()
    )


def resolve_update_url(server_url: str, url: str) -> str:
    url = (url or "").strip() or "/api/ingest/agent-exe"
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if not url.startswith("/"):
        url = "/" + url
    return server_url.rstrip("/") + url


def download_agent_exe(url: str, token: str, dest: str, expected_size: int, expected_sha256: str) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    digest = hashlib.sha256()
    req = request.Request(url, headers={"X-Ingest-Token": token})
    with request.urlopen(req, timeout=120) as resp:
        with open(tmp, "wb") as handle:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                handle.write(chunk)
                if handle.tell() > MAX_AGENT_BYTES:
                    raise ValueError("更新包过大")
    size = os.path.getsize(tmp)
    if expected_size and size != int(expected_size):
        raise ValueError("更新包大小不匹配")
    got = digest.hexdigest().lower()
    if got != str(expected_sha256 or "").strip().lower():
        raise ValueError("更新包校验失败")
    os.replace(tmp, dest)
    return dest


def _append_log(text: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def write_updater_script(pid: int, src: str, dest: str, extra_args: list) -> str:
    folder = os.path.join(WORK_DIR, "update")
    os.makedirs(folder, exist_ok=True)
    plan_path = os.path.join(folder, "plan.json")
    script_path = os.path.join(folder, "apply.ps1")
    with open(plan_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "pid": int(pid),
                "src": src,
                "dst": dest,
                "args": list(extra_args or []),
            },
            handle,
            ensure_ascii=True,
        )
    # Exit first, then replace the exe. Waiting on the UI thread left the file locked.
    script = r"""$ErrorActionPreference = 'Continue'
$planPath = Join-Path $PSScriptRoot 'plan.json'
$logPath = Join-Path $PSScriptRoot 'apply.log'
function Write-Log($m) {
  "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Out-File -FilePath $logPath -Append -Encoding utf8
}
$plan = Get-Content -LiteralPath $planPath -Raw | ConvertFrom-Json
$waitPid = [int]$plan.pid
Write-Log "stopping $waitPid"
Stop-Process -Id $waitPid -Force -ErrorAction SilentlyContinue
Get-Process WeComSyncAgent -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 40; $i++) {
  $left = @(Get-Process WeComSyncAgent -ErrorAction SilentlyContinue)
  if ($left.Count -eq 0) { break }
  Start-Sleep -Milliseconds 250
  Get-Process WeComSyncAgent -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
$copied = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    Copy-Item -LiteralPath $plan.src -Destination $plan.dst -Force
    $srcLen = (Get-Item -LiteralPath $plan.src).Length
    $dstLen = (Get-Item -LiteralPath $plan.dst).Length
    if ($srcLen -eq $dstLen) {
      $copied = $true
      break
    }
    Write-Log "size mismatch $dstLen $srcLen"
  } catch {
    Write-Log $_
  }
  Start-Sleep -Milliseconds 500
}
if (-not $copied) {
  Write-Log 'copy failed'
  exit 1
}
$lock = Join-Path (Split-Path -Parent $PSScriptRoot) 'agent.lock'
Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
$argList = @('--background')
Start-Process -FilePath $plan.dst -ArgumentList $argList
Write-Log "started $($plan.dst)"
"""
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(script)
    return script_path


def spawn_updater(script_path: str) -> None:
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = flags
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script_path,
        ],
        cwd=os.path.dirname(script_path),
        **kwargs,
    )


def apply_downloaded_update(new_path: str, dest_path: str, extra_args: list) -> str:
    script = write_updater_script(os.getpid(), new_path, dest_path, extra_args)
    spawn_updater(script)
    return script


_attach_mod = None


def _load_attachments():
    attach_path = os.path.join(BUNDLE_DIR, "core-wecom", "attachments.py")
    if not os.path.exists(attach_path):
        attach_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "core-wecom",
            "attachments.py",
        )
    spec = importlib.util.spec_from_file_location("wecom_attachments", attach_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _attachments():
    global _attach_mod
    if _attach_mod is None:
        _attach_mod = _load_attachments()
    return _attach_mod


def _wxwork_base_dirs() -> list:
    try:
        from shared.wxwork_paths import collect_wxwork_base_dirs
    except ImportError:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from shared.wxwork_paths import collect_wxwork_base_dirs
    return collect_wxwork_base_dirs()


def wxwork_account_roots() -> list:
    bases = _wxwork_base_dirs()
    roots = []
    seen = set()
    for base in bases:
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for uid_dir in names:
            if not uid_dir.isdigit():
                continue
            uid_path = os.path.join(base, uid_dir)
            if os.path.isdir(os.path.join(uid_path, "Cache")) and uid_path not in seen:
                seen.add(uid_path)
                roots.append(uid_path)
    return roots


def find_local_attachment(message_id: int, filename: str = "") -> str:
    file_db = os.path.join(DECRYPTED_DIR, "file.db")
    resolver = _attachments().WeComAttachmentResolver(file_db, wxwork_account_roots())
    path = resolver.resolve_file(int(message_id or 0), filename_hint=filename)
    if path and os.path.isfile(path):
        return path
    return ""


def report_file_job(server_url: str, token: str, job_id: str, status: str, detail: str = "") -> dict:
    payload = {
        "token": token,
        "job_id": job_id,
        "status": status,
        "detail": detail or "",
    }
    return _post_json(server_url, "/api/ingest/file-result", payload, token, timeout=12)


def upload_attachment(server_url: str, token: str, job_id: str, message_id: int, path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file too large")
    filename = os.path.basename(path)
    url = server_url.rstrip("/") + "/api/ingest/file"
    req = request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/octet-stream",
        "X-Ingest-Token": token,
        "X-Job-Id": job_id,
        "X-Message-Id": str(int(message_id or 0)),
        "X-Filename": quote(filename, safe=""),
    })
    with request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def process_file_job(server_url: str, token: str, job: dict) -> str:
    if str((job or {}).get("kind") or "") == "log":
        return process_log_job(server_url, token, job)
    job_id = str(job.get("job_id") or "")
    message_id = int(job.get("message_id") or 0)
    filename = str(job.get("filename") or "")
    if not job_id:
        return "error"
    path = find_local_attachment(message_id, filename)
    if not path:
        report_file_job(server_url, token, job_id, "missing", "对方电脑未缓存该文件")
        return "missing"
    upload_attachment(server_url, token, job_id, message_id, path)
    return "ready"


MAX_LOG_UPLOAD_BYTES = 512 * 1024


def read_agent_log_bytes(path: str, max_bytes: int = MAX_LOG_UPLOAD_BYTES) -> bytes:
    if not path or not os.path.isfile(path):
        return b""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
            data = data.split(b"\n", 1)[-1]
        else:
            data = handle.read()
    return data


def process_log_job(server_url: str, token: str, job: dict) -> str:
    job_id = str((job or {}).get("job_id") or "")
    if not job_id:
        return "error"
    if not os.path.isfile(LOG_FILE):
        report_file_job(server_url, token, job_id, "missing", "助手尚未产生日志")
        return "missing"
    data = read_agent_log_bytes(LOG_FILE)
    if not data:
        data = "（日志文件为空）\n".encode("utf-8")
    url = server_url.rstrip("/") + "/api/ingest/file"
    req = request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/octet-stream",
        "X-Ingest-Token": token,
        "X-Job-Id": job_id,
        "X-Filename": quote("agent.log", safe=""),
    })
    with request.urlopen(req, timeout=60) as resp:
        json.loads(resp.read().decode("utf-8"))
    return "ready"


def _cursor_map(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for cid, entry in raw.items():
        if not cid or not isinstance(entry, dict):
            continue
        tables = {}
        for table, value in entry.items():
            try:
                tables[str(table)] = int(value or 0)
            except (TypeError, ValueError):
                continue
        if tables:
            out[str(cid)] = tables
    return out


def _table_cursor(cursors: dict, conversation_id: str, table: str):
    """None means this table has never been synced for the conversation."""
    entry = (cursors or {}).get(str(conversation_id))
    if not isinstance(entry, dict) or table not in entry:
        return None
    try:
        return int(entry.get(table) or 0)
    except (TypeError, ValueError):
        return 0


def _message_id_column(cols: list) -> str:
    if "message_id" in cols:
        return "message_id"
    if "id" in cols:
        return "id"
    return ""


def _message_record(row: dict, conversation_id: str, users: dict, room_nicks: dict, decoder) -> dict:
    ts = row.get("send_time")
    dt = datetime.fromtimestamp(ts) if ts else None
    sender_raw = row.get("sender_id")
    if sender_raw in (None, ""):
        sender_raw = row.get("sender")
    sender = _resolve_sender(sender_raw, conversation_id, users, room_nicks)
    content_type = int(row.get("content_type") or 0)
    text, media_url, attachment_name = decoder.build_display_fields(
        content_type,
        row.get("content"),
        row.get("extra_content") or "",
        row.get("local_extra_content") or "",
    )
    if len(text) > 4000:
        text = text[:4000]
    return {
        "time_text": dt.strftime("%Y-%m-%d %H:%M") if dt else "",
        "hour": dt.hour if dt else None,
        "sender": sender,
        "sender_id": "" if sender_raw in (None, "") else str(sender_raw),
        "text": text,
        "msg_type": content_type,
        "msg_type_label": decoder.message_type_name(content_type),
        "message_id": int(row.get("message_id") or row.get("id") or 0),
        "has_attachment": content_type in (4, 7, 14, 15, 16, 20) or bool(media_url or attachment_name),
        "attachment_name": attachment_name,
        "media_url": media_url,
    }


def export_incremental(conversation_ids, users: dict, room_nicks: dict = None, cursors: dict = None):
    """Export only messages newer than each conversation's stored message id.

    Returns (messages_by_id, pending_cursors). pending_cursors is applied only
    after those messages are accepted by the server.
    """
    room_nicks = room_nicks or {}
    cursors = cursors or {}
    wanted = []
    seen = set()
    for cid in conversation_ids or []:
        text = str(cid or "")
        if not text or text in seen:
            continue
        seen.add(text)
        wanted.append(text)
    grouped = {}
    pending = {}
    if not wanted:
        return grouped, pending
    conn = _open_db("message.db")
    if not conn:
        return grouped, pending
    try:
        decoder = _decoder()
        for table in MESSAGE_TABLES:
            if not _table_exists(conn, table):
                continue
            cols = [d[0] for d in conn.execute(f'SELECT * FROM "{table}" LIMIT 1').description]
            id_col = _message_id_column(cols)
            if not id_col:
                continue
            highs = {}
            for cid, max_id in conn.execute(
                f'SELECT conversation_id, MAX("{id_col}") FROM "{table}" GROUP BY conversation_id'
            ):
                text = str(cid or "")
                if text not in seen:
                    continue
                try:
                    max_id = int(max_id or 0)
                except (TypeError, ValueError):
                    continue
                prev = _table_cursor(cursors, text, table)
                if prev is not None and max_id <= prev:
                    continue
                highs[text] = (prev, max_id)
            for cid, (prev, max_id) in highs.items():
                sql = f'SELECT * FROM "{table}" WHERE conversation_id = ?'
                params = [cid]
                if prev is not None:
                    sql += f' AND "{id_col}" > ?'
                    params.append(prev)
                rows = conn.execute(sql, tuple(params)).fetchall()
                bucket = grouped.setdefault(cid, [])
                for row in rows:
                    bucket.append(_message_record(
                        dict(zip(cols, row)), cid, users, room_nicks, decoder,
                    ))
                pending.setdefault(cid, {})[table] = max_id
    finally:
        conn.close()
    for cid, messages in grouped.items():
        messages.sort(key=lambda m: ((m.get("time_text") or ""), int(m.get("message_id") or 0)))
    return grouped, pending


def export_messages(conversation_id: str, users: dict, limit: int = MESSAGE_EXPORT_LIMIT,
                    room_nicks: dict = None):
    grouped, _pending = export_incremental([conversation_id], users, room_nicks or {}, {})
    out = grouped.get(str(conversation_id), [])
    if limit and int(limit) > 0 and len(out) > int(limit):
        return out[-int(limit):]
    return out


def fetch_server_cursors(server_url: str, token: str, operator_name: str = "") -> dict:
    payload = {"token": token, **client_identity(operator_name)}
    result = _post_json(server_url, "/api/ingest/cursors", payload, token, timeout=60)
    raw = (result or {}).get("cursors") or {}
    cursors = {}
    if not isinstance(raw, dict):
        return cursors
    for cid, value in raw.items():
        text = str(cid or "")
        if not text:
            continue
        try:
            cursors[text] = {"message_table": int(value or 0)}
        except (TypeError, ValueError):
            continue
    return cursors


def seed_cursors(server_url: str, token: str, cursors: dict, operator_name: str = "", log=None) -> dict:
    """Use ids already on the server so the first incremental run is not a full upload."""
    cursors = _cursor_map(cursors)
    if cursors:
        return cursors
    try:
        seeded = fetch_server_cursors(server_url, token, operator_name)
    except Exception as exc:
        if log:
            log(f"读取服务器同步进度失败，本次改为全量上传: {exc}")
        return {}
    if log:
        if seeded:
            log(f"按服务器已有记录增量同步，{len(seeded)} 个会话")
        else:
            log("服务器还没有这些聊天记录，本次全量上传")
    return seeded


def save_sync_cursors(cursors: dict) -> None:
    data = _load_settings()
    data["sync_cursors"] = _cursor_map(cursors)
    _save_settings(data)


def _account_id() -> str:
    try:
        with open(os.path.join(WORK_DIR, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        db_dir = cfg.get("wxwork_db_dir") or ""
        for part in reversed(os.path.normpath(db_dir).split(os.sep)):
            if part.isdigit() and len(part) >= 10:
                return part
    except Exception:
        pass
    return ""


def push_sessions(server_url: str, token: str, selected: list, users: dict, log,
                  operator_name: str = "", room_nicks: dict = None, cursors: dict = None):
    identity = client_identity(operator_name)
    if cursors is None:
        cursors = {}
    normalized = _cursor_map(cursors)
    cursors.clear()
    cursors.update(normalized)
    grouped, pending = export_incremental(
        [item.get("id") for item in (selected or [])],
        users,
        room_nicks or {},
        cursors,
    )
    last = {
        "saved_sessions": 0,
        "session_count": 0,
        "new_messages": 0,
        "computer_name": identity.get("computer_name") or "",
    }
    batch = []
    saved_total = 0
    new_total = 0
    pending_items = [item for item in (selected or []) if grouped.get(str(item.get("id") or ""))]
    total = len(pending_items)
    if not pending_items:
        log("增量同步：没有新消息")
        last["cursors"] = cursors
        return last
    log(f"增量同步：{total} 个会话有新消息，共 {sum(len(grouped[str(item['id'])]) for item in pending_items)} 条")

    def flush():
        nonlocal last, batch, saved_total, new_total
        if not batch:
            return
        payload = {
            "token": token,
            **identity,
            "sessions": batch,
        }
        result = _post_json(server_url, "/api/ingest/wecom", payload, token, timeout=180)
        for item in batch:
            cid = str(item.get("id") or "")
            entry = cursors.setdefault(cid, {})
            entry.update(pending.get(cid) or {})
            new_total += len(item.get("messages") or [])
        save_sync_cursors(cursors)
        saved_total += int((result or {}).get("saved_sessions") or 0)
        last = dict(result or last)
        last["saved_sessions"] = saved_total
        last["new_messages"] = new_total
        last["cursors"] = cursors
        batch = []

    for index, item in enumerate(pending_items, 1):
        cid = str(item.get("id") or "")
        messages = grouped.get(cid) or []
        log(f"上传 {item['display_name']} {len(messages)} 条（{index}/{total}）...")
        batch.append({
            "id": cid,
            "username": cid,
            "display_name": item["display_name"],
            "session_type": item["session_type"],
            "last_time": item["last_time"],
            "msg_count": item.get("msg_count") or len(messages),
            "messages": messages,
        })
        if len(batch) >= SYNC_BATCH_SIZE:
            flush()
    flush()
    last["cursors"] = cursors
    last["new_messages"] = new_total
    return last


class AgentApp:
    def __init__(self, root: tk.Tk, start_hidden: bool = False):
        self.root = root
        self.root.title(f"企业微信同步助手  {AGENT_VERSION}")
        self.root.geometry("820x720")
        self.settings = _load_settings()
        self.conversations = []
        self.users = {}
        self.room_nicks = {}
        self.var_by_id = {}
        self._busy = False
        self._auto_job = None
        self._hb_job = None
        self._hb_fail_logged = False
        self._job_lock = threading.Lock()
        self._active_jobs = set()
        self._updating = False
        self._update_retry_after = 0.0
        self._hidden = bool(start_hidden)
        if self._hidden:
            self.root.withdraw()

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(root)
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        ttk.Label(frm, text="服务器地址（ChatInsight，含端口）").pack(anchor="w")
        self.server_var = tk.StringVar(value=self.settings.get("server_url") or DEFAULT_SERVER_URL)
        self.server_entry = ttk.Entry(frm, textvariable=self.server_var)
        self.server_entry.pack(fill=tk.X)
        self.server_entry.bind("<FocusOut>", lambda _e: self.fetch_token())
        self.server_entry.bind("<Return>", lambda _e: self.fetch_token())

        ttk.Label(frm, text="真实姓名（显示在服务器在线提醒中）").pack(anchor="w", pady=(6, 0))
        self.name_var = tk.StringVar(value=self.settings.get("operator_name") or "")
        self.name_entry = ttk.Entry(frm, textvariable=self.name_var)
        self.name_entry.pack(fill=tk.X)
        self.name_entry.bind("<FocusOut>", lambda _e: self._persist_settings())

        token_row = ttk.Frame(frm)
        token_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(token_row, text="同步令牌（启动后自动获取）").pack(side=tk.LEFT)
        ttk.Button(token_row, text="重新获取", command=self.fetch_token).pack(side=tk.RIGHT)
        self.token_var = tk.StringVar(value=self.settings.get("token") or "")
        ttk.Entry(frm, textvariable=self.token_var).pack(fill=tk.X)

        auto_row = ttk.Frame(frm)
        auto_row.pack(fill=tk.X, pady=(8, 0))
        self.auto_var = tk.BooleanVar(value=bool(self.settings.get("auto_sync", True)))
        ttk.Checkbutton(
            auto_row,
            text="每 5 分钟自动解密并同步全部群聊和单聊",
            variable=self.auto_var,
            command=self._on_auto_toggle,
        ).pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="")
        ttk.Label(auto_row, textvariable=self.status_var).pack(side=tk.RIGHT)

        self.update_banner_var = tk.StringVar(value="")
        self.update_banner = ttk.Label(
            frm,
            textvariable=self.update_banner_var,
            foreground="#0f766e",
            wraplength=760,
            justify="left",
        )
        self._update_banner_btn = ttk.Button(frm, text="知道了", command=self._dismiss_update_notice)

        btns = ttk.Frame(frm)
        self.btn_row = btns
        btns.pack(fill=tk.X, pady=8)
        self.decrypt_btn = ttk.Button(btns, text="1. 解密并刷新会话", command=self.start_decrypt)
        self.decrypt_btn.pack(side=tk.LEFT)
        self.sync_btn = ttk.Button(btns, text="2. 同步所选会话到服务器", command=self.start_sync)
        self.sync_btn.pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="全选", command=lambda: self._set_all(True)).pack(side=tk.LEFT)
        ttk.Button(btns, text="全选群聊", command=lambda: self._set_kind(True, True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="全选单聊", command=lambda: self._set_kind(False, True)).pack(side=tk.LEFT)
        ttk.Button(btns, text="全不选", command=lambda: self._set_all(False)).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="退出应用", command=self.quit_app).pack(side=tk.RIGHT)
        ttk.Button(btns, text="更新说明", command=self.show_update_notes).pack(side=tk.RIGHT, padx=4)

        ttk.Label(frm, text="选择要同步的聊天").pack(anchor="w")
        list_wrap = ttk.Frame(frm)
        list_wrap.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(list_wrap, highlightthickness=0)
        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.canvas.yview)
        self.list_frame = ttk.Frame(self.canvas)
        self.list_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas_window = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(frm, text="日志").pack(anchor="w")
        self.log_box = tk.Text(frm, height=8, wrap="word")
        self.log_box.pack(fill=tk.X)
        ttk.Button(frm, text="完成", command=self.complete_and_hide).pack(anchor="e", pady=(8, 0))

        self.log("请保持企业微信已登录。默认同步全部群聊和单聊，点「完成」转入后台静默运行。")
        self.log(f"助手版本 {AGENT_VERSION}，服务器地址已默认填写：{self.server_var.get()}")
        self.root.protocol("WM_DELETE_WINDOW", self.complete_and_hide)
        self.root.after(800, self._poll_show_flag)
        self.root.after(200, self.fetch_token)
        self.root.after(800, self._ensure_heartbeat)
        self.root.after(2500, self._ensure_auto_sync)
        if os.path.isdir(DECRYPTED_DIR) and os.path.exists(os.path.join(DECRYPTED_DIR, "message.db")):
            self.log("发现已有解密数据，正在加载会话列表...")
            self.root.after(200, self.reload_conversations)
        if self._hidden:
            self.root.after(400, self._maybe_show_update_notice)
        else:
            self.root.after(400, self._startup_visible)

    def log(self, text: str):
        line = f"{datetime.now().strftime('%H:%M:%S')} {text}"
        try:
            self.log_box.insert(tk.END, line + "\n")
            self.log_box.see(tk.END)
        except tk.TclError:
            pass
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def _update_notice_text(self) -> str:
        last_seen = str(self.settings.get("last_seen_version") or "").strip()
        if not last_seen:
            if looks_like_existing_install(self.settings):
                last_seen = "0"
            else:
                return ""
        if parse_version(AGENT_VERSION) <= parse_version(last_seen):
            return ""
        entries = parse_changelog(_read_changelog_text())
        pending = load_pending_update()
        if pending.get("changelog"):
            entries = pending.get("changelog") or entries
        elif str(pending.get("notes") or "").strip():
            notes = [
                line.strip("•- ").strip()
                for line in str(pending.get("notes")).splitlines()
                if line.strip()
            ]
            if notes:
                entries = [{"version": pending.get("version") or AGENT_VERSION, "notes": notes}]
        items = changelog_since(entries, last_seen, AGENT_VERSION)
        if not items:
            items = [{"version": AGENT_VERSION, "notes": ["助手已自动更新到此版本。"]}]
        return format_update_notice(AGENT_VERSION, items)

    def _show_update_banner(self, text: str):
        self.update_banner_var.set(text)
        if not self.update_banner.winfo_manager():
            self.update_banner.pack(fill=tk.X, pady=(8, 0), before=self.btn_row)
            self._update_banner_btn.pack(anchor="e", pady=(2, 4), before=self.btn_row)

    def _dismiss_update_notice(self):
        self.settings["last_seen_version"] = AGENT_VERSION
        self._persist_settings()
        clear_pending_update()
        try:
            self.update_banner.pack_forget()
            self._update_banner_btn.pack_forget()
        except tk.TclError:
            pass
        self.update_banner_var.set("")

    def _maybe_show_update_notice(self):
        text = self._update_notice_text()
        if not text:
            if not str(self.settings.get("last_seen_version") or "").strip():
                self.settings["last_seen_version"] = AGENT_VERSION
                self._persist_settings()
            return
        for line in text.splitlines():
            if line.strip():
                self.log(line)
        if self._hidden:
            return
        self._show_update_banner(text)
        try:
            messagebox.showinfo("助手已更新", text, parent=self.root)
        except tk.TclError:
            return
        self._dismiss_update_notice()

    def show_update_notes(self):
        entries = parse_changelog(_read_changelog_text())
        pending = load_pending_update()
        if not entries and pending.get("changelog"):
            entries = pending.get("changelog")
        if not entries:
            notes = str(pending.get("notes") or "").strip()
            entries = [{
                "version": AGENT_VERSION,
                "notes": [notes] if notes else ["当前没有更多更新说明。"],
            }]
        text = format_update_notice(AGENT_VERSION, entries[:8])
        if self._hidden:
            self.show_window()
        try:
            messagebox.showinfo("更新说明", text, parent=self.root)
        except tk.TclError:
            self.log(text)

    def _notify(self, title: str, message: str, kind: str = "info"):
        if self._hidden:
            self.log(f"{title}: {message}")
            return
        if kind == "error":
            messagebox.showerror(title, message)
        else:
            messagebox.showinfo(title, message)

    def show_window(self):
        self._hidden = False
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._maybe_show_update_notice()
        self.root.after(200, self._prompt_real_name)

    def _startup_visible(self):
        self._maybe_show_update_notice()
        self._prompt_real_name()

    def _prompt_real_name(self):
        if self._hidden:
            return
        if looks_like_real_name(self._operator_name()):
            self._ensure_heartbeat()
            self._ensure_auto_sync()
            return
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.update_idletasks()
        except tk.TclError:
            return
        while True:
            try:
                name = simpledialog.askstring(
                    "请输入真实姓名",
                    "请填写您的真实姓名，用于服务器在线提醒。",
                    parent=self.root,
                    initialvalue=self._operator_name() if looks_like_real_name(self._operator_name()) else "",
                )
            except tk.TclError:
                return
            if name is None:
                if messagebox.askyesno(
                    "未填写真实姓名",
                    "未填写真实姓名将无法同步。是否退出应用？",
                    parent=self.root,
                ):
                    self.quit_app()
                    return
                continue
            name = name.strip()
            if not looks_like_real_name(name):
                messagebox.showwarning(
                    "姓名无效",
                    "请输入真实姓名（至少 2 个字），不要使用电脑登录名。",
                    parent=self.root,
                )
                continue
            self.name_var.set(name)
            self._persist_settings()
            self.log(f"已记录真实姓名：{name}")
            self.name_entry.focus_set()
            if self._hb_job is None:
                self._heartbeat_tick()
            self._ensure_auto_sync()
            return

    def complete_and_hide(self):
        if not self._ensure_name():
            return
        self.settings["setup_done"] = True
        self._persist_settings()
        try:
            enable_autostart()
            self.log("已登记开机自启动")
        except Exception as exc:
            self.log(f"开机自启动设置失败: {exc}")
        self.log("已转入后台静默运行。再次打开本程序可显示窗口。")
        self._hidden = True
        self.root.withdraw()
        self._heartbeat_now()

    def quit_app(self):
        self._cancel_auto()
        self._cancel_heartbeat()
        try:
            disable_autostart()
        except Exception as exc:
            self.log(f"取消开机自启动失败: {exc}")
        try:
            server = self.server_var.get().strip()
            token = self.token_var.get().strip()
            if server and token:
                send_offline(server, token, self._operator_name())
        except Exception:
            pass
        self.log("正在停止后台同步并退出")
        release_instance_lock()
        self.root.destroy()

    def _poll_show_flag(self):
        if os.path.isfile(SHOW_FLAG):
            try:
                os.remove(SHOW_FLAG)
            except OSError:
                pass
            self.show_window()
        self.root.after(1000, self._poll_show_flag)

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _set_all(self, value: bool):
        for var in self.var_by_id.values():
            var.set(value)
        self._persist_settings()

    def _set_kind(self, group: bool, value: bool):
        for item in self.conversations:
            if is_group_chat(item) == group:
                var = self.var_by_id.get(item["id"])
                if var is not None:
                    var.set(value)
        self._persist_settings()

    def _checked_ids(self):
        return [
            item["id"] for item in self.conversations
            if self.var_by_id.get(item["id"]) and self.var_by_id[item["id"]].get()
        ]

    def _operator_name(self) -> str:
        return (self.name_var.get() or "").strip()

    def _ensure_name(self) -> bool:
        if looks_like_real_name(self._operator_name()):
            return True
        self._prompt_real_name()
        return looks_like_real_name(self._operator_name())

    def _persist_settings(self):
        seen = {str(cid) for cid in (self.settings.get("seen_conversation_ids") or []) if cid}
        seen.update(str(item["id"]) for item in self.conversations if item.get("id"))
        if self.var_by_id:
            auto_sync_ids = self._checked_ids()
        else:
            auto_sync_ids = list(self.settings.get("auto_sync_ids") or [])
        data = {
            "server_url": self.server_var.get().strip() or DEFAULT_SERVER_URL,
            "token": self.token_var.get().strip(),
            "operator_name": self._operator_name(),
            "auto_sync": bool(self.auto_var.get()),
            "auto_sync_ids": auto_sync_ids,
            "seen_conversation_ids": sorted(seen),
            "setup_done": bool(self.settings.get("setup_done")),
            "last_seen_version": str(self.settings.get("last_seen_version") or ""),
            "sync_cursors": _cursor_map(self.settings.get("sync_cursors")),
        }
        self.settings = data
        _save_settings(data)

    def _on_auto_toggle(self):
        self._persist_settings()
        if self.auto_var.get():
            self.log("已开启每 5 分钟自动同步，只上传新消息")
            self.status_var.set("约 8 秒后开始自动同步")
            self._schedule_auto(8000)
        else:
            self._cancel_auto()
            self.status_var.set("自动同步已关闭")
            self.log("已关闭自动同步")

    def _cancel_auto(self):
        if self._auto_job is not None:
            self.root.after_cancel(self._auto_job)
            self._auto_job = None

    def _cancel_heartbeat(self):
        if self._hb_job is not None:
            self.root.after_cancel(self._hb_job)
            self._hb_job = None

    def _heartbeat_tick(self):
        self._heartbeat_now()
        self._hb_job = self.root.after(HEARTBEAT_INTERVAL_MS, self._heartbeat_tick)

    def _ensure_heartbeat(self):
        if self._hb_job is not None:
            return
        self._heartbeat_tick()

    def _ensure_auto_sync(self):
        if not self.auto_var.get() or self._auto_job is not None or self._busy:
            return
        self.log("已开启自动解密并同步全部群聊和单聊")
        self._schedule_auto(3000)

    def _heartbeat_now(self):
        server = self.server_var.get().strip()
        token = self.token_var.get().strip()
        if not server or not token:
            return
        name = self._operator_name()
        threading.Thread(
            target=self._heartbeat_worker,
            args=(server, token, name),
            daemon=True,
        ).start()

    def _heartbeat_worker(self, server, token, name):
        try:
            resp = send_heartbeat(server, token, name)
            self._hb_fail_logged = False
            jobs = (resp or {}).get("jobs") or []
            if jobs:
                self.root.after(0, lambda: self._dispatch_file_jobs(server, token, jobs))
            log_jobs = (resp or {}).get("log_jobs") or []
            if log_jobs:
                self.root.after(0, lambda: self._dispatch_file_jobs(server, token, log_jobs))
            info = (resp or {}).get("agent_update") or {}
            update_jobs = (resp or {}).get("update_jobs") or []
            if update_jobs:
                self.root.after(0, lambda: self._accept_update_jobs(server, token, info, update_jobs))
            sync_jobs = (resp or {}).get("sync_jobs") or []
            if sync_jobs:
                self.root.after(0, lambda jobs=sync_jobs: self._accept_sync_jobs(server, token, jobs))
            elif info:
                self.root.after(0, lambda: self._start_update(server, token, info))
        except Exception as exc:
            if not self._hb_fail_logged:
                self.root.after(0, lambda: self.log(f"在线心跳失败: {exc}"))
                self._hb_fail_logged = True

    def _accept_sync_jobs(self, server, token, jobs):
        self.log("收到立即同步")
        self._ack_update_jobs(server, token, jobs, "助手已开始同步")
        self._auto_cycle(force=True)

    def _accept_update_jobs(self, server, token, info, jobs):
        self._start_update(server, token, info, force=True, jobs=jobs)

    def _start_update(self, server, token, info, force=False, jobs=None):
        if self._updating:
            return
        if not is_frozen():
            if force:
                self.log("当前不是打包版助手，无法在线替换")
                self._ack_update_jobs(server, token, jobs, "当前不是打包版助手")
            return
        if not info or not str(info.get("sha256") or "").strip() or int(info.get("size") or 0) <= 0:
            if force:
                self.log("服务器尚未发布助手更新包")
                self._ack_update_jobs(server, token, jobs, "服务器尚未发布助手更新包")
            return
        current = current_exe_sha256()
        remote_sha = str(info.get("sha256") or "").strip().lower()
        if current and remote_sha == current.lower():
            if force:
                self.log(f"已是最新版本 {info.get('version')}，无需更新")
                self._ack_update_jobs(server, token, jobs, "已是最新版本")
            return
        if not force:
            if self._busy or self._active_jobs:
                return
            if time.time() < self._update_retry_after:
                return
            if not should_apply_update(AGENT_VERSION, info, current):
                return
        elif self._active_jobs:
            self.log("有文件任务进行中，稍后再推送更新")
            return
        self._updating = True
        self._ack_update_jobs(server, token, jobs, "助手已开始更新")
        remote = str(info.get("version") or "")
        self.log(f"{'收到推送，正在更新到' if force else '发现新版本'} {remote}，正在下载...")
        threading.Thread(
            target=self._update_worker,
            args=(server, token, info),
            daemon=True,
        ).start()

    def _ack_update_jobs(self, server, token, jobs, detail=""):
        for job in jobs or []:
            job_id = str(job.get("job_id") or "")
            if not job_id:
                continue
            try:
                report_file_job(server, token, job_id, "accepted", detail)
            except Exception:
                pass

    def _update_worker(self, server, token, info):
        try:
            dest_dir = os.path.join(WORK_DIR, "update")
            os.makedirs(dest_dir, exist_ok=True)
            new_path = os.path.join(dest_dir, "WeComSyncAgent.exe")
            url = resolve_update_url(server, info.get("url") or "/api/ingest/agent-exe")
            download_agent_exe(
                url,
                token,
                new_path,
                int(info.get("size") or 0),
                str(info.get("sha256") or ""),
            )
            extra = ["--background"]
            save_pending_update(info)
            try:
                send_heartbeat(server, token, self._operator_name(), status="updating")
            except Exception:
                pass
            apply_downloaded_update(new_path, app_executable(), extra)
            _append_log(f"新版本 {info.get('version')} 已就绪，正在重启助手")
            release_instance_lock()
            os._exit(0)
        except Exception as exc:
            self._updating = False
            self._update_retry_after = time.time() + UPDATE_RETRY_SEC
            self.root.after(0, lambda e=exc: self.log(f"自动更新失败: {e}"))

    def _exit_for_update(self):
        self._cancel_auto()
        self._cancel_heartbeat()
        release_instance_lock()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        os._exit(0)

    def _dispatch_file_jobs(self, server, token, jobs):
        queued = []
        with self._job_lock:
            for job in jobs or []:
                job_id = str(job.get("job_id") or "")
                if not job_id or job_id in self._active_jobs:
                    continue
                self._active_jobs.add(job_id)
                queued.append(job)
        if not queued:
            return
        names = [str(j.get("filename") or j.get("kind") or j.get("message_id") or "") for j in queued]
        if any(str(j.get("kind") or "") == "log" for j in queued):
            self.log("远端请求助手日志")
        else:
            self.log("远端请求文件: " + "、".join(names))
        threading.Thread(
            target=self._file_jobs_worker,
            args=(server, token, queued),
            daemon=True,
        ).start()

    def _file_jobs_worker(self, server, token, jobs):
        for job in jobs:
            job_id = str(job.get("job_id") or "")
            name = str(job.get("filename") or job.get("message_id") or job_id)
            try:
                result = process_file_job(server, token, job)
                kind = "日志" if str(job.get("kind") or "") == "log" else "文件"
                self.root.after(0, lambda n=name, r=result, k=kind: self.log(f"{k}回传完成: {n} ({r})"))
            except Exception as exc:
                try:
                    report_file_job(server, token, job_id, "error", str(exc))
                except Exception:
                    pass
                kind = "日志" if str(job.get("kind") or "") == "log" else "文件"
                self.root.after(0, lambda e=exc, n=name, k=kind: self.log(f"{k}回传失败: {n}: {e}"))
            finally:
                with self._job_lock:
                    self._active_jobs.discard(job_id)

    def _schedule_auto(self, delay_ms=None):
        self._cancel_auto()
        wait = AUTO_SYNC_INTERVAL_MS if delay_ms is None else delay_ms
        next_time = datetime.now() + timedelta(milliseconds=wait)
        self.status_var.set(f"下次自动同步 {next_time.strftime('%H:%M:%S')}")
        self._auto_job = self.root.after(wait, self._auto_cycle)

    def _auto_cycle(self, force=False):
        self._auto_job = None
        if not force and not self.auto_var.get():
            self.status_var.set("自动同步已关闭")
            return
        if self._busy:
            if force:
                self.log("上一次同步尚未结束，立即同步已跳过")
            else:
                self.log("上一次任务尚未结束，5 分钟后重试")
                self._schedule_auto()
            return
        server = self.server_var.get().strip()
        token = self.token_var.get().strip()
        checked = self._checked_ids() or list(self.settings.get("auto_sync_ids") or [])
        seen = list(self.settings.get("seen_conversation_ids") or [])
        self._busy = True
        self.decrypt_btn.state(["disabled"])
        self.sync_btn.state(["disabled"])
        threading.Thread(
            target=self._auto_worker,
            args=(server, token, checked, seen, self._operator_name()),
            daemon=True,
        ).start()

    def _auto_worker(self, server, token, checked_ids, seen_ids=None, operator_name=""):
        try:
            if not server:
                raise RuntimeError("未填写服务器地址")
            if not token:
                info = fetch_ingest_info(server)
                token = str(info.get("token") or "").strip()
                if token:
                    self.root.after(0, lambda: self.token_var.set(token))
            if not token:
                raise RuntimeError("尚未获取到同步令牌")

            if _wxwork_running():
                self.root.after(0, lambda: self.log("自动同步：正在解密聊天记录..."))
                _run_decrypt()
                self.root.after(0, lambda: self.log("自动同步：解密完成，开始解析"))
            else:
                self.root.after(0, lambda: self.log("自动同步：企业微信未运行，跳过解密，同步已有数据"))

            conversations, users, room_nicks = list_conversations()
            selected = sessions_for_auto_sync(conversations, checked_ids, seen_ids)
            if not selected:
                self.root.after(0, lambda: self.log("自动同步：当前没有可上传的聊天记录，已跳过"))
                return
            cursors = _cursor_map(self.settings.get("sync_cursors"))
            cursors = seed_cursors(
                server, token, cursors, operator_name,
                lambda m: self.root.after(0, lambda msg=m: self.log(msg)),
            )
            self.settings["sync_cursors"] = cursors
            result = push_sessions(
                server, token, selected, users,
                lambda m: self.root.after(0, lambda msg=m: self.log(msg)),
                operator_name=operator_name,
                room_nicks=room_nicks,
                cursors=cursors,
            )
            self.settings["sync_cursors"] = result.get("cursors") or cursors
            msg = (
                f"自动同步完成：{result.get('saved_sessions')} 个会话，"
                f"{result.get('new_messages') or 0} 条新消息 -> {server}"
            )
            self.root.after(0, lambda: self.log(msg))
            self.root.after(0, lambda: self._refresh_after_auto(conversations, users, room_nicks))
        except error.URLError as exc:
            self.root.after(0, lambda e=exc: self.log(f"自动同步失败，无法连接服务器: {e}"))
        except Exception as exc:
            self.root.after(0, lambda e=exc: self.log(f"自动同步失败: {e}"))
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            self.root.after(0, lambda e=exc: self.log(f"自动同步失败: {e}"))
        finally:
            self.root.after(0, self._finish_auto)

    def _refresh_after_auto(self, conversations, users, room_nicks=None):
        self.conversations = conversations
        self.users = users
        self.room_nicks = room_nicks or {}
        self._render_list()

    def _finish_auto(self):
        self._busy = False
        self.decrypt_btn.state(["!disabled"])
        self.sync_btn.state(["!disabled"])
        self._persist_settings()
        if self.auto_var.get():
            self._schedule_auto()

    def _add_section(self, title: str, items: list, saved: set):
        box = ttk.LabelFrame(self.list_frame, text=f"{title}（{len(items)}）")
        box.pack(fill=tk.X, padx=4, pady=6, anchor="n")
        if not items:
            ttk.Label(box, text="无").pack(anchor="w", padx=8, pady=4)
            return
        for item in items:
            var = tk.BooleanVar(value=str(item["id"]) in saved)
            self.var_by_id[item["id"]] = var
            label = f"{item['display_name']}    {item['msg_count']}条    {item['last_time']}"
            ttk.Checkbutton(
                box,
                text=label,
                variable=var,
                command=self._persist_settings,
            ).pack(anchor="w", padx=8)

    def _render_list(self):
        checked = set(checked_ids_for_render(
            self.conversations,
            self.settings.get("auto_sync_ids") or [],
            self.settings.get("seen_conversation_ids") or [],
        ))
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.var_by_id = {}
        groups, singles = split_conversations(self.conversations)
        self._add_section("群聊", groups, checked)
        self._add_section("单聊", singles, checked)

    def reload_conversations(self):
        try:
            self.conversations, self.users, self.room_nicks = list_conversations()
            groups, singles = split_conversations(self.conversations)
            self._render_list()
            self._persist_settings()
            self.log(f"共 {len(self.conversations)} 个有消息的会话：群聊 {len(groups)}，单聊 {len(singles)}")
        except Exception as exc:
            self.log(f"加载会话失败: {exc}")

    def fetch_token(self, _event=None):
        server = self.server_var.get().strip()
        if not server:
            return
        threading.Thread(target=self._fetch_token_worker, args=(server,), daemon=True).start()

    def _fetch_token_worker(self, server: str):
        try:
            info = fetch_ingest_info(server)
            token = str(info.get("token") or "").strip()

            def apply():
                if token:
                    self.token_var.set(token)
                    self._persist_settings()
                    self.log(f"已自动获取同步令牌，服务器 {server}")
                else:
                    self.log("服务器未返回同步令牌")

            self.root.after(0, apply)
        except Exception as exc:
            self.root.after(0, lambda: self.log(f"自动获取令牌失败: {exc}"))

    def start_decrypt(self):
        if self._busy:
            self._notify("进行中", "正在自动同步，请稍后再试。")
            return
        if not _wxwork_running():
            self._notify("未运行", "请先启动并登录企业微信，再解密。", "error")
            return
        self._busy = True
        self.decrypt_btn.state(["disabled"])
        threading.Thread(target=self._decrypt_worker, daemon=True).start()

    def _decrypt_worker(self):
        try:
            self.root.after(0, lambda: self.log("正在从企业微信进程提取密钥并解密..."))
            _run_decrypt()
            self.root.after(0, lambda: self.log("解密完成"))
            self.root.after(0, self.reload_conversations)
        except Exception as exc:
            self.root.after(0, lambda e=exc: self.log(f"解密失败: {e}"))
            self.root.after(0, lambda e=exc: self._notify("解密失败", str(e), "error"))
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            self.root.after(0, lambda e=exc: self.log(f"解密失败: {e}"))
        finally:
            self.root.after(0, self._finish_manual)

    def start_sync(self):
        if self._busy:
            self._notify("进行中", "正在自动同步，请稍后再试。")
            return
        selected = [
            item for item in self.conversations
            if self.var_by_id.get(item["id"]) and self.var_by_id[item["id"]].get()
        ]
        if not selected:
            self._notify("未选择", "请先勾选要同步的聊天。")
            return
        if not self._ensure_name():
            return
        server = self.server_var.get().strip()
        token = self.token_var.get().strip()
        if not server:
            self._notify("缺少配置", "请填写服务器地址。", "error")
            return
        if not token:
            self._notify("缺少令牌", "尚未获取到同步令牌，请确认服务器已启动后点「重新获取」。", "error")
            return
        self._persist_settings()
        self._busy = True
        self.sync_btn.state(["disabled"])
        name = self._operator_name()
        threading.Thread(
            target=self._sync_worker,
            args=(server, token, selected, name),
            daemon=True,
        ).start()

    def _sync_worker(self, server, token, selected, operator_name=""):
        try:
            self.root.after(0, lambda: self.log(f"开始增量同步 {len(selected)} 个会话 -> {server}"))
            cursors = _cursor_map(self.settings.get("sync_cursors"))
            cursors = seed_cursors(
                server, token, cursors, operator_name,
                lambda m: self.root.after(0, lambda msg=m: self.log(msg)),
            )
            self.settings["sync_cursors"] = cursors
            result = push_sessions(
                server, token, selected, self.users,
                lambda m: self.root.after(0, lambda msg=m: self.log(msg)),
                operator_name=operator_name,
                room_nicks=self.room_nicks,
                cursors=cursors,
            )
            self.settings["sync_cursors"] = result.get("cursors") or cursors
            msg = (
                f"已同步到服务器「{result.get('computer_name')}」，"
                f"会话 {result.get('saved_sessions')} 个，"
                f"新消息 {result.get('new_messages') or 0} 条"
            )
            self.root.after(0, lambda: self.log(msg))
            self.root.after(0, lambda: self._notify("同步完成", msg))
        except error.URLError as exc:
            self.root.after(0, lambda: self.log(f"无法连接服务器: {exc}"))
            self.root.after(0, lambda: self._notify(
                "连接失败",
                "请确认服务器 start.ps1 已启动，地址填写局域网 IP，且防火墙放行 8767 端口。",
                "error",
            ))
        except Exception as exc:
            self.root.after(0, lambda: self.log(f"同步失败: {exc}"))
            self.root.after(0, lambda: self._notify("同步失败", str(exc), "error"))
        finally:
            self.root.after(0, self._finish_manual)

    def _finish_manual(self):
        self._busy = False
        self.decrypt_btn.state(["!disabled"])
        self.sync_btn.state(["!disabled"])


def main():
    if request_existing_instance_to_show():
        return
    if not acquire_instance_lock():
        request_existing_instance_to_show()
        return
    background = "--background" in sys.argv
    root = tk.Tk()
    if background:
        root.withdraw()
        root.update_idletasks()
    AgentApp(root, start_hidden=background)
    try:
        root.mainloop()
    finally:
        release_instance_lock()


if __name__ == "__main__":
    main()
