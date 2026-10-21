import os
import tempfile
import unittest

from shared import file_jobs, synced_store


class FileJobHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_root = synced_store.SYNCED_ROOT
        synced_store.SYNCED_ROOT = os.path.join(self.tmp.name, "synced")
        self.hub = file_jobs.FileJobHub()

    def tearDown(self):
        synced_store.SYNCED_ROOT = self._orig_root
        self.tmp.cleanup()

    def test_request_creates_pending_job(self):
        job = self.hub.request("PC-A-1", 42, "R:1", "a.zip")
        self.assertEqual(job["status"], "pending")
        self.assertTrue(job["job_id"])
        pending = self.hub.pending_for("PC-A-1")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["message_id"], 42)
        self.assertEqual(pending[0]["filename"], "a.zip")

    def test_request_reuses_pending_job(self):
        first = self.hub.request("PC-A-1", 42, "R:1", "a.zip")
        second = self.hub.request("PC-A-1", 42, "R:1", "a.zip")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.hub.pending_for("PC-A-1")), 1)

    def test_save_bytes_then_stored_path(self):
        job = self.hub.request("PC-A-1", 99, "R:1", "a.zip")
        saved = self.hub.save_bytes(job["job_id"], "a.zip", b"hello-file")
        self.assertEqual(saved["status"], "ready")
        path = file_jobs.stored_path("PC-A-1", 99, "a.zip")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"hello-file")
        ready = self.hub.request("PC-A-1", 99, "R:1", "a.zip")
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(self.hub.pending_for("PC-A-1"), [])

    def test_mark_missing(self):
        job = self.hub.request("PC-B", 7, "S:1", "x.pdf")
        missing = self.hub.mark_missing(job["job_id"], "对方电脑未缓存该文件")
        self.assertEqual(missing["status"], "missing")
        again = self.hub.request("PC-B", 7, "S:1", "x.pdf")
        self.assertEqual(again["status"], "missing")
        self.assertEqual(self.hub.pending_for("PC-B"), [])
