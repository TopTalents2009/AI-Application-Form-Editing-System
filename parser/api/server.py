"""FastAPI server for chat analysis."""
import os
import socket
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse
import urllib.request
from typing import Optional, List
from collections import Counter

from fastapi import Depends, FastAPI, Query, HTTPException, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add project root to path
_api_dir = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(_api_dir)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Import platform implementations (hyphenated names need importlib)
import importlib
_wechat_mod = importlib.import_module('core-wechat.chat_platform')
_wecom_mod = importlib.import_module('core-wecom.chat_platform')
_dingtalk_mod = importlib.import_module('core-dingtalk.chat_platform')
from shared import agent_logs, agent_update, api_keys, file_jobs, lan, message_search, sync_now, synced_store
from shared.presence import PresenceHub
from shared.wecom_ssh import load_live_manifest, strip_jsonc
import json

WeChatPlatform = _wechat_mod.WeChatPlatform
WeComPlatform = _wecom_mod.WeComPlatform
DingTalkPlatform = _dingtalk_mod.DingTalkPlatform

PLATFORMS = {
    "wechat": WeChatPlatform,
    "wecom": WeComPlatform,
    "dingtalk": DingTalkPlatform,
}

def get_platform(name, **kwargs):
    cls = PLATFORMS.get(name)
    if not cls:
        raise ValueError(f"Unknown platform: {name}")
    return cls(**kwargs)

app = FastAPI(title="Chat Analysis API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SessionResponse(BaseModel):
    username: str
    display_name: str
    session_type: int = 0
    summary: str = ""
    last_time: str = ""
    unread: int = 0
    msg_count: int = 0
    synced_at: str = ""


class MessageResponse(BaseModel):
    time_text: str = ""
    sender: str = ""
    sender_id: str = ""
    text: str = ""
    msg_type: int = 0
    msg_type_label: str = ""
    hour: Optional[int] = None
    message_id: int = 0
    has_attachment: bool = False
    attachment_name: str = ""
    media_url: str = ""


def _message_response(m) -> MessageResponse:
    if isinstance(m, dict):
        return MessageResponse(
            time_text=m.get("time_text") or "",
            sender=m.get("sender") or "",
            sender_id=m.get("sender_id") or "",
            text=m.get("text") or "",
            msg_type=int(m.get("msg_type") or 0),
            msg_type_label=m.get("msg_type_label") or "",
            hour=m.get("hour"),
            message_id=int(m.get("message_id") or 0),
            has_attachment=bool(m.get("has_attachment") or m.get("media_url")),
            attachment_name=m.get("attachment_name") or "",
            media_url=m.get("media_url") or "",
        )
    return MessageResponse(
        time_text=m.time_text,
        sender=m.sender,
        sender_id=m.sender_id,
        text=m.text,
        msg_type=m.msg_type,
        msg_type_label=m.msg_type_label,
        hour=m.hour,
        message_id=m.message_id,
        has_attachment=m.has_attachment or bool(getattr(m, "media_url", "")),
        attachment_name=m.attachment_name,
        media_url=getattr(m, "media_url", "") or "",
    )


class ContactResponse(BaseModel):
    user_id: str
    nickname: str
    remark: str


class StatsResponse(BaseModel):
    total_messages: int = 0
    unique_senders: int = 0
    sender_stats: dict = {}
    hourly_distribution: dict = {}
    msg_type_distribution: dict = {}
    time_range: dict = {}
    top_senders: list = []
    activity_timeline: list = []


class PlatformInfo(BaseModel):
    name: str
    display_name: str
    detected: bool = False
    data_dir: str = ""


class SourceInfo(BaseModel):
    id: str
    kind: str = "remote"
    computer_name: str
    operator_name: str = ""
    username: str = ""
    account_id: str = ""
    host: str = ""
    last_sync: str = ""
    session_count: int = 0
    platform: str = "wecom"
    agent_version: str = ""
    update_available: bool = False


class AgentUpdateInfo(BaseModel):
    version: str
    sha256: str = ""
    size: int = 0
    url: str = "/api/ingest/agent-exe"
    download_url: str = "/api/agent-exe"
    notes: str = ""
    changelog: list = []
    published: bool = False


class SearchHit(BaseModel):
    source_id: str
    source_name: str = ""
    session_id: str
    session_name: str = ""
    time_text: str = ""
    sender: str = ""
    sender_id: str = ""
    text: str = ""
    snippet: str = ""
    message_id: int = 0
    msg_type: int = 0
    attachment_name: str = ""
    media_url: str = ""


class ReadApiKeyInfo(BaseModel):
    name: str = "default"
    key: str = ""


class ReadApiInfo(BaseModel):
    enabled: bool = True
    key: str = ""
    keys: List[ReadApiKeyInfo] = []
    urls: List[str] = []
    example: str = ""


class IngestInfo(BaseModel):
    token: str
    port: int = 8767
    urls: List[str] = []
    web_port: int = 5173
    web_urls: List[str] = []
    read_api: Optional[ReadApiInfo] = None
    agent_update: Optional[AgentUpdateInfo] = None
    latest_agent_version: str = ""


_platforms_cache = {}
_INGEST_TOKEN_FILE = os.path.join(PROJECT_DIR, "export", "ingest_token.txt")


def _load_ingest_token() -> str:
    cfg_path = os.path.join(PROJECT_DIR, "config.jsonc")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.loads(strip_jsonc(f.read()))
        token = ((cfg.get("wecom_remote") or {}).get("ingest_token") or "").strip()
        if token:
            return token
    env = os.environ.get("WECOM_INGEST_TOKEN", "").strip()
    if env:
        return env
    if os.path.exists(_INGEST_TOKEN_FILE):
        return open(_INGEST_TOKEN_FILE, encoding="utf-8").read().strip()
    os.makedirs(os.path.dirname(_INGEST_TOKEN_FILE), exist_ok=True)
    token = uuid.uuid4().hex
    with open(_INGEST_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def _lan_urls(port: int = 8767) -> List[str]:
    return lan.lan_urls(port)


def _check_ingest_token(payload_token: str = "", header_token: str = ""):
    expected = _load_ingest_token()
    got = (header_token or payload_token or "").strip()
    if not expected or got != expected:
        raise HTTPException(status_code=401, detail="Invalid ingest token")


def require_read_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
    key: str = Query("", alias="key"),
) -> dict:
    if not api_keys.is_enabled():
        raise HTTPException(status_code=403, detail="Open API is disabled")
    token = api_keys.extract_token(x_api_key or "", authorization or "", key)
    found = api_keys.verify(token)
    if not found:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return found


def _v1_ok(data, **meta):
    payload = {"ok": True, "data": jsonable_encoder(data)}
    payload.update(meta)
    return payload


def _message_day(item) -> str:
    if isinstance(item, dict):
        text = item.get("time_text") or ""
    else:
        text = getattr(item, "time_text", "") or ""
    return str(text)[:10]


def _filter_v1_messages(rows, start_date: Optional[str], end_date: Optional[str],
                        offset: int, limit: int):
    filtered = []
    for item in rows:
        day = _message_day(item)
        if start_date and day and day < start_date:
            continue
        if end_date and day and day > end_date:
            continue
        filtered.append(item)
    sliced = filtered[offset:offset + limit]
    return sliced, len(filtered)


def _show_windows_balloon(title: str, message: str) -> None:
    if os.name != "nt":
        return
    safe_title = "".join(ch for ch in (title or "ChatInsight") if ch not in "'\"`")[:40]
    safe_msg = "".join(ch for ch in (message or "") if ch not in "'\"`")[:120]
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip(6000, '{safe_title}', '{safe_msg}', "
        "[System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Seconds 7;"
        "$n.Dispose();"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass


def _desktop_alert(alert: dict) -> None:
    if (alert or {}).get("kind") == "offline":
        return
    threading.Thread(
        target=_show_windows_balloon,
        args=(alert.get("title") or "ChatInsight", alert.get("message") or ""),
        daemon=True,
    ).start()


presence = PresenceHub(
    on_alert=_desktop_alert,
    persist_path=os.path.join(PROJECT_DIR, "export", "presence.json"),
)


def _client_source_id(payload: dict) -> str:
    computer_name = (payload.get("computer_name") or socket.gethostname()).strip()
    account_id = str(payload.get("account_id") or "")
    host = str(payload.get("host") or "")
    return synced_store.find_source_id(computer_name, account_id, host)


def _peer_host(request: Request, payload: Optional[dict] = None) -> str:
    peer = request.client.host if request.client else ""
    reported = str((payload or {}).get("host") or "")
    if peer and peer not in ("127.0.0.1", "::1"):
        return peer
    return reported or peer


def _bare_computer(name: str) -> str:
    return (name or "").replace("（本机）", "").replace("（SSH）", "").strip()


def _local_ip_set() -> set:
    return set(lan.list_lan_ipv4())


def _same_machine(computer: str, host: str, other_computer: str, other_host: str) -> bool:
    left = _bare_computer(computer)
    right = _bare_computer(other_computer)
    if left and right and left.lower() == right.lower():
        return True
    if host and other_host and host == other_host:
        return True
    return False


def _is_this_pc(computer: str, host: str, local: Optional[dict], local_ips: Optional[set] = None) -> bool:
    if not local:
        return False
    if _same_machine(computer, host, local.get("computer_name") or "", local.get("host") or ""):
        return True
    ips = local_ips if local_ips is not None else _local_ip_set()
    return bool(host) and host in ips


def _enrich_local_source(local: dict, clients: list) -> dict:
    ips = _local_ip_set()
    for client in clients:
        computer = str(client.get("computer_name") or "")
        host = str(client.get("host") or "")
        if not _is_this_pc(computer, host, local, ips):
            continue
        name = str(client.get("operator_name") or "").strip()
        if name:
            local["operator_name"] = name
            local["username"] = name
        if host:
            local["host"] = host
        version = str(client.get("agent_version") or "").strip()
        if version:
            local["agent_version"] = version
        break
    return local


def _match_presence_client(
    source_id: str = "",
    computer: str = "",
    host: str = "",
    operator_name: str = "",
    clients: Optional[list] = None,
) -> Optional[dict]:
    items = clients if clients is not None else presence.known_clients()
    source_id = str(source_id or "").strip()
    computer = _bare_computer(computer)
    host = str(host or "").strip()
    operator_name = str(operator_name or "").strip()
    for client in items:
        cid = str(client.get("source_id") or "").strip()
        if source_id and cid and source_id == cid:
            return client
    for client in items:
        if _same_machine(
            computer,
            host,
            str(client.get("computer_name") or ""),
            str(client.get("host") or ""),
        ):
            return client
    if operator_name:
        for client in items:
            if str(client.get("operator_name") or "").strip() == operator_name:
                return client
    return None


def _attach_agent_fields(items: List[SourceInfo]) -> List[SourceInfo]:
    known = presence.known_clients()
    latest = agent_update.read_version(PROJECT_DIR) or (
        (_agent_update_payload() or {}).get("version") or ""
    )
    for item in items:
        client = _match_presence_client(
            item.id, item.computer_name, item.host, item.operator_name, known,
        )
        if not client:
            continue
        version = str(client.get("agent_version") or "").strip()
        if version:
            item.agent_version = version
        if latest and item.agent_version:
            item.update_available = agent_update.version_newer(latest, item.agent_version)
    return items


def _resolve_agent_source_id(source_id: str) -> str:
    source_id = str(source_id or "").strip()
    if not source_id:
        raise HTTPException(status_code=400, detail="source_id is required")
    if source_id == "local":
        local = _local_source()
        known = presence.known_clients()
        local_ips = _local_ip_set()
        for client in known:
            computer = str(client.get("computer_name") or "")
            host = str(client.get("host") or "")
            if _is_this_pc(computer, host, local, local_ips):
                return str(client.get("source_id") or source_id)
        return source_id
    client = _match_presence_client(source_id)
    if client and client.get("source_id"):
        return str(client["source_id"])
    return source_id


def _agent_is_online(source_id: str) -> bool:
    wanted = str(source_id or "").strip()
    if not wanted:
        return False
    for item in presence.snapshot().get("online") or []:
        if str(item.get("source_id") or "") == wanted:
            return True
    return False


def _log_payload(source_id: str, job: Optional[dict] = None) -> dict:
    path = ""
    status = "missing"
    detail = ""
    job_id = ""
    updated = 0.0
    if job:
        status = str(job.get("status") or "")
        detail = str(job.get("detail") or "")
        job_id = str(job.get("job_id") or "")
        path = str(job.get("path") or "")
        updated = float(job.get("updated") or 0)
    if not path:
        path = agent_logs.stored_path(source_id)
        if path and status in ("", "missing"):
            status = "ready"
    text = agent_logs.read_log_text(path) if path else ""
    return {
        "ok": True,
        "source_id": source_id,
        "job_id": job_id,
        "status": status or "missing",
        "detail": detail,
        "text": text,
        "size": len(text.encode("utf-8")) if text else 0,
        "updated": updated,
        "filename": f"{source_id}-agent.log",
    }


def _local_source() -> Optional[dict]:
    try:
        plat = WeComPlatform()
        data_dir = plat.detect_data_dir()
        if not data_dir:
            return None
        live = load_live_manifest()
        sessions = []
        encrypted = False
        try:
            from importlib import import_module
            wecom = import_module("core-wecom.chat_platform")
            encrypted = wecom._looks_like_encrypted_dir(data_dir)
        except Exception:
            encrypted = False
        if not encrypted:
            sessions = plat.list_sessions(limit=5000)
        computer = socket.gethostname()
        kind = "local"
        host = ""
        if live and os.path.normcase(data_dir) == os.path.normcase(live.get("decrypted_dir") or ""):
            kind = "ssh"
            host = live.get("host") or ""
            computer = f"{host or computer}（SSH）"
        else:
            computer = f"{computer}（本机）"
        return {
            "id": "local",
            "kind": kind,
            "computer_name": computer,
            "operator_name": "",
            "username": "",
            "account_id": "",
            "host": host,
            "last_sync": "直连",
            "session_count": len(sessions),
            "platform": "wecom",
        }
    except Exception:
        return None


def _get_platform(name: str):
    if name not in _platforms_cache:
        try:
            plat = get_platform(name)
            data_dir = plat.detect_data_dir()
            if data_dir:
                plat.data_dir = data_dir
            _platforms_cache[name] = plat
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    return _platforms_cache[name]


@app.get("/api/platforms")
async def list_platforms():
    result = []
    for name, cls in PLATFORMS.items():
        try:
            plat = get_platform(name)
            data_dir = plat.detect_data_dir()
            result.append(PlatformInfo(
                name=name,
                display_name=cls.display_name,
                detected=data_dir is not None,
                data_dir=data_dir or "",
            ))
        except Exception:
            result.append(PlatformInfo(name=name, display_name=cls.display_name))
    return result


def _agent_update_payload() -> Optional[dict]:
    return agent_update.build_manifest(PROJECT_DIR)


@app.get("/api/ingest/info")
async def ingest_info():
    latest = agent_update.latest_info(PROJECT_DIR)
    api_urls = _lan_urls(8767)
    read_api = ReadApiInfo(**api_keys.homepage_info(api_urls))
    return IngestInfo(
        token=_load_ingest_token(),
        port=8767,
        urls=api_urls,
        web_port=5173,
        web_urls=_lan_urls(5173),
        read_api=read_api,
        agent_update=AgentUpdateInfo(**latest) if latest.get("version") else None,
        latest_agent_version=str(latest.get("version") or ""),
    )


@app.get("/api/sources")
async def list_sources():
    items = []
    seen = set()
    known = presence.known_clients()
    local_ips = _local_ip_set()
    local = _local_source()
    if local:
        _enrich_local_source(local, known)
        items.append(SourceInfo(**local))
        seen.add(local["id"])
    for src in synced_store.list_sources():
        if _is_this_pc(src.get("computer_name") or "", src.get("host") or "", local, local_ips):
            seen.add(src["id"])
            continue
        items.append(SourceInfo(**src))
        seen.add(src["id"])
    online_ids = {
        str(item.get("source_id") or "")
        for item in (presence.snapshot().get("online") or [])
    }
    for client in known:
        sid = str(client.get("source_id") or "").strip()
        computer = str(client.get("computer_name") or "").strip()
        host = str(client.get("host") or "").strip()
        if sid and sid in seen:
            continue
        if _is_this_pc(computer, host, local, local_ips):
            continue
        already = False
        for item in items:
            if _same_machine(computer, host, item.computer_name, item.host):
                already = True
                break
        if already:
            continue
        if not sid:
            sid = synced_store.source_id_for(computer or "pc", "")
        last_seen = str(client.get("last_seen") or "").strip()
        if sid in online_ids:
            last_sync = "在线，尚未同步聊天"
        elif last_seen:
            last_sync = f"{last_seen} 曾在线"
        else:
            last_sync = "尚未同步聊天"
        items.insert(0 if not local else 1, SourceInfo(
            id=sid,
            kind="remote",
            computer_name=computer or sid,
            operator_name=str(client.get("operator_name") or ""),
            username=str(client.get("operator_name") or ""),
            host=host,
            last_sync=last_sync,
            session_count=0,
            platform="wecom",
            agent_version=str(client.get("agent_version") or ""),
        ))
        seen.add(sid)
    return _attach_agent_fields(items)


@app.post("/api/ingest/cursors")
async def ingest_cursors(payload: dict, x_ingest_token: Optional[str] = Header(None)):
    _check_ingest_token(str(payload.get("token") or ""), x_ingest_token or "")
    source_id = _client_source_id(payload)
    if not synced_store.get_source(source_id):
        return {"ok": True, "source_id": source_id, "cursors": {}}
    return {
        "ok": True,
        "source_id": source_id,
        "cursors": synced_store.message_cursors(source_id),
    }


@app.post("/api/ingest/wecom")
async def ingest_wecom(payload: dict, x_ingest_token: Optional[str] = Header(None)):
    _check_ingest_token(str(payload.get("token") or ""), x_ingest_token or "")
    try:
        result = synced_store.save_ingest(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    names = [
        item.get("display_name") or item.get("id") or item.get("username") or ""
        for item in (payload.get("sessions") or [])
    ]
    presence.note_sync(
        result["source_id"],
        payload.get("operator_name") or result.get("operator_name") or "",
        result.get("computer_name") or "",
        names,
        result.get("saved_sessions") or 0,
        payload.get("host") or "",
    )
    return {"ok": True, **result}


@app.post("/api/ingest/heartbeat")
async def ingest_heartbeat(
    payload: dict,
    request: Request,
    x_ingest_token: Optional[str] = Header(None),
):
    _check_ingest_token(str(payload.get("token") or ""), x_ingest_token or "")
    source_id = _client_source_id(payload)
    result = presence.heartbeat(
        source_id,
        payload.get("operator_name") or "",
        payload.get("computer_name") or "",
        _peer_host(request, payload),
        str(payload.get("status") or "online"),
        str(payload.get("agent_version") or ""),
    )
    result["source_id"] = source_id
    result["jobs"] = file_jobs.hub.pending_for(source_id)
    result["log_jobs"] = agent_logs.hub.pending_for(source_id)
    result["update_jobs"] = agent_update.push_hub.pending_for(source_id)
    result["sync_jobs"] = sync_now.hub.pending_for(source_id)
    result["agent_update"] = _agent_update_payload()
    return result


@app.post("/api/ingest/file")
async def ingest_file(
    request: Request,
    x_ingest_token: Optional[str] = Header(None),
    x_job_id: Optional[str] = Header(None),
    x_filename: Optional[str] = Header(None),
):
    _check_ingest_token("", x_ingest_token or "")
    job_id = (x_job_id or "").strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="X-Job-Id is required")
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    if length > file_jobs.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    data = await request.body()
    filename = unquote(x_filename or "")
    try:
        job = file_jobs.hub.save_bytes(job_id, filename, data)
    except KeyError:
        try:
            job = agent_logs.hub.save_bytes(job_id, filename, data)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
    except ValueError as exc:
        code = 413 if "large" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc))
    return {"ok": True, "job_id": job_id, "status": job["status"]}


@app.post("/api/ingest/file-result")
async def ingest_file_result(payload: dict, x_ingest_token: Optional[str] = Header(None)):
    _check_ingest_token(str(payload.get("token") or ""), x_ingest_token or "")
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")
    status = str(payload.get("status") or "").strip()
    detail = str(payload.get("detail") or "")
    try:
        if status == "missing":
            try:
                job = file_jobs.hub.mark_missing(job_id, detail)
            except KeyError:
                job = agent_logs.hub.mark_missing(job_id, detail)
        elif status == "accepted":
            try:
                job = agent_update.push_hub.mark_accepted(job_id, detail)
            except KeyError:
                job = sync_now.hub.mark_accepted(job_id, detail)
        elif status == "error":
            try:
                job = file_jobs.hub.mark_error(job_id, detail)
            except KeyError:
                try:
                    job = agent_logs.hub.mark_error(job_id, detail)
                except KeyError:
                    job = agent_update.push_hub.mark_error(job_id, detail)
        else:
            raise HTTPException(status_code=400, detail="unsupported status")
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, "job_id": job_id, "status": job["status"]}


@app.post("/api/ingest/offline")
async def ingest_offline(payload: dict, x_ingest_token: Optional[str] = Header(None)):
    _check_ingest_token(str(payload.get("token") or ""), x_ingest_token or "")
    return presence.mark_offline(_client_source_id(payload))


def _agent_exe_file(*, mark_host: str = "") -> FileResponse:
    path = agent_update.agent_exe_path(PROJECT_DIR)
    if not path:
        raise HTTPException(status_code=404, detail="尚未打包助手安装包，请在分析机运行 parser\\build_agent.bat")
    if mark_host:
        presence.mark_updating_by_host(mark_host)
    version = agent_update.read_version(PROJECT_DIR) or "latest"
    safe_ver = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in version)
    return FileResponse(
        path,
        filename="WeComSyncAgent-" + safe_ver + ".exe",
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/ingest/agent-update")
async def ingest_agent_update():
    update = _agent_update_payload()
    if not update:
        raise HTTPException(status_code=404, detail="agent exe not published")
    return update


@app.get("/api/agent-exe")
def download_agent_exe():
    """解析控制台下载最新助手，发给其它电脑安装。不校验同步令牌。"""
    return _agent_exe_file()


@app.get("/api/ingest/agent-exe")
def ingest_agent_exe(
    request: Request,
    x_ingest_token: Optional[str] = Header(None),
    token: str = Query(""),
):
    _check_ingest_token(token, x_ingest_token or "")
    host = request.client.host if request.client else ""
    return _agent_exe_file(mark_host=host)


@app.get("/api/presence")
async def get_presence(since: int = Query(0, ge=0)):
    return presence.snapshot(since_id=since)


@app.post("/api/sources/{source_id}/agent-log")
async def request_agent_log(source_id: str):
    sid = _resolve_agent_source_id(source_id)
    if not _agent_is_online(sid):
        raise HTTPException(status_code=409, detail="助手离线，无法回传日志")
    try:
        job = agent_logs.hub.request(sid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "source_id": sid,
        "job_id": job["job_id"],
        "status": job["status"],
        "detail": "正在等待助手回传日志",
    }


@app.post("/api/sources/{source_id}/sync-now")
async def request_sync_now(source_id: str):
    sid = _resolve_agent_source_id(source_id)
    if not _agent_is_online(sid):
        raise HTTPException(status_code=409, detail="助手离线，无法立即同步")
    try:
        job = sync_now.hub.request(sid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "source_id": sid,
        "job_id": job["job_id"],
        "status": job["status"],
        "detail": "已通知助手立即同步，下一次心跳后开始",
    }


@app.post("/api/sources/{source_id}/agent-update")
async def request_agent_update(source_id: str):
    sid = _resolve_agent_source_id(source_id)
    if not _agent_is_online(sid):
        raise HTTPException(status_code=409, detail="助手离线，无法推送更新")
    for item in presence.snapshot().get("online") or []:
        if str(item.get("source_id") or "") == sid and str(item.get("status") or "") == "updating":
            raise HTTPException(status_code=409, detail="助手正在更新")
    manifest = _agent_update_payload()
    if not manifest:
        raise HTTPException(status_code=409, detail="尚未打包助手更新包，请先运行 build_agent.bat")
    try:
        job = agent_update.push_hub.request(sid, str(manifest.get("version") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "ok": True,
        "source_id": sid,
        "job_id": job["job_id"],
        "status": job["status"],
        "version": manifest.get("version") or "",
        "detail": f"已通知助手更新到 {manifest.get('version')}",
    }


@app.get("/api/sources/{source_id}/agent-log")
async def get_agent_log(source_id: str, job_id: str = Query("")):
    sid = _resolve_agent_source_id(source_id)
    job = agent_logs.hub.get(job_id) if job_id else None
    if job_id and not job:
        raise HTTPException(status_code=404, detail="日志任务不存在")
    if job and job.get("status") == "pending":
        return JSONResponse(status_code=202, content=_log_payload(sid, job))
    if job and job.get("status") == "error":
        raise HTTPException(status_code=409, detail=job.get("detail") or "回传失败")
    if job and job.get("status") == "missing":
        raise HTTPException(status_code=404, detail=job.get("detail") or "助手尚未产生日志")
    payload = _log_payload(sid, job)
    if payload["status"] != "ready" or not payload["text"]:
        if job and job.get("status") == "ready":
            return payload
        raise HTTPException(status_code=404, detail="还没有回传过日志")
    return payload


@app.get("/api/sources/{source_id}/agent-log/file")
def download_agent_log(source_id: str):
    sid = _resolve_agent_source_id(source_id)
    path = agent_logs.stored_path(sid)
    if not path:
        raise HTTPException(status_code=404, detail="还没有回传过日志")
    return _file_response(path, f"{sid}-agent.log")


def _local_platform_or_none():
    try:
        plat = _get_platform("wecom")
        if plat.detect_data_dir():
            return plat
    except Exception:
        return None
    return None


@app.get("/api/search")
async def search_messages(
    q: str = Query(..., min_length=1, max_length=80),
    source_id: str = Query(""),
    session_id: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    local = None
    if source_id in ("", "local"):
        local = _local_platform_or_none()
    hits = message_search.search(
        q,
        source_id=source_id,
        session_id=session_id,
        limit=limit,
        local_platform=local,
    )
    return [SearchHit(**item) for item in hits]


@app.get("/api/sources/{source_id}/search")
async def search_source_messages(
    source_id: str,
    q: str = Query(..., min_length=1, max_length=80),
    session_id: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    return await search_messages(q, source_id, session_id, limit)


@app.get("/api/sources/{source_id}/sessions")
async def list_source_sessions(source_id: str, limit: int = Query(1000, ge=1, le=5000)):
    if source_id == "local":
        plat = _get_platform("wecom")
        sessions = plat.list_sessions(limit=limit)
        return [SessionResponse(
            username=s.username,
            display_name=s.display_name,
            session_type=s.session_type,
            summary=s.summary,
            last_time=s.last_time,
            unread=s.unread,
            msg_count=s.msg_count,
        ) for s in sessions]
    if not synced_store.get_source(source_id):
        raise HTTPException(status_code=404, detail="Unknown computer")
    sessions = synced_store.list_sessions(source_id)[:limit]
    return [SessionResponse(
        username=s.get("username") or "",
        display_name=s.get("display_name") or "",
        session_type=int(s.get("session_type") or 0),
        summary=s.get("summary") or "",
        last_time=s.get("last_time") or "",
        msg_count=int(s.get("msg_count") or 0),
        synced_at=s.get("synced_at") or "",
    ) for s in sessions]


@app.get("/api/sources/{source_id}/messages/{session_id}")
async def get_source_messages(
    source_id: str,
    session_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(5000, ge=1, le=20000),
):
    if source_id == "local":
        plat = _get_platform("wecom")
        start_time = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end_time = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1) if end_date else None
        messages = plat.query_messages(session_id, start_time=start_time, end_time=end_time, limit=limit)
        # query_messages is newest-first; the page reads oldest-first and scrolls to the end.
        return [_message_response(m) for m in reversed(list(messages))]
    if not synced_store.get_source(source_id):
        raise HTTPException(status_code=404, detail="Unknown computer")
    fetch_all = bool(start_date or end_date)
    rows = synced_store.list_messages(source_id, session_id, limit=0 if fetch_all else limit)
    rows = synced_store.filter_messages_by_date(rows, start_date or "", end_date or "")
    if limit and len(rows) > int(limit):
        rows = rows[-int(limit):]
    return [_message_response(m) for m in rows]


@app.get("/v1/health")
async def v1_health():
    return {"ok": True, "service": "chatinsight", "read_api": api_keys.is_enabled()}


@app.get("/v1/info")
async def v1_info(auth: dict = Depends(require_read_api_key)):
    return _v1_ok({
        "key_name": auth.get("name") or "key",
        "endpoints": [
            "GET /v1/health",
            "GET /v1/info",
            "GET /v1/sources",
            "GET /v1/sources/{source_id}/sessions",
            "GET /v1/sources/{source_id}/messages/{session_id}",
            "GET /v1/search?q=",
        ],
    })


@app.get("/v1/sources")
async def v1_sources(_auth: dict = Depends(require_read_api_key)):
    items = await list_sources()
    return _v1_ok(items, count=len(items))


@app.get("/v1/sources/{source_id}/sessions")
async def v1_source_sessions(
    source_id: str,
    limit: int = Query(1000, ge=1, le=5000),
    _auth: dict = Depends(require_read_api_key),
):
    items = await list_source_sessions(source_id, limit)
    return _v1_ok(items, count=len(items), source_id=source_id)


@app.get("/v1/sources/{source_id}/messages/{session_id}")
async def v1_source_messages(
    source_id: str,
    session_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(200, ge=1, le=1000),
    _auth: dict = Depends(require_read_api_key),
):
    fetch_limit = 20000 if (start_date or end_date or offset) else limit
    rows = await get_source_messages(source_id, session_id, start_date, end_date, fetch_limit)
    data, total = _filter_v1_messages(rows, start_date, end_date, offset, limit)
    return _v1_ok(
        data,
        count=len(data),
        total=total,
        offset=offset,
        limit=limit,
        source_id=source_id,
        session_id=session_id,
    )


@app.get("/v1/search")
async def v1_search(
    q: str = Query(..., min_length=1, max_length=80),
    source_id: str = Query(""),
    session_id: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    _auth: dict = Depends(require_read_api_key),
):
    items = await search_messages(q, source_id, session_id, limit)
    return _v1_ok(items, count=len(items), q=q)


@app.get("/api/sources/{source_id}/stats/{session_id}")
async def get_source_stats(
    source_id: str,
    session_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    if source_id == "local":
        return await get_stats("wecom", session_id, start_date, end_date)
    messages = await get_source_messages(source_id, session_id, start_date, end_date, 5000)
    if not messages:
        return StatsResponse()
    sender_counter = Counter(m.sender for m in messages if m.sender)
    hour_counter = Counter(m.hour for m in messages if m.hour is not None)
    type_counter = Counter(m.msg_type for m in messages)
    date_counter = Counter()
    for m in messages:
        day = (m.time_text or "")[:10]
        if len(day) == 10 and day[4] == "-":
            date_counter[day] += 1
    return StatsResponse(
        total_messages=len(messages),
        unique_senders=len(sender_counter),
        sender_stats=dict(sender_counter),
        hourly_distribution={str(h): c for h, c in sorted(hour_counter.items())},
        msg_type_distribution={str(t): c for t, c in type_counter.items()},
        time_range={},
        top_senders=[{"name": name, "count": count} for name, count in sender_counter.most_common(20)],
        activity_timeline=[{"date": d, "count": c} for d, c in sorted(date_counter.items())],
    )


@app.get("/api/{platform}/sessions")
async def list_sessions(platform: str, limit: int = Query(1000, ge=1, le=5000)):
    plat = _get_platform(platform)
    sessions = plat.list_sessions(limit=limit)
    return [SessionResponse(
        username=s.username,
        display_name=s.display_name,
        session_type=s.session_type,
        summary=s.summary,
        last_time=s.last_time,
        unread=s.unread,
        msg_count=s.msg_count,
    ) for s in sessions]


@app.get("/api/{platform}/messages/{session_id}")
async def get_messages(
    platform: str,
    session_id: str,
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(500, ge=1, le=5000),
):
    plat = _get_platform(platform)
    start_time = None
    end_time = None
    if start_date:
        start_time = datetime.strptime(start_date, "%Y-%m-%d")
    if end_date:
        end_time = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    messages = plat.query_messages(session_id, start_time=start_time,
                                   end_time=end_time, limit=limit)
    return [_message_response(m) for m in messages]


@app.get("/api/{platform}/contacts")
async def get_contacts(platform: str):
    plat = _get_platform(platform)
    contacts = plat.get_contacts()
    return [ContactResponse(
        user_id=c.user_id,
        nickname=c.nickname,
        remark=c.remark,
    ) for c in contacts]


@app.get("/api/{platform}/stats/{session_id}")
async def get_stats(
    platform: str,
    session_id: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    plat = _get_platform(platform)
    start_time = None
    end_time = None
    if start_date:
        start_time = datetime.strptime(start_date, "%Y-%m-%d")
    if end_date:
        end_time = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    messages = plat.query_messages(session_id, start_time=start_time,
                                   end_time=end_time, limit=5000)

    if not messages:
        return StatsResponse()

    sender_counter = Counter(m.sender for m in messages if m.sender)
    hour_counter = Counter(m.hour for m in messages if m.hour is not None)
    type_counter = Counter(m.msg_type for m in messages)

    top_senders = sender_counter.most_common(20)

    time_range = {}
    if messages:
        times = [m.time for m in messages if m.time]
        if times:
            time_range = {
                "start": min(times).isoformat(),
                "end": max(times).isoformat(),
            }

    activity_timeline = []
    date_counter = Counter()
    for m in messages:
        if m.time:
            date_key = m.time.strftime("%Y-%m-%d")
            date_counter[date_key] += 1
    for date_key in sorted(date_counter.keys()):
        activity_timeline.append({"date": date_key, "count": date_counter[date_key]})

    return StatsResponse(
        total_messages=len(messages),
        unique_senders=len(sender_counter),
        sender_stats=dict(sender_counter),
        hourly_distribution={str(h): c for h, c in sorted(hour_counter.items())},
        msg_type_distribution={str(t): c for t, c in type_counter.items()},
        time_range=time_range,
        top_senders=[{"name": name, "count": count} for name, count in top_senders],
        activity_timeline=activity_timeline,
    )


@app.get("/api/{platform}/groups")
async def list_groups(platform: str):
    plat = _get_platform(platform)
    sessions = plat.list_sessions(limit=5000)
    groups = [s for s in sessions if "@chatroom" in s.username or s.session_type == 2]
    return [SessionResponse(
        username=s.username,
        display_name=s.display_name,
        session_type=s.session_type,
        msg_count=s.msg_count,
    ) for s in groups]


_MEDIA_HOST_SUFFIXES = (
    ".qpic.cn",
    "qpic.cn",
    ".qlogo.cn",
    "qlogo.cn",
    ".weixin.qq.com",
    "weixin.qq.com",
)
_MEDIA_MAX_BYTES = 12 * 1024 * 1024


def _allowed_media_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in _MEDIA_HOST_SUFFIXES)


def _file_response(path: str, filename: str = ""):
    return FileResponse(
        path,
        filename=filename or os.path.basename(path),
        content_disposition_type="attachment",
    )


@app.get("/api/sources/{source_id}/attachments/{message_id}")
def download_source_attachment(
    source_id: str,
    message_id: int,
    session_id: str = Query(""),
):
    filename = ""
    plat = _get_platform("wecom")
    getter = getattr(plat, "get_attachment_file", None)
    if not getter:
        raise HTTPException(status_code=404, detail="This platform does not support attachments")
    if source_id != "local":
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        item = synced_store.get_message(source_id, session_id, message_id)
        if not item:
            raise HTTPException(status_code=404, detail="Message not found")
        filename = item.get("attachment_name") or ""
        cached = file_jobs.stored_path(source_id, message_id, filename)
        if cached:
            return _file_response(cached, filename)
        # 远端消息 id 对不上本机缓存，不要先扫本地盘，否则会一直停在「正在查缓存」
        try:
            job = file_jobs.hub.request(source_id, message_id, session_id, filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if job.get("status") == "ready" and job.get("path") and os.path.isfile(job["path"]):
            return _file_response(job["path"], filename or job.get("filename") or "")
        if job.get("status") == "missing":
            raise HTTPException(status_code=404, detail=job.get("detail") or "对方电脑未缓存该文件")
        if job.get("status") == "error":
            raise HTTPException(status_code=409, detail=job.get("detail") or "拉取失败")
        return JSONResponse(
            status_code=202,
            content={
                "status": job.get("status") or "pending",
                "job_id": job.get("job_id") or "",
                "detail": "正在等待远端助手回传文件",
            },
        )
    try:
        path = getter(int(message_id), filename_hint=filename)
    except TypeError:
        path = getter(int(message_id))
    if path and os.path.isfile(path):
        return _file_response(path, filename)
    raise HTTPException(status_code=404, detail="文件不在本机缓存，查看聊天时不会自动下载")


@app.get("/api/media/proxy")
def proxy_media(url: str = Query(..., min_length=8, max_length=2000)):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not _allowed_media_host(parsed.hostname or ""):
        raise HTTPException(status_code=400, detail="Unsupported image host")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            content_type = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=415, detail="Remote content is not an image")
            data = resp.read(_MEDIA_MAX_BYTES + 1)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to fetch image")
    if len(data) > _MEDIA_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image too large")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/{platform}/attachments/{message_id}")
async def get_attachment(platform: str, message_id: int):
    plat = _get_platform(platform)
    getter = getattr(plat, "get_attachment_file", None)
    if not getter:
        raise HTTPException(status_code=404, detail="This platform does not support attachments")
    path = getter(int(message_id))
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Attachment not found on this PC or remote Cache")
    return FileResponse(path, filename=os.path.basename(path))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
