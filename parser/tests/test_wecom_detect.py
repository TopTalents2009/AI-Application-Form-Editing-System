import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

wecom = importlib.import_module("core-wecom.chat_platform")


class WeComDetectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.wxwork = os.path.join(self.root, "WXWork")
        self.account_data = os.path.join(self.wxwork, "1688855370846072", "Data")
        os.makedirs(self.account_data)
        with open(os.path.join(self.account_data, "message.db"), "wb") as f:
            f.write(b"\xecencryptedheader")
        self.decrypted = os.path.join(self.root, "wxwork_decrypted")
        self.live_patch = patch.object(wecom, "load_live_manifest", return_value=None)
        self.live_patch.start()
        self.agent_patch = patch.object(wecom, "_agent_decrypted_dir", return_value="")
        self.agent_patch.start()

    def tearDown(self):
        self.agent_patch.stop()
        self.live_patch.stop()
        self.tmp.cleanup()

    def test_collect_data_dirs_finds_account_data(self):
        found = wecom._collect_data_dirs(self.wxwork)
        self.assertEqual(found, [self.account_data])

    def test_collect_data_dirs_skips_non_account_folders(self):
        os.makedirs(os.path.join(self.wxwork, "Global", "Data"))
        with open(os.path.join(self.wxwork, "Global", "Data", "message.db"), "wb") as f:
            f.write(b"SQLite format 3\x00")
        found = wecom._collect_data_dirs(self.wxwork)
        self.assertEqual(found, [self.account_data])

    def test_detect_data_dir_uses_custom_wxwork_root(self):
        with patch.object(wecom, "_get_wxwork_base_dirs", return_value=[self.wxwork]), \
             patch.object(wecom, "DECRYPTED_DIR", self.decrypted):
            plat = wecom.WeComPlatform()
            self.assertEqual(plat.detect_data_dir(), self.account_data)
            self.assertEqual(plat.data_dir, self.account_data)

    def test_detect_data_dir_prefers_newer_agent_decrypt(self):
        os.makedirs(self.decrypted)
        with open(os.path.join(self.decrypted, "message.db"), "wb") as f:
            f.write(b"SQLite format 3\x00")
        os.utime(os.path.join(self.decrypted, "message.db"), (10, 10))
        agent_dir = os.path.join(self.root, "agent_decrypted")
        os.makedirs(agent_dir)
        agent_db = os.path.join(agent_dir, "message.db")
        with open(agent_db, "wb") as f:
            f.write(b"SQLite format 3\x00")
        os.utime(agent_db, (100, 100))
        with patch.object(wecom, "_get_wxwork_base_dirs", return_value=[self.wxwork]), \
             patch.object(wecom, "DECRYPTED_DIR", self.decrypted), \
             patch.object(wecom, "_agent_decrypted_dir", return_value=agent_dir):
            plat = wecom.WeComPlatform()
            self.assertEqual(plat.detect_data_dir(), agent_dir)
            self.assertEqual(plat._decrypted_dir, agent_dir)

    def test_detect_data_dir_prefers_plaintext_decrypted_dir(self):
        os.makedirs(self.decrypted)
        with open(os.path.join(self.decrypted, "message.db"), "wb") as f:
            f.write(b"SQLite format 3\x00")
        with patch.object(wecom, "_get_wxwork_base_dirs", return_value=[self.wxwork]), \
             patch.object(wecom, "DECRYPTED_DIR", self.decrypted):
            plat = wecom.WeComPlatform()
            self.assertEqual(plat.detect_data_dir(), self.decrypted)

    def test_detect_data_dir_picks_newer_account(self):
        older = os.path.join(self.wxwork, "1688855723749883", "Data")
        os.makedirs(older)
        with open(os.path.join(older, "message.db"), "wb") as f:
            f.write(b"\x04encrypted")
        os.utime(older, (1, 1))
        os.utime(self.account_data, (100, 100))
        with patch.object(wecom, "_get_wxwork_base_dirs", return_value=[self.wxwork]), \
             patch.object(wecom, "DECRYPTED_DIR", self.decrypted):
            plat = wecom.WeComPlatform()
            self.assertEqual(plat.detect_data_dir(), self.account_data)

    def test_infer_self_id_counts_self_chat_once(self):
        self_id = 1688855370846072
        cids = [
            "S:%s_1688858081684753" % self_id,
            "S:%s_1688850157563691" % self_id,
            "S:%s_%s" % (self_id, self_id),
        ]
        self.assertEqual(wecom._infer_self_user_id(cids), self_id)

    def test_detect_data_dir_uses_redirected_documents(self):
        docs = os.path.join(self.root, "redirected_docs")
        account = os.path.join(docs, "WXWork", "1688855370846072", "Data")
        os.makedirs(account)
        with open(os.path.join(account, "message.db"), "wb") as f:
            f.write(b"\xecencryptedheader")

        def fake_reg(_subkey, name):
            if name == "Personal":
                return docs
            return ""

        env = {
            "USERPROFILE": os.path.join(self.root, "profile"),
            "APPDATA": os.path.join(self.root, "appdata"),
        }
        with patch("shared.wxwork_paths._reg_value", side_effect=fake_reg), \
             patch.dict(os.environ, env), \
             patch.object(wecom, "DECRYPTED_DIR", self.decrypted):
            plat = wecom.WeComPlatform()
            self.assertEqual(plat.detect_data_dir(), account)

    def test_append_wxwork_base_includes_nested_wxwork(self):
        parent = os.path.join(self.root, "chat_records")
        nested = os.path.join(parent, "WXWork")
        os.makedirs(nested)
        bases = []
        wecom._append_wxwork_base(parent, bases)
        self.assertEqual(bases, [os.path.abspath(parent), os.path.abspath(nested)])

    def test_detect_live_manifest_uses_remote_dir(self):
        live = {"mode": "live", "decrypted_dir": r"C:\wxwork_pull\decrypted"}
        with patch.object(wecom, "load_live_manifest", return_value=live), \
             patch.object(wecom, "DECRYPTED_DIR", self.decrypted):
            plat = wecom.WeComPlatform()
            self.assertEqual(plat.detect_data_dir(), r"C:\wxwork_pull\decrypted")
            self.assertTrue(plat._live)


if __name__ == "__main__":
    unittest.main()
