"""In-memory online presence and sync alerts for remote WeCom agents."""
import json
import os
from collections import deque
from datetime import datetime
from threading import Lock
from typing import Callable, Optional


ONLINE_TTL_SEC = 90
UPDATING_TTL_SEC = 3 * 60
MAX_ALERTS = 50
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def display_who(operator_name: str = "", computer_name: str = "") -> str:
    return (operator_name or "").strip() or (computer_name or "").strip() or "远端助手"


class PresenceHub:
    def __init__(
        self,
        now: Optional[Callable[[], datetime]] = None,
        on_alert: Optional[Callable[[dict], None]] = None,
        persist_path: str = "",
    ):
        self._now = now or datetime.now
        self.on_alert = on_alert
        self._persist_path = persist_path or ""
        self._lock = Lock()
        self._clients: dict = {}
        self._alerts: deque = deque(maxlen=MAX_ALERTS)
        self._seq = 0
        self._load()

    def _stamp(self) -> str:
        return self._now().strftime(TIME_FMT)

    def _load(self) -> None:
        path = self._persist_path
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return
        clients = data.get("clients") if isinstance(data, dict) else None
        if isinstance(clients, dict):
            self._clients = {
                str(key): dict(value)
                for key, value in clients.items()
                if isinstance(value, dict)
            }
        try:
            self._seq = int(data.get("seq") or 0)
        except (TypeError, ValueError):
            self._seq = 0

    def _save_unlocked(self) -> None:
        path = self._persist_path
        if not path:
            return
        try:
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = path + ".tmp"
            payload = {"clients": self._clients, "seq": self._seq}
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError:
            pass

    def _parse(self, text: str) -> datetime:
        try:
            return datetime.strptime(text, TIME_FMT)
        except (TypeError, ValueError):
            return datetime.min

    def _fresh(self, last_seen: str, now: datetime, status: str = "") -> bool:
        ttl = UPDATING_TTL_SEC if status == "updating" else ONLINE_TTL_SEC
        return (now - self._parse(last_seen)).total_seconds() <= ttl

    def _push_alert(self, kind: str, title: str, message: str, extra: Optional[dict] = None) -> dict:
        self._seq += 1
        item = {
            "id": self._seq,
            "kind": kind,
            "title": title,
            "message": message,
            "time": self._stamp(),
            **(extra or {}),
        }
        self._alerts.append(item)
        cb = self.on_alert
        if cb:
            try:
                cb(item)
            except Exception:
                pass
        return item

    def heartbeat(
        self,
        source_id: str,
        operator_name: str = "",
        computer_name: str = "",
        host: str = "",
        status: str = "online",
        agent_version: str = "",
    ) -> dict:
        source_id = str(source_id or "").strip() or "pc"
        who = display_who(operator_name, computer_name)
        status = str(status or "online").strip() or "online"
        if status not in ("online", "updating"):
            status = "online"
        with self._lock:
            now = self._now()
            prev = self._clients.get(source_id)
            was_online = bool(prev) and self._fresh(
                prev.get("last_seen") or "",
                now,
                prev.get("status") or "",
            )
            version = (agent_version or "").strip() or ((prev or {}).get("agent_version") or "")
            self._clients[source_id] = {
                "source_id": source_id,
                "operator_name": (operator_name or "").strip(),
                "computer_name": (computer_name or "").strip(),
                "host": (host or "").strip(),
                "last_seen": now.strftime(TIME_FMT),
                "status": status,
                "agent_version": version,
            }
            alert = None
            if not was_online:
                alert = self._push_alert(
                    "online",
                    f"{who} 已上线",
                    f"{who}（{computer_name or source_id}）正在连接服务器",
                    {"source_id": source_id},
                )
            self._save_unlocked()
        return {"ok": True, "online": True, "alert": alert}

    def mark_updating_by_host(self, host: str) -> int:
        host = (host or "").strip()
        if not host:
            return 0
        changed = 0
        with self._lock:
            now = self._now().strftime(TIME_FMT)
            for client in self._clients.values():
                if (client.get("host") or "") != host:
                    continue
                client["status"] = "updating"
                client["last_seen"] = now
                changed += 1
            if changed:
                self._save_unlocked()
        return changed

    def mark_offline(self, source_id: str) -> dict:
        source_id = str(source_id or "").strip()
        with self._lock:
            prev = self._clients.pop(source_id, None)
            if not prev:
                return {"ok": True, "online": False}
            who = display_who(prev.get("operator_name") or "", prev.get("computer_name") or "")
            alert = self._push_alert(
                "offline",
                f"{who} 已离线",
                f"{who} 已停止同步",
                {"source_id": source_id},
            )
            self._save_unlocked()
        return {"ok": True, "online": False, "alert": alert}

    def note_sync(
        self,
        source_id: str,
        operator_name: str = "",
        computer_name: str = "",
        session_names: Optional[list] = None,
        saved_sessions: int = 0,
        host: str = "",
    ) -> dict:
        names = [str(n).strip() for n in (session_names or []) if str(n).strip()]
        shown = names[:5]
        extra = f" 等{len(names)}个" if len(names) > 5 else ""
        label = "、".join(shown) + extra
        who = display_who(operator_name, computer_name)
        message = f"{who} 同步了 {int(saved_sessions or 0)} 个会话"
        if label:
            message += f"：{label}"
        self.heartbeat(source_id, operator_name, computer_name, host)
        with self._lock:
            alert = self._push_alert(
                "sync",
                f"{who} 已同步",
                message,
                {"source_id": source_id, "saved_sessions": int(saved_sessions or 0)},
            )
        return alert

    def snapshot(self, since_id: int = 0) -> dict:
        with self._lock:
            now = self._now()
            online = []
            for source_id, client in self._clients.items():
                if self._fresh(client.get("last_seen") or "", now, client.get("status") or ""):
                    online.append(dict(client))
            alerts = [dict(item) for item in self._alerts if int(item.get("id") or 0) > int(since_id or 0)]
        online.sort(key=lambda c: c.get("last_seen") or "", reverse=True)
        return {"online": online, "alerts": alerts}

    def known_clients(self) -> list:
        with self._lock:
            clients = [dict(item) for item in self._clients.values()]
        clients.sort(key=lambda c: c.get("last_seen") or "", reverse=True)
        return clients
