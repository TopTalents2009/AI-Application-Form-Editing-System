import os
import tempfile
import unittest
from unittest.mock import patch

from shared import wxwork_paths


class WxWorkPathTests(unittest.TestCase):
    def test_reg_value_expands_userprofile(self):
        class _Key:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def open_key(_hive, subkey):
            if subkey.endswith("User Shell Folders"):
                return _Key()
            raise OSError(subkey)

        def query(_key, name):
            if name == "Personal":
                return r"%USERPROFILE%\Documents", 2
            raise OSError(name)

        with patch.dict(os.environ, {"USERPROFILE": r"C:\Users\demo"}), \
             patch("winreg.OpenKey", open_key), \
             patch("winreg.QueryValueEx", query):
            value = wxwork_paths._reg_value(
                wxwork_paths._SHELL_KEYS[0], "Personal"
            )
        self.assertEqual(value, r"C:\Users\demo\Documents")

    def test_empty_custom_path_still_uses_shell_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, "Docs")
            wx = os.path.join(docs, "WXWork")
            os.makedirs(wx)

            def fake_reg(subkey, name):
                if name == "DataLocationPath":
                    return ""
                if name == "Personal" and subkey == wxwork_paths._SHELL_KEYS[0]:
                    return docs
                return ""

            env = {
                "USERPROFILE": os.path.join(tmp, "profile"),
                "APPDATA": os.path.join(tmp, "appdata"),
            }
            with patch.object(wxwork_paths, "_reg_value", side_effect=fake_reg), \
                 patch.dict(os.environ, env):
                bases = wxwork_paths.collect_wxwork_base_dirs()
            self.assertEqual(bases, [os.path.abspath(wx)])

    def test_appdata_data_root_is_included_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_data = os.path.join(tmp, "Roaming", "Tencent", "WXWork", "Data")
            os.makedirs(app_data)

            def fake_reg(_subkey, name):
                if name == "DataLocation":
                    return "1"
                return ""

            env = {
                "USERPROFILE": os.path.join(tmp, "profile"),
                "APPDATA": os.path.join(tmp, "Roaming"),
            }
            with patch.object(wxwork_paths, "_reg_value", side_effect=fake_reg), \
                 patch.dict(os.environ, env):
                bases = wxwork_paths.collect_wxwork_base_dirs()
            self.assertIn(os.path.abspath(app_data), bases)

    def test_custom_data_location_path_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = os.path.join(tmp, "Chats", "WXWork")
            os.makedirs(custom)

            def fake_reg(_subkey, name):
                if name == "DataLocationPath":
                    return os.path.join(tmp, "Chats")
                return ""

            env = {
                "USERPROFILE": os.path.join(tmp, "profile"),
                "APPDATA": os.path.join(tmp, "appdata"),
            }
            with patch.object(wxwork_paths, "_reg_value", side_effect=fake_reg), \
                 patch.dict(os.environ, env):
                bases = wxwork_paths.collect_wxwork_base_dirs()
            self.assertEqual(bases[0], os.path.abspath(os.path.join(tmp, "Chats")))
            self.assertIn(os.path.abspath(custom), bases)


if __name__ == "__main__":
    unittest.main()
