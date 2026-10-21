"""On-demand remote agent log fetch jobs."""
import os
import threading
import time
import uuid
from typing import Optional

from shared import synced_store


JOB_TTL_SEC = 120
MAX_LOG_BYTES = 512 * 1024
LOGS_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "export",
    "agent_logs",
)


def logs_dir(source_id: str) -> str:
    return os.path.join(LOGS_ROOT, synced_store._safe_id(source_id))


def stored_path(source_id: str) -> str:
    path = os.path.join(logs_dir(source_id), "agent.log")
    return path if os.path.isfile(path) else ""


def read_log_text(path: str, max_bytes: int = MAX_LOG_BYTES) -> str:
    if not path or not os.path.isfile(path):
        return ""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if size > max_bytes:
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
            data = data.split(b"\n", 1)[-1]
        else:
            data = handle.read()
    return data.decode("utf-8", errors="replace")


class LogJobHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._by_source = {}

    def request(self, source_id: str) -> dict:
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
                "kind": "log",
                "filename": "agent.log",
                "status": "pending",
                "detail": "",
                "path": "",
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
                        "kind": "log",
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
            job["detail"] = detail or "助手尚未产生日志"
            job["updated"] = time.time()
            return dict(job)

    def mark_error(self, job_id: str, detail: str = "") -> dict:
        with self._lock:
            job = self._require(job_id)
            job["status"] = "error"
            job["detail"] = detail or "回传失败"
            job["updated"] = time.time()
            return dict(job)

    def save_bytes(self, job_id: str, filename: str, data: bytes) -> dict:
        if data is None:
            raise ValueError("empty file")
        if len(data) > MAX_LOG_BYTES * 2:
            raise ValueError("file too large")
        with self._lock:
            job = self._require(job_id)
            folder = logs_dir(job["source_id"])
            os.makedirs(folder, exist_ok=True)
            dest = os.path.join(folder, "agent.log")
            tmp = dest + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(data or b"")
        os.replace(tmp, dest)
        with self._lock:
            job = self._require(job_id)
            job["status"] = "ready"
            job["path"] = dest
            job["filename"] = filename or "agent.log"
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
                job["detail"] = "回传超时，请确认助手在线"
                job["updated"] = now
            if age > JOB_TTL_SEC * 4:
                drop.append(job_id)
        for job_id in drop:
            job = self._jobs.pop(job_id, None)
            if not job:
                continue
            if self._by_source.get(job["source_id"]) == job_id:
                self._by_source.pop(job["source_id"], None)


hub = LogJobHub()
