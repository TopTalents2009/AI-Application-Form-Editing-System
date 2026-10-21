import os
import tempfile
import unittest

from shared import api_keys


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "config.jsonc")
        self.key_file = os.path.join(self.tmp.name, "export", "api_read_key.txt")
        self._orig_root = api_keys.PROJECT_ROOT
        self._orig_cfg = api_keys.CONFIG_FILE
        self._orig_key = api_keys.KEY_FILE
        api_keys.PROJECT_ROOT = self.tmp.name
        api_keys.CONFIG_FILE = self.cfg
        api_keys.KEY_FILE = self.key_file

    def tearDown(self):
        api_keys.PROJECT_ROOT = self._orig_root
        api_keys.CONFIG_FILE = self._orig_cfg
        api_keys.KEY_FILE = self._orig_key
        self.tmp.cleanup()

    def _write_cfg(self, body: str):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(body)

    def test_auto_creates_file_key(self):
        keys = api_keys.list_keys()
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0]["key"].startswith("ci_"))
        self.assertTrue(os.path.isfile(self.key_file))
        self.assertEqual(api_keys.verify(keys[0]["key"])["name"], "default")
        self.assertIsNone(api_keys.verify("wrong-key"))

    def test_config_read_key_and_named_keys(self):
        self._write_cfg("""{
          "open_api": {
            "enabled": true,
            "read_key": "ci_main_key_aaaaaaaaaaaaaaaaaaaaaaaa",
            "keys": [
              {"name": "同事A", "key": "ci_peer_key_bbbbbbbbbbbbbbbbbbbbbbbb"},
              {"name": "停用", "key": "ci_dead", "enabled": false}
            ]
          }
        }""")
        keys = api_keys.list_keys()
        names = [item["name"] for item in keys]
        self.assertEqual(names, ["default", "同事A"])
        self.assertEqual(api_keys.verify("ci_peer_key_bbbbbbbbbbbbbbbbbbbbbbbb")["name"], "同事A")
        self.assertIsNone(api_keys.verify("ci_dead"))

    def test_disabled_rejects_all(self):
        self._write_cfg('{"open_api": {"enabled": false, "read_key": "ci_hidden"}}')
        self.assertFalse(api_keys.is_enabled())
        self.assertEqual(api_keys.list_keys(), [])
        self.assertIsNone(api_keys.verify("ci_hidden"))

    def test_extract_token_prefers_header(self):
        self.assertEqual(api_keys.extract_token("abc", "Bearer xyz", "q"), "abc")
        self.assertEqual(api_keys.extract_token("", "Bearer xyz", "q"), "xyz")
        self.assertEqual(api_keys.extract_token("", "", "q"), "q")

    def test_homepage_info_includes_v1_url(self):
        self._write_cfg('{"open_api": {"read_key": "ci_demo_key_cccccccccccccccccccc"}}')
        info = api_keys.homepage_info(["http://127.0.0.1:8767", "http://192.168.2.25:8767"])
        self.assertTrue(info["enabled"])
        self.assertEqual(info["key"], "ci_demo_key_cccccccccccccccccccc")
        self.assertIn("http://192.168.2.25:8767/v1", info["urls"])
        self.assertIn("X-API-Key", info["example"])
        self.assertIn("/v1/sources", info["example"])


if __name__ == "__main__":
    unittest.main()
