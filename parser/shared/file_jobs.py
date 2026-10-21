"""On-demand remote WeCom attachment fetch jobs."""
import os
import threading
import time
import uuid
from typing import Optional

from shared import synced_store


JOB_TTL_SEC = 600
MAX_FILE_BYTES = 80 * 1024 * 1024


def files_dir(source_id: str) -> str:
    return os.path.join(synced_store._source_dir(source_id), "files")


def _safe_filename(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    return synced_store._safe_id(base)[:80]


def stored_path(source_id: str, message_id: int, filename: str = "") -> str:
    folder = files_dir(source_id)
    if not os.path.isdir(folder):
        return ""
    mid = int(message_id or 0)
    if not mid:
        return ""
    prefix = f"{mid}_"
    safe = _safe_filename(filename)
    candidates = []
    if safe:
        candidates.append(os.path.join(folder, f"{prefix}{safe}"))
    candidates.append(os.path.join(folder, str(mid)))
    for path in candidates:
        if os.path.isfile(path):
            return path
    try:
        names = os.listdir(folder)
    except OSError:
        return ""
    for name in names:
        if name.startswith(prefix) or name == str(mid):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                return path
    return ""


class FileJobHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._by_key = {}

    def request(
        self,
        source_id: str,
        message_id: int,
        session_id: str = "",
        filename: str = "",
    ) -> dict:
        source_id = synced_store._safe_id(source_id)
        message_id = int(message_id or 0)
        if not source_id or not message_id:
            raise ValueError("source_id and message_id are required")
        path = stored_path(source_id, message_id, filename)
        if path:
            return {
                "job_id": "",
                "source_id": source_id,
                "message_id": message_id,
                "session_id": session_id or "",
                "filename": filename or os.path.basename(path),
                "status": "ready",
                "detail": "",
                "path": path,
            }

        key = (source_id, message_id)
        now = time.time()
        with self._lock:
            self._expire(now)
            job_id = self._by_key.get(key)
            job = self._jobs.get(job_id) if job_id else None
            if job:
                age = now - float(job.get("updated") or job.get("created") or 0)
                if job["status"] in ("pending", "uploading", "ready"):
                    return dict(job)
                if job["status"] == "missing" and age < 30:
                    return dict(job)
                if job["status"] == "error" and age < 15:
                    return dict(job)
            job_id = uuid.uuid4().hex
            job = {
                "job_id": job_id,
                "source_id": source_id,
                "message_id": message_id,
                "session_id": session_id or "",
                "filename": filename or "",
                "status": "pending",
                "detail": "",
                "path": "",
                "created": now,
                "updated": now,
            }
            self._jobs[job_id] = job
            self._by_key[key] = job_id
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
                        "message_id": job["message_id"],
                        "session_id": job["session_id"],
                        "filename": job["filename"],
                    })
            return out

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def mark_missing(self, job_id: str, detail: str = "") -> dict:
        with self._lock:
            job = self._require(job_id)
            job["status"] = "missing"
            job["detail"] = detail or "对方电脑未缓存该文件"
            job["updated"] = time.time()
            return dict(job)

    def mark_error(self, job_id: str, detail: str = "") -> dict:
        with self._lock:
            job = self._require(job_id)
            job["status"] = "error"
            job["detail"] = detail or "拉取失败"
            job["updated"] = time.time()
            return dict(job)

    def save_bytes(self, job_id: str, filename: str, data: bytes) -> dict:
        if data is None:
            raise ValueError("empty file")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("file too large")
        if not data:
            raise ValueError("empty file")
        with self._lock:
            job = self._require(job_id)
            name = filename or job.get("filename") or "file"
            folder = files_dir(job["source_id"])
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, f"{int(job['message_id'])}_{_safe_filename(name)}")
            tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        with self._lock:
            job = self._require(job_id)
            job["status"] = "ready"
            job["path"] = dest
            job["filename"] = name
            job["detail"] = ""
            job["updated"] = time.time()
            return dict(job)

    def _require(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError("job not found")
        return job

    def _expire(self, now: float) -> None:
        drop = []
        for job_id, job in self._jobs.items():
            created = float(job.get("created") or 0)
            age = now - created
            if job["status"] == "pending" and age > JOB_TTL_SEC:
                job["status"] = "error"
                job["detail"] = "拉取超时，请确认助手在线"
                job["updated"] = now
            if age > JOB_TTL_SEC * 2:
                drop.append(job_id)
        for job_id in drop:
            job = self._jobs.pop(job_id, None)
            if not job:
                continue
            key = (job["source_id"], job["message_id"])
            if self._by_key.get(key) == job_id:
                self._by_key.pop(key, None)


hub = FileJobHub()
