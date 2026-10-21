import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from shared import agent_update
import api.server as server


class AgentExePathTests(unittest.TestCase):
    def test_prefers_local_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = os.path.join(tmp, "dist")
            os.makedirs(dist)
            exe = os.path.join(dist, "WeComSyncAgent.exe")
            with open(exe, "wb") as f:
                f.write(b"local")
            self.assertEqual(agent_update.agent_exe_path(tmp), exe)

    def test_falls_back_to_sibling_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = os.path.join(tmp, "agent修改申报书_py", "parser")
            sibling = os.path.join(tmp, "企业微信聊天记录解析", "dist")
            os.makedirs(bundled)
            os.makedirs(sibling)
            exe = os.path.join(sibling, "WeComSyncAgent.exe")
            with open(exe, "wb") as f:
                f.write(b"sibling")
            self.assertEqual(agent_update.agent_exe_path(bundled), exe)


class AgentDownloadApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "agent"))
        os.makedirs(os.path.join(root, "dist"))
        with open(os.path.join(root, "agent", "VERSION"), "w", encoding="utf-8") as f:
            f.write("2026.09.23.1\n")
        with open(os.path.join(root, "dist", "WeComSyncAgent.exe"), "wb") as f:
            f.write(b"MZ-fake-agent")
        self._orig_dir = server.PROJECT_DIR
        self._orig_token = server._load_ingest_token
        server.PROJECT_DIR = root
        server._load_ingest_token = lambda: "test-token"
        agent_update._CACHE.update({"path": "", "mtime": 0.0, "size": 0, "sha256": ""})
        self.client = TestClient(server.app)

    def tearDown(self):
        server.PROJECT_DIR = self._orig_dir
        server._load_ingest_token = self._orig_token
        self.tmp.cleanup()

    def test_console_download_no_token(self):
        res = self.client.get("/api/agent-exe")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.content, b"MZ-fake-agent")
        cd = res.headers.get("content-disposition") or ""
        self.assertIn("WeComSyncAgent-2026.09.23.1.exe", cd)

    def test_ingest_download_requires_token(self):
        res = self.client.get("/api/ingest/agent-exe")
        self.assertEqual(res.status_code, 401)

    def test_ingest_download_with_token(self):
        res = self.client.get("/api/ingest/agent-exe", params={"token": "test-token"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.content, b"MZ-fake-agent")
