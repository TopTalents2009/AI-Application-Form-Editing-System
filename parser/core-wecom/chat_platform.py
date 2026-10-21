"""WeCom (Enterprise WeChat) platform implementation."""
import os
import sqlite3
import json
from datetime import datetime
from typing import List, Optional
from collections import defaultdict

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util

from shared.platform_base import BasePlatform, ChatMessage, ChatSession, Contact
from shared.wecom_ssh import get_live_session, load_live_manifest
from shared.wxwork_paths import append_wxwork_base as _append_wxwork_base
from shared.wxwork_paths import collect_wxwork_base_dirs as _get_wxwork_base_dirs

_decode_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "message_decode.py")
_decode_spec = importlib.util.spec_from_file_location("core_wecom_message_decode", _decode_path)
_decode_mod = importlib.util.module_from_spec(_decode_spec)
_decode_spec.loader.exec_module(_decode_mod)
decode_content = _decode_mod.decode_content
build_display_fields = _decode_mod.build_display_fields
message_type_name = _decode_mod.message_type_name
_FILE_EXT_RE = _decode_mod._FILE_EXT_RE

_attach_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attachments.py")
_attach_spec = importlib.util.spec_from_file_location("core_wecom_attachments", _attach_path)
_attach_mod = importlib.util.module_from_spec(_attach_spec)
_attach_spec.loader.exec_module(_attach_mod)
WeComAttachmentResolver = _attach_mod.WeComAttachmentResolver
ATTACHMENT_CONTENT_TYPES = _attach_mod.IMAGE_CONTENT_TYPES | _attach_mod.FILE_CONTENT_TYPES


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECRYPTED_DIR = os.path.join(_PROJECT_ROOT, "export", "wxwork_decrypted")
_SQLITE_HEADER = b"SQLite format 3\x00"


def _agent_decrypted_dir() -> str:
    """Decrypted copy maintained by the sync agent on this PC."""
    appdata = os.environ.get("APPDATA") or ""
    if not appdata:
        return ""
    return os.path.join(appdata, "WeComSyncAgent", "wxwork_decrypted")


def _has_plain_message_db(folder: str) -> bool:
    if not folder:
        return False
    path = os.path.join(folder, "message.db")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == _SQLITE_HEADER
    except OSError:
        return False


def _infer_self_user_id(conversation_ids) -> Optional[int]:
    """The account that appears in every direct chat is the local user.

    A chat with yourself is stored as S:<id>_<id>. Count that id once, or it
    looks more common than the number of chats and the match fails.
    """
    counts = {}
    session_n = 0
    for cid in conversation_ids or []:
        text = str(cid or "")
        if not text.startswith("S:"):
            continue
        ids = [int(part) for part in text[2:].split("_") if part.isdigit()]
        if len(ids) < 2:
            continue
        session_n += 1
        for uid in set(ids):
            counts[uid] = counts.get(uid, 0) + 1
    for uid, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        if session_n >= 2 and count == session_n:
            return uid
    return None


def _newest_local_decrypted_dir() -> Optional[str]:
    """Pick the decrypted folder whose message.db was written most recently."""
    best = ""
    best_mtime = -1.0
    for folder in (DECRYPTED_DIR, _agent_decrypted_dir()):
        if not _has_plain_message_db(folder):
            continue
        try:
            mtime = os.path.getmtime(os.path.join(folder, "message.db"))
        except OSError:
            continue
        if mtime > best_mtime:
            best = folder
            best_mtime = mtime
    return best or None


def _collect_data_dirs(base_dir: str) -> list:
    """Find Data directories containing message.db under a WXWork root."""
    candidates = []
    seen = set()
    if not os.path.isdir(base_dir):
        return candidates

    def add(data_dir: str) -> None:
        if not os.path.isdir(data_dir):
            return
        if not os.path.exists(os.path.join(data_dir, "message.db")):
            return
        key = os.path.normcase(os.path.abspath(data_dir))
        if key not in seen:
            seen.add(key)
            candidates.append(data_dir)

    add(base_dir)
    for uid_dir in os.listdir(base_dir):
        uid_path = os.path.join(base_dir, uid_dir)
        if not os.path.isdir(uid_path) or not uid_dir.isdigit():
            continue
        add(uid_path)
        add(os.path.join(uid_path, "Data"))
        for version_dir in os.listdir(uid_path):
            if version_dir == "Data":
                continue
            add(os.path.join(uid_path, version_dir, "Data"))
    return candidates


def _latest_wxwork_data_dir() -> Optional[str]:
    candidates = []
    seen = set()
    for base_dir in _get_wxwork_base_dirs():
        for data_dir in _collect_data_dirs(base_dir):
            key = os.path.normcase(os.path.abspath(data_dir))
            if key not in seen:
                seen.add(key)
                candidates.append(data_dir)
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _looks_like_encrypted_dir(data_dir: str) -> bool:
    """Check whether the WeCom data directory still contains encrypted databases."""
    if not data_dir or not os.path.isdir(data_dir):
        return False
    for name in ("message.db", "session.db", "user.db"):
        path = os.path.join(data_dir, name)
        if os.path.exists(path):
            with open(path, "rb") as f:
                header = f.read(16)
            if header == b"SQLite format 3\x00":
                return False
            return True
    return False


class WeComPlatform(BasePlatform):
    name = "wecom"
    display_name = "企业微信"

    def __init__(self, data_dir: str = None, **kwargs):
        super().__init__(data_dir=data_dir, **kwargs)
        self._decrypted_dir = DECRYPTED_DIR
        self._conn = {}
        self._contacts_cache = {}
        self._user_map = {}
        self._room_nicks = {}
        self._inferred_self_id = None
        self._account_roots = None
        self._attachment_resolver = None
        self._live = False
        self._conn_mtime = {}
        if self.data_dir is None:
            self.data_dir = self.detect_data_dir()
        live = load_live_manifest()
        if live and self.data_dir:
            self._live = os.path.normcase(self.data_dir) == os.path.normcase(
                live.get("decrypted_dir") or ""
            )
        if not self._live and _has_plain_message_db(self.data_dir or ""):
            self._decrypted_dir = self.data_dir

    def detect_data_dir(self) -> Optional[str]:
        """Auto-detect WeCom data directory.

        Prefer a live SSH session (data stays on the remote PC), then the
        newest local decrypted database, then the original encrypted directory.
        The sync agent keeps a fresher copy than export/wxwork_decrypted.
        """
        live = load_live_manifest()
        if live and live.get("decrypted_dir"):
            return live["decrypted_dir"]

        decrypted = _newest_local_decrypted_dir()
        if decrypted:
            return decrypted

        return _latest_wxwork_data_dir()

    def _get_account_roots(self) -> list:
        if self._account_roots is not None:
            return self._account_roots
        roots = []
        seen = set()
        for base_dir in _get_wxwork_base_dirs():
            if not os.path.isdir(base_dir):
                continue
            for uid_dir in os.listdir(base_dir):
                if not uid_dir.isdigit():
                    continue
                uid_path = os.path.join(base_dir, uid_dir)
                cache_dir = os.path.join(uid_path, "Cache")
                if os.path.isdir(cache_dir) and uid_path not in seen:
                    seen.add(uid_path)
                    roots.append(uid_path)

        remote_root = os.path.join(_PROJECT_ROOT, "export", "wxwork_remote")
        if os.path.isdir(remote_root):
            for uid_dir in os.listdir(remote_root):
                uid_path = os.path.join(remote_root, uid_dir)
                cache_dir = os.path.join(uid_path, "Cache")
                if os.path.isdir(cache_dir) and uid_path not in seen:
                    seen.add(uid_path)
                    roots.append(uid_path)

        self._account_roots = roots
        return self._account_roots

    def _ensure_attachment_resolver(self):
        if self._attachment_resolver is None:
            file_db = os.path.join(self._decrypted_dir, "file.db")
            finder = None
            if self._live:
                session = get_live_session()
                file_db = session.fetch_db("file.db") if session else ""

                def finder(name, md5, _session=session):
                    if not _session:
                        return ""
                    return _session.find_cache_file(name, md5)

            self._attachment_resolver = WeComAttachmentResolver(
                file_db, self._get_account_roots(), finder=finder
            )
        return self._attachment_resolver

    def get_attachment_file(self, message_id: int, filename_hint: str = "") -> str:
        resolver = self._ensure_attachment_resolver()
        path = resolver.resolve_file(int(message_id or 0), filename_hint=filename_hint)
        if path and os.path.isfile(path):
            return path
        if path and self._live:
            session = get_live_session()
            if session:
                return session.fetch_attachment(path)
        return ""

    @staticmethod
    def _filename_hint(text: str) -> str:
        if not text:
            return ""
        if text.startswith("[") and "]" in text:
            return text.split("]", 1)[1].strip()
        if _FILE_EXT_RE.search(text):
            return text.strip()
        return ""

    def _open_db(self, name: str):
        if self._live:
            if name in self._conn:
                return self._conn[name]
            session = get_live_session()
            if not session:
                return None
            try:
                db_path = session.fetch_db(name)
            except Exception:
                return None
        else:
            db_path = os.path.join(self._decrypted_dir, name)
            if not os.path.exists(db_path):
                return None
            cached = self._conn.get(name)
            try:
                mtime = os.path.getmtime(db_path)
            except OSError:
                mtime = None
            if cached is not None and self._conn_mtime.get(name) == mtime:
                return cached
            if cached is not None:
                cached.close()
                self._conn.pop(name, None)
            self._conn[name] = sqlite3.connect(db_path)
            self._conn_mtime[name] = mtime
            return self._conn[name]
        self._conn[name] = sqlite3.connect(db_path)
        return self._conn[name]

    def _table_exists(self, conn, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    def _load_user_map(self):
        if self._user_map:
            return
        conn = self._open_db("user.db")
        if not conn:
            return
        try:
            if self._table_exists(conn, "user_table"):
                for row in conn.execute(
                    "SELECT id, name, real_name, account FROM user_table"
                ).fetchall():
                    uid, name, real_name, account = row
                    display = real_name or name or account or ""
                    if display:
                        self._user_map[int(uid)] = display
            if self._table_exists(conn, "external_user_relation_v3"):
                for row in conn.execute(
                    "SELECT user_id, remarks, real_remarks, corp_remark FROM external_user_relation_v3"
                ).fetchall():
                    uid, remarks, real_remarks, corp_remark = row
                    display = real_remarks or remarks or corp_remark or ""
                    if display:
                        self._user_map[int(uid)] = display
        except Exception:
            pass
        self._load_room_nicknames()

    def _load_room_nicknames(self):
        conn = self._open_db("session.db")
        if not conn or not self._table_exists(conn, "conversation_member_nickname_table"):
            return
        try:
            for room_id, userid, nickname in conn.execute(
                "SELECT room_id, userid, nickname FROM conversation_member_nickname_table"
            ):
                if nickname and userid is not None and room_id is not None:
                    self._room_nicks[(int(room_id), int(userid))] = nickname
        except Exception:
            pass

    def _resolve_sender(self, sender_id, conversation_id: str = "") -> str:
        uid = None
        if isinstance(sender_id, int) or (isinstance(sender_id, str) and str(sender_id).isdigit()):
            uid = int(sender_id)
        if uid in (None, 0):
            return "系统" if uid == 0 else ""
        if conversation_id.startswith("R:"):
            tail = conversation_id[2:]
            if tail.isdigit():
                nick = self._room_nicks.get((int(tail), uid))
                if nick:
                    return nick
        return self._user_map.get(uid, str(uid))

    def _name_from_conversation_id(self, conversation_id: str) -> str:
        if not conversation_id:
            return ""
        if conversation_id.startswith("S:"):
            ids = []
            for value in conversation_id[2:].split("_"):
                if value.isdigit():
                    ids.append(int(value))
            other_ids = [uid for uid in ids if uid != self._self_id]
            for uid in other_ids or ids:
                if uid in self._user_map:
                    return self._user_map[uid]
        if ":" in conversation_id:
            tail = conversation_id.split(":", 1)[1]
            if tail.isdigit() and int(tail) in self._user_map:
                return self._user_map[int(tail)]
        return conversation_id

    def _message_counts_and_last_times(self):
        counts = defaultdict(int)
        last_times = defaultdict(int)
        conn = self._open_db("message.db")
        if not conn:
            return counts, last_times
        try:
            for table in ("message_table", "message_small_table", "kf_message_tableV1"):
                if not self._table_exists(conn, table):
                    continue
                rows = conn.execute(
                    f'SELECT conversation_id, COUNT(*) AS c, MAX(send_time) AS t FROM "{table}" GROUP BY conversation_id'
                ).fetchall()
                for cid, c, t in rows:
                    if not cid:
                        continue
                    counts[cid] += int(c or 0)
                    last_times[cid] = max(last_times[cid], int(t or 0))
        except Exception:
            pass
        return counts, last_times

    @property
    def _self_id(self) -> Optional[int]:
        if not self.data_dir:
            return self._inferred_self_id
        parts = os.path.normpath(self.data_dir).split(os.sep)
        for part in reversed(parts):
            if part.isdigit() and len(part) >= 10:
                return int(part)
        if self._inferred_self_id:
            return self._inferred_self_id
        session_db = self._open_db("session.db")
        cids = []
        if session_db and self._table_exists(session_db, "conversation_table"):
            cids = [
                row[0]
                for row in session_db.execute("SELECT id FROM conversation_table")
            ]
        inferred = _infer_self_user_id(cids)
        if inferred:
            self._inferred_self_id = inferred
        return inferred

    def list_sessions(self, limit: int = 100) -> List[ChatSession]:
        """List WeCom chat sessions from decrypted databases."""
        result = []

        # If the detected directory is still encrypted, show a helpful placeholder.
        if not self._live and _looks_like_encrypted_dir(self.data_dir):
            result.append(ChatSession(
                username="__need_decrypt__",
                display_name="企业微信数据尚未解密",
                summary="请先运行解密工具：python tools/wechat-decrypt/decrypt_wxwork_db.py --key <key>",
                last_time="",
                session_type=0,
            ))
            return result

        self._load_user_map()
        counts, message_last_times = self._message_counts_and_last_times()

        conversations = {}
        session_db = self._open_db("session.db")
        if session_db:
            try:
                if self._table_exists(session_db, "conversation_table"):
                    rows = session_db.execute(
                        "SELECT id, name, roomname_remark, last_message_time, last_message_id FROM conversation_table"
                    ).fetchall()
                    for cid, name, roomname_remark, last_msg_time, last_msg_id in rows:
                        if not cid:
                            continue
                        display = roomname_remark or name or self._name_from_conversation_id(cid)
                        last_time = max(int(last_msg_time or 0), message_last_times.get(cid, 0))
                        conversations[cid] = ChatSession(
                            username=cid,
                            display_name=display,
                            session_type=2 if cid.startswith("R:") else 1,
                            msg_count=counts.get(cid, 0),
                            last_time=datetime.fromtimestamp(last_time).strftime("%Y-%m-%d %H:%M") if last_time else "",
                            summary="",
                        )
            except Exception:
                pass

        # Merge any conversations that only appear in message counts.
        for cid, count in counts.items():
            if cid in conversations:
                sessions = conversations[cid]
                sessions.msg_count = count
                t = message_last_times.get(cid, 0)
                if t and not sessions.last_time:
                    sessions.last_time = datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
                continue
            display = self._name_from_conversation_id(cid)
            last_time = message_last_times.get(cid, 0)
            conversations[cid] = ChatSession(
                username=cid,
                display_name=display,
                session_type=2 if cid.startswith("R:") else 1,
                msg_count=count,
                last_time=datetime.fromtimestamp(last_time).strftime("%Y-%m-%d %H:%M") if last_time else "",
                summary="",
            )

        result = [c for c in conversations.values() if c.msg_count > 0]
        result.sort(key=lambda c: c.last_time, reverse=True)
        return result[:limit]

    def query_messages(self, session_id: str, start_time: datetime = None,
                       end_time: datetime = None, limit: int = 500) -> List[ChatMessage]:
        """Query WeCom messages for a conversation."""
        result = []
        conn = self._open_db("message.db")
        if not conn:
            return result

        self._load_user_map()
        try:
            for table in ("message_table", "message_small_table", "kf_message_tableV1"):
                if not self._table_exists(conn, table):
                    continue
                cols = [d[0] for d in conn.execute(f'SELECT * FROM "{table}" LIMIT 1').description]
                query = f'SELECT * FROM "{table}" WHERE conversation_id = ?'
                params = [session_id]
                if start_time:
                    query += " AND send_time >= ?"
                    params.append(int(start_time.timestamp()))
                if end_time:
                    query += " AND send_time <= ?"
                    params.append(int(end_time.timestamp()))
                query += " ORDER BY send_time DESC LIMIT ?"
                params.append(limit)

                rows = conn.execute(query, tuple(params)).fetchall()
                for row in rows:
                    m = dict(zip(cols, row))
                    ts = m.get("send_time")
                    dt = datetime.fromtimestamp(ts) if ts else None
                    sender_raw = m.get("sender_id")
                    if sender_raw in (None, ""):
                        sender_raw = m.get("sender")
                    sender = self._resolve_sender(sender_raw, session_id)
                    sender_id = sender_raw if sender_raw not in (None, "") else ""
                    content_type = int(m.get("content_type") or 0)
                    message_id = int(m.get("message_id") or m.get("id") or 0)
                    text, media_url, attachment_name = build_display_fields(
                        content_type,
                        m.get("content"),
                        m.get("extra_content") or "",
                        m.get("local_extra_content") or "",
                    )
                    filename_hint = attachment_name or self._filename_hint(text)
                    result.append(ChatMessage(
                        time=dt,
                        time_text=dt.strftime("%Y-%m-%d %H:%M") if dt else "",
                        hour=dt.hour if dt else None,
                        sender=sender,
                        sender_id=str(sender_id),
                        text=text,
                        msg_type=content_type or m.get("msg_type", 0),
                        msg_type_label=message_type_name(content_type),
                        chatroom=session_id,
                        message_id=message_id,
                        has_attachment=content_type in ATTACHMENT_CONTENT_TYPES or bool(media_url or filename_hint),
                        attachment_name=filename_hint,
                        media_url=media_url,
                        raw=m,
                    ))
        except Exception:
            pass

        result.sort(key=lambda m: m.time or datetime.min, reverse=True)
        return result[:limit]

    def get_contacts(self) -> List[Contact]:
        """Get WeCom contacts."""
        result = []
        self._load_user_map()
        for uid, name in self._user_map.items():
            result.append(Contact(user_id=str(uid), nickname=name, remark=""))
        return result

    def get_contact_name(self, user_id: str, session_id: str = None) -> str:
        if not user_id:
            return ""
        self._load_user_map()
        if user_id.isdigit() and int(user_id) in self._user_map:
            return self._user_map[int(user_id)]
        return user_id

    def close(self):
        for conn in self._conn.values():
            conn.close()
        self._conn.clear()
