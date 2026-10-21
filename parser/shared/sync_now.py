"""Ask an online agent to sync immediately, delivered on the next heartbeat."""
import threading
import time
import uuid

from shared import synced_store


class SyncNowHub:
    def __init__(self, ttl_sec: int = 2 * 60):
        self._ttl = int(ttl_sec)
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
                "kind": "sync",
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
                        "kind": "sync",
                    })
            return out

    def mark_accepted(self, job_id: str, detail: str = "") -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError("job not found")
            job["status"] = "accepted"
            job["detail"] = detail or "助手已开始同步"
            job["updated"] = time.time()
            return dict(job)

    def _expire(self, now: float) -> None:
        drop = []
        for job_id, job in self._jobs.items():
            age = now - float(job.get("created") or 0)
            if job["status"] == "pending" and age > self._ttl:
                job["status"] = "error"
                job["detail"] = "立即同步超时，请确认助手在线"
                job["updated"] = now
            if age > self._ttl * 2:
                drop.append(job_id)
        for job_id in drop:
            job = self._jobs.pop(job_id, None)
            if job and self._by_source.get(job["source_id"]) == job_id:
                self._by_source.pop(job["source_id"], None)


hub = SyncNowHub()
