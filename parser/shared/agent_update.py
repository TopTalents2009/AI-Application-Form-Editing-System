"""Publish the packaged WeCom sync agent for client self-update."""
import hashlib
import os
import re
import threading
import time
import uuid
from typing import List, Optional

from shared import synced_store


_CACHE = {"path": "", "mtime": 0.0, "size": 0, "sha256": ""}
_CHANGELOG_HEADING = re.compile(r"^##\s+(\S+)")
_CHANGELOG_BULLET = re.compile(r"^[-*]\s+(.+)$")


def version_path(project_root: str) -> str:
    return os.path.join(project_root, "agent", "VERSION")


def changelog_path(project_root: str) -> str:
    return os.path.join(project_root, "agent", "CHANGELOG.md")


def candidate_exe_paths(project_root: str) -> list:
    root = os.path.abspath(project_root)
    name = "WeComSyncAgent.exe"
    out = [os.path.join(root, "dist", name)]
    here = root
    for _ in range(4):
        parent = os.path.dirname(here)
        if parent == here:
            break
        out.append(os.path.join(parent, "企业微信聊天记录解析", "dist", name))
        here = parent
    seen = set()
    uniq = []
    for path in out:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(path)
    return uniq


def agent_exe_path(project_root: str) -> str:
    for path in candidate_exe_paths(project_root):
        if os.path.isfile(path):
            return path
    return ""


def read_version(project_root: str) -> str:
    path = version_path(project_root)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return (f.read() or "").strip()
    except OSError:
        return ""


def parse_changelog(text: str) -> List[dict]:
    entries: List[dict] = []
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


def read_changelog(project_root: str) -> List[dict]:
    path = changelog_path(project_root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return parse_changelog(f.read())
    except OSError:
        return []


def notes_for_version(entries: List[dict], version: str) -> List[str]:
    version = (version or "").strip()
    for item in entries:
        if item.get("version") == version:
            return list(item.get("notes") or [])
    return []


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


def _cached_hash(path: str) -> str:
    st = os.stat(path)
    if (
        _CACHE["path"] == path
        and _CACHE["mtime"] == st.st_mtime
        and _CACHE["size"] == st.st_size
        and _CACHE["sha256"]
    ):
        return _CACHE["sha256"]
    sha256 = hash_file(path)
    _CACHE.update({
        "path": path,
        "mtime": st.st_mtime,
        "size": st.st_size,
        "sha256": sha256,
    })
    return sha256


def build_manifest(project_root: str) -> Optional[dict]:
    version = read_version(project_root)
    path = agent_exe_path(project_root)
    if not version or not path:
        return None
    st = os.stat(path)
    entries = read_changelog(project_root)
    notes = notes_for_version(entries, version)
    return {
        "version": version,
        "sha256": _cached_hash(path),
        "size": int(st.st_size),
        "url": "/api/ingest/agent-exe",
        "download_url": "/api/agent-exe",
        "notes": "\n".join(notes),
        "changelog": entries[:12],
        "published": True,
    }


def latest_info(project_root: str) -> dict:
    version = read_version(project_root)
    manifest = build_manifest(project_root)
    if manifest:
        return manifest
    entries = read_changelog(project_root)
    notes = notes_for_version(entries, version)
    return {
        "version": version,
        "sha256": "",
        "size": 0,
        "url": "/api/ingest/agent-exe",
        "download_url": "/api/agent-exe",
        "notes": "\n".join(notes),
        "changelog": entries[:12],
        "published": False,
    }


class PushHub:
    """Per-computer force-update jobs delivered on the next heartbeat."""

    def __init__(self, ttl_sec: int = 10 * 60):
        self._ttl = int(ttl_sec)
        self._lock = threading.Lock()
        self._jobs = {}
        self._by_source = {}

    def request(self, source_id: str, version: str = "") -> dict:
        source_id = synced_store._safe_id(source_id)
        if not source_id:
            raise ValueError("source_id is required")
        now = time.time()
        with self._lock:
            self._expire(now)
            job_id = self._by_source.get(source_id)
            job = self._jobs.get(job_id) if job_id else None
            if job and job["status"] == "pending":
                return dict(job)
            job_id = uuid.uuid4().hex
            job = {
                "job_id": job_id,
                "source_id": source_id,
                "kind": "update",
                "force": True,
                "version": str(version or "").strip(),
                "status": "pending",
                "detail": "",
                "created": now,
                "updated": now,
            }
            self._jobs[job_id] = job
            self._by_source[source_id] = job_id
            return dict(job)

    def pending_for(self, source_id: str) -> list:
        source_id = synced_store._safe_id(source_id)
        now = time.time()
        with self._lock:
            self._expire(now)
            out = []
            for job in self._jobs.values():
                if job["source_id"] == source_id and job["status"] == "pending":
                    out.append({
                        "job_id": job["job_id"],
                        "kind": "update",
                        "force": True,
                        "version": job.get("version") or "",
                    })
            return out

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def mark_accepted(self, job_id: str, detail: str = "") -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError("job not found")
            job["status"] = "accepted"
            job["detail"] = detail or "助手已开始更新"
            job["updated"] = time.time()
            return dict(job)

    def mark_error(self, job_id: str, detail: str = "") -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError("job not found")
            job["status"] = "error"
            job["detail"] = detail or "更新失败"
            job["updated"] = time.time()
            return dict(job)

    def _expire(self, now: float) -> None:
        drop = []
        for job_id, job in self._jobs.items():
            age = now - float(job.get("created") or 0)
            if job["status"] == "pending" and age > self._ttl:
                job["status"] = "error"
                job["detail"] = "推送超时，请确认助手在线"
                job["updated"] = now
            if age > self._ttl * 2:
                drop.append(job_id)
        for job_id in drop:
            job = self._jobs.pop(job_id, None)
            if not job:
                continue
            if self._by_source.get(job["source_id"]) == job_id:
                self._by_source.pop(job["source_id"], None)


push_hub = PushHub()
