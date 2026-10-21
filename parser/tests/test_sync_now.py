import unittest

from fastapi.testclient import TestClient

from shared import sync_now
from shared.presence import PresenceHub
import api.server as server


class SyncNowHubTests(unittest.TestCase):
    def setUp(self):
        self.hub = sync_now.SyncNowHub()

    def test_request_creates_pending_job(self):
        job = self.hub.request("SKY-20241202FDZ")
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["kind"], "sync")
        pending = self.hub.pending_for("SKY-20241202FDZ")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["job_id"], job["job_id"])

    def test_request_reuses_pending_job(self):
        first = self.hub.request("pc-a")
        second = self.hub.request("pc-a")
        self.assertEqual(first["job_id"], second["job_id"])

    def test_accepted_clears_pending(self):
        job = self.hub.request("pc-a")
        self.hub.mark_accepted(job["job_id"], "助手已开始同步")
        self.assertEqual(self.hub.pending_for("pc-a"), [])


class SyncNowApiTests(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "presence": server.presence,
            "token": server._load_ingest_token,
            "hub": sync_now.hub,
        }
        server.presence = PresenceHub()
        server._load_ingest_token = lambda: "test-token"
        sync_now.hub = sync_now.SyncNowHub()
        self.client = TestClient(server.app)
        self.client.post("/api/ingest/heartbeat", json={
            "token": "test-token",
            "computer_name": "SYNC-NOW-TEST-PC",
            "operator_name": "测试",
            "agent_version": "2026.09.22.3",
        })

    def tearDown(self):
        server.presence = self._orig["presence"]
        server._load_ingest_token = self._orig["token"]
        sync_now.hub = self._orig["hub"]

    def test_sync_now_requires_online(self):
        server.presence = PresenceHub()
        res = self.client.post("/api/sources/ghost-pc/sync-now")
        self.assertEqual(res.status_code, 409)

    def test_sync_now_queues_heartbeat_job(self):
        created = self.client.post("/api/sources/SYNC-NOW-TEST-PC/sync-now")
        self.assertEqual(created.status_code, 200, created.text)
        job_id = created.json()["job_id"]
        hb = self.client.post("/api/ingest/heartbeat", json={
            "token": "test-token",
            "computer_name": "SYNC-NOW-TEST-PC",
            "operator_name": "测试",
            "agent_version": "2026.09.22.3",
        })
        jobs = hb.json().get("sync_jobs") or []
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_id"], job_id)
        self.assertEqual(jobs[0]["kind"], "sync")
        ack = self.client.post("/api/ingest/file-result", json={
            "token": "test-token",
            "job_id": job_id,
            "status": "accepted",
            "detail": "助手已开始同步",
        })
        self.assertEqual(ack.status_code, 200)
        again = self.client.post("/api/ingest/heartbeat", json={
            "token": "test-token",
            "computer_name": "SYNC-NOW-TEST-PC",
            "operator_name": "测试",
            "agent_version": "2026.09.22.3",
        })
        self.assertEqual(again.json().get("sync_jobs") or [], [])
