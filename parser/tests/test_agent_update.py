import hashlib
import os
import tempfile
import unittest

from shared import agent_update


class AgentUpdateManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "agent"))
        os.makedirs(os.path.join(self.root, "dist"))
        with open(os.path.join(self.root, "agent", "VERSION"), "w", encoding="utf-8") as f:
            f.write("2026.09.16.1\n")
        with open(os.path.join(self.root, "agent", "CHANGELOG.md"), "w", encoding="utf-8") as f:
            f.write("## 2026.09.16.1\n- 测试说明一行\n")
        self.exe = os.path.join(self.root, "dist", "WeComSyncAgent.exe")
        with open(self.exe, "wb") as f:
            f.write(b"fake-agent-bytes")
        agent_update._CACHE.update({"path": "", "mtime": 0.0, "size": 0, "sha256": ""})

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_manifest(self):
        info = agent_update.build_manifest(self.root)
        self.assertEqual(info["version"], "2026.09.16.1")
        self.assertEqual(info["size"], len(b"fake-agent-bytes"))
        self.assertEqual(info["url"], "/api/ingest/agent-exe")
        self.assertEqual(info["download_url"], "/api/agent-exe")
        self.assertEqual(info["sha256"], hashlib.sha256(b"fake-agent-bytes").hexdigest())
        self.assertEqual(info["notes"], "测试说明一行")
        self.assertEqual(info["changelog"][0]["version"], "2026.09.16.1")

    def test_parse_changelog(self):
        text = "## 2026.09.17.2\n- 显示更新说明\n\n## 2026.09.17.1\n- 修复短回复\n"
        entries = agent_update.parse_changelog(text)
        self.assertEqual(entries[0]["version"], "2026.09.17.2")
        self.assertEqual(entries[1]["notes"], ["修复短回复"])

    def test_missing_exe_returns_none(self):
        os.remove(self.exe)
        self.assertIsNone(agent_update.build_manifest(self.root))

    def test_latest_info_without_exe(self):
        os.remove(self.exe)
        info = agent_update.latest_info(self.root)
        self.assertEqual(info["version"], "2026.09.16.1")
        self.assertFalse(info["published"])

    def test_version_newer(self):
        self.assertTrue(agent_update.version_newer("2026.09.21.2", "2026.09.21.1"))
        self.assertFalse(agent_update.version_newer("2026.09.20.3", "2026.09.21.1"))

    def test_push_hub_pending_then_accept(self):
        hub = agent_update.PushHub()
        job = hub.request("pc-a", "2026.09.21.2")
        self.assertEqual(job["kind"], "update")
        self.assertEqual(len(hub.pending_for("pc-a")), 1)
        hub.mark_accepted(job["job_id"])
        self.assertEqual(hub.pending_for("pc-a"), [])
