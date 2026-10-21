import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from shared import agent_logs, agent_update
from shared.presence import PresenceHub
import api.server as server


class LogJobHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_root = agent_logs.LOGS_ROOT
        agent_logs.LOGS_ROOT = os.path.join(self.tmp.name, "agent_logs")
        self.hub = agent_logs.LogJobHub()

    def tearDown(self):
        agent_logs.LOGS_ROOT = self._orig_root
        self.tmp.cleanup()

    def test_request_creates_pending_job(self):
        job = self.hub.request("SKY-20241202FDZ")
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["kind"], "log")
        pending = self.hub.pending_for("SKY-20241202FDZ")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["kind"], "log")

    def test_request_reuses_pending_job(self):
        first = self.hub.request("pc-a")
        second = self.hub.request("pc-a")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.hub.pending_for("pc-a")), 1)

    def test_save_bytes_then_read(self):
        job = self.hub.request("pc-a")
        saved = self.hub.save_bytes(job["job_id"], "agent.log", "hello log\n".encode("utf-8"))
        self.assertEqual(saved["status"], "ready")
        path = agent_logs.stored_path("pc-a")
        self.assertTrue(os.path.isfile(path))
        self.assertIn("hello log", agent_logs.read_log_text(path))
        self.assertEqual(self.hub.pending_for("pc-a"), [])

    def test_new_request_after_ready_refetches(self):
        first = self.hub.request("pc-a")
        self.hub.save_bytes(first["job_id"], "agent.log", b"old")
        second = self.hub.request("pc-a")
        self.assertEqual(second["status"], "pending")
        self.assertNotEqual(first["job_id"], second["job_id"])


class AgentLogApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = {
            "presence": server.presence,
            "logs_root": agent_logs.LOGS_ROOT,
            "hub": agent_logs.hub,
            "token": server._load_ingest_token,
        }
        agent_logs.LOGS_ROOT = os.path.join(self.tmp.name, "agent_logs")
        agent_logs.hub = agent_logs.LogJobHub()
        server.presence = PresenceHub()
        server._load_ingest_token = lambda: "test-token"
        self.client = TestClient(server.app)
        self.client.post("/api/ingest/heartbeat", json={
            "token": "test-token",
            "computer_name": "SKY-20241202FDZ",
            "operator_name": "唐利萍",
            "agent_version": "2026.09.21.1",
        })

    def tearDown(self):
        server.presence = self._orig["presence"]
        agent_logs.LOGS_ROOT = self._orig["logs_root"]
        agent_logs.hub = self._orig["hub"]
        server._load_ingest_token = self._orig["token"]
        self.tmp.cleanup()

    def test_sources_include_agent_version(self):
        res = self.client.get("/api/sources")
        self.assertEqual(res.status_code, 200)
        versions = {
            item["computer_name"]: item.get("agent_version")
            for item in res.json()
        }
        self.assertEqual(versions.get("SKY-20241202FDZ"), "2026.09.21.1")

    def test_request_log_offline(self):
        server.presence = PresenceHub()
        res = self.client.post("/api/sources/ghost-pc/agent-log")
        self.assertEqual(res.status_code, 409)

    def test_request_and_fetch_log(self):
        created = self.client.post("/api/sources/SKY-20241202FDZ/agent-log")
        self.assertEqual(created.status_code, 200)
        job_id = created.json()["job_id"]
        pending = self.client.get(
            "/api/sources/SKY-20241202FDZ/agent-log",
            params={"job_id": job_id},
        )
        self.assertEqual(pending.status_code, 202)
        agent_logs.hub.save_bytes(job_id, "agent.log", "解密失败示例\n".encode("utf-8"))
        ready = self.client.get(
            "/api/sources/SKY-20241202FDZ/agent-log",
            params={"job_id": job_id},
        )
        self.assertEqual(ready.status_code, 200)
        self.assertIn("解密失败示例", ready.json()["text"])


class AgentPushApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = {
            "presence": server.presence,
            "token": server._load_ingest_token,
            "payload": server._agent_update_payload,
            "hub": agent_update.push_hub,
        }
        server.presence = PresenceHub()
        server._load_ingest_token = lambda: "test-token"
        server._agent_update_payload = lambda: {
            "version": "2026.09.21.2",
            "sha256": "abc",
            "size": 12,
            "url": "/api/ingest/agent-exe",
            "notes": "",
            "changelog": [],
            "published": True,
        }
        agent_update.push_hub = agent_update.PushHub()
        self.client = TestClient(server.app)
        self.client.post("/api/ingest/heartbeat", json={
            "token": "test-token",
            "computer_name": "SKY-20241202FDZ",
            "operator_name": "唐利萍",
            "agent_version": "2026.09.21.1",
        })

    def tearDown(self):
        server.presence = self._orig["presence"]
        server._load_ingest_token = self._orig["token"]
        server._agent_update_payload = self._orig["payload"]
        agent_update.push_hub = self._orig["hub"]
        self.tmp.cleanup()

    def test_push_requires_online(self):
        server.presence = PresenceHub()
        res = self.client.post("/api/sources/ghost-pc/agent-update")
        self.assertEqual(res.status_code, 409)

    def test_push_queues_update_job(self):
        res = self.client.post("/api/sources/SKY-20241202FDZ/agent-update")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["version"], "2026.09.21.2")
        hb = self.client.post("/api/ingest/heartbeat", json={
            "token": "test-token",
            "computer_name": "SKY-20241202FDZ",
            "operator_name": "唐利萍",
            "agent_version": "2026.09.21.1",
        })
        jobs = hb.json().get("update_jobs") or []
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["kind"], "update")
        self.assertTrue(jobs[0]["force"])

    def test_sources_mark_update_available(self):
        res = self.client.get("/api/sources")
        items = [item for item in res.json() if item.get("computer_name") == "SKY-20241202FDZ"]
        self.assertTrue(items)
        self.assertEqual(items[0]["agent_version"], "2026.09.21.1")
        self.assertTrue(items[0]["update_available"])


class AgentLogProcessTests(unittest.TestCase):
    def test_process_log_job_uploads(self):
        import importlib.util

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "wecom_sync_agent", os.path.join(root, "agent", "wecom_sync_agent.py")
        )
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "agent.log")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write("自动同步：正在解密聊天记录...\n")
            captured = {}

            class FakeResp:
                def read(self):
                    return b'{"ok": true}'

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            def fake_urlopen(req, timeout=60):
                captured["url"] = req.full_url
                captured["data"] = req.data
                return FakeResp()

            with mock.patch.object(agent, "LOG_FILE", log_path):
                with mock.patch.object(agent.request, "urlopen", side_effect=fake_urlopen):
                    status = agent.process_file_job("http://x", "tok", {
                        "job_id": "log1",
                        "kind": "log",
                        "filename": "agent.log",
                    })
            self.assertEqual(status, "ready")
            self.assertIn("/api/ingest/file", captured["url"])
            self.assertIn("正在解密", captured["data"].decode("utf-8"))

    def test_process_log_job_missing(self):
        import importlib.util

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "wecom_sync_agent", os.path.join(root, "agent", "wecom_sync_agent.py")
        )
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        with mock.patch.object(agent, "LOG_FILE", os.path.join("no-such", "agent.log")):
            with mock.patch.object(agent, "report_file_job", return_value={"ok": True}) as report:
                status = agent.process_file_job("http://x", "tok", {
                    "job_id": "log2",
                    "kind": "log",
                })
        self.assertEqual(status, "missing")
        report.assert_called_once()


if __name__ == "__main__":
    unittest.main()
