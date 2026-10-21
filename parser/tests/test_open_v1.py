import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from shared import api_keys, synced_store
import api.server as server


class OpenV1ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "config.jsonc")
        self.key_file = os.path.join(self.tmp.name, "export", "api_read_key.txt")
        self._orig = {
            "root": api_keys.PROJECT_ROOT,
            "cfg": api_keys.CONFIG_FILE,
            "key": api_keys.KEY_FILE,
            "synced": synced_store.SYNCED_ROOT,
        }
        api_keys.PROJECT_ROOT = self.tmp.name
        api_keys.CONFIG_FILE = self.cfg
        api_keys.KEY_FILE = self.key_file
        synced_store.SYNCED_ROOT = os.path.join(self.tmp.name, "synced")
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write('{"open_api": {"enabled": true, "read_key": "ci_test_key_xxxxxxxxxxxxxxxxxxxx"}}')
        self.client = TestClient(server.app)

    def tearDown(self):
        api_keys.PROJECT_ROOT = self._orig["root"]
        api_keys.CONFIG_FILE = self._orig["cfg"]
        api_keys.KEY_FILE = self._orig["key"]
        synced_store.SYNCED_ROOT = self._orig["synced"]
        self.tmp.cleanup()

    def test_health_does_not_need_key(self):
        res = self.client.get("/v1/health")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    def test_sources_requires_key(self):
        res = self.client.get("/v1/sources")
        self.assertEqual(res.status_code, 401)

    def test_sources_with_header_key(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-API",
            "operator_name": "测试员",
            "sessions": [{
                "id": "R:9",
                "display_name": "材料群",
                "messages": [
                    {
                        "text": "申报材料已发",
                        "sender": "alice",
                        "time_text": "2026-09-16 13:00",
                        "message_id": 2,
                        "msg_type": 2,
                    }
                ],
            }],
        })
        res = self.client.get("/v1/sources", headers={"X-API-Key": "ci_test_key_xxxxxxxxxxxxxxxxxxxx"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        ids = [item["id"] for item in body["data"]]
        self.assertIn(result["source_id"], ids)

        sessions = self.client.get(
            f"/v1/sources/{result['source_id']}/sessions",
            headers={"Authorization": "Bearer ci_test_key_xxxxxxxxxxxxxxxxxxxx"},
        )
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(sessions.json()["data"][0]["display_name"], "材料群")

        messages = self.client.get(
            f"/v1/sources/{result['source_id']}/messages/R:9",
            params={"key": "ci_test_key_xxxxxxxxxxxxxxxxxxxx"},
        )
        self.assertEqual(messages.status_code, 200)
        self.assertIn("申报材料已发", messages.json()["data"][0]["text"])

        search = self.client.get(
            "/v1/search",
            params={"q": "申报", "source_id": result["source_id"]},
            headers={"X-API-Key": "ci_test_key_xxxxxxxxxxxxxxxxxxxx"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["count"], 1)

    def test_wrong_key_is_rejected(self):
        res = self.client.get("/v1/info", headers={"X-API-Key": "nope"})
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
