import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from shared import synced_store
from shared.presence import PresenceHub
import api.server as server


class SourceDeleteApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = {
            "presence": server.presence,
            "synced_root": synced_store.SYNCED_ROOT,
            "token": server._load_ingest_token,
        }
        synced_store.SYNCED_ROOT = os.path.join(self.tmp.name, "synced")
        server.presence = PresenceHub()
        server._load_ingest_token = lambda: "test-token"
        self.client = TestClient(server.app)

    def tearDown(self):
        server.presence = self._orig["presence"]
        synced_store.SYNCED_ROOT = self._orig["synced_root"]
        server._load_ingest_token = self._orig["token"]
        self.tmp.cleanup()

    def test_delete_synced_source(self):
        saved = synced_store.save_ingest({
            "computer_name": "PC-DEL",
            "operator_name": "alice",
            "sessions": [{"id": "S:1", "display_name": "家", "messages": [{"text": "hi"}]}],
        })
        sid = saved["source_id"]
        res = self.client.delete("/api/sources/" + sid)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["removed_store"])
        ids = [item["id"] for item in self.client.get("/api/sources").json()]
        self.assertNotIn(sid, ids)

    def test_delete_presence_only_source(self):
        self.client.post("/api/ingest/heartbeat", json={
            "token": "test-token",
            "computer_name": "PC-HB",
            "operator_name": "bob",
        })
        res = self.client.delete("/api/sources/PC-HB")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["forgotten"])
        names = [item["computer_name"] for item in self.client.get("/api/sources").json()]
        self.assertNotIn("PC-HB", names)

    def test_delete_local_rejected(self):
        res = self.client.delete("/api/sources/local")
        self.assertEqual(res.status_code, 400)

    def test_delete_missing(self):
        res = self.client.delete("/api/sources/no-such-pc")
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
