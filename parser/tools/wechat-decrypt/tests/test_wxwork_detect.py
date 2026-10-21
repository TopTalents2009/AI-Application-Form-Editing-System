import json
import os
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.path.dirname(os.path.dirname(ROOT))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if PROJECT not in sys.path:
    sys.path.append(PROJECT)

from find_wxwork_keys import (
    _load_wxwork_config,
    auto_detect_wxwork_db_dir,
    collect_wxwork_data_dirs,
    wxwork_base_dirs,
)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_wxwork_base_dirs_uses_redirected_documents():
    with tempfile.TemporaryDirectory() as tmp:
        docs = os.path.join(tmp, "Docs")
        wx = os.path.join(docs, "WXWork")
        os.makedirs(wx)

        def fake_reg(_subkey, name):
            if name == "Personal":
                return docs
            return ""

        env = {
            "USERPROFILE": os.path.join(tmp, "profile"),
            "APPDATA": os.path.join(tmp, "appdata"),
        }
        with patch("shared.wxwork_paths._reg_value", side_effect=fake_reg), \
                patch.dict(os.environ, env):
            found = wxwork_base_dirs()
        assert os.path.normcase(os.path.abspath(wx)) in [
            os.path.normcase(path) for path in found
        ]


def test_collect_finds_message_db_directly_under_data_root():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "WXWork", "Data")
        _write(os.path.join(data_dir, "message.db"), b"\xecencryptedheaderxxxx")
        found = collect_wxwork_data_dirs([data_dir])
        assert found == [data_dir]


def test_collect_wxwork_data_dirs_from_custom_root():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "WXWork", "1688855370846072", "Data")
        _write(os.path.join(data_dir, "message.db"), b"\xecencryptedheaderxxxx")
        os.makedirs(os.path.join(tmp, "WXWork", "Global", "Data"), exist_ok=True)
        found = collect_wxwork_data_dirs([os.path.join(tmp, "WXWork")])
        assert found == [data_dir]


def test_auto_detect_prefers_encrypted_and_newer_account():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "WXWork")
        newer = os.path.join(root, "1688855370846072", "Data")
        older = os.path.join(root, "1688855723749883", "Data")
        plain = os.path.join(root, "2251800139972452", "Data")
        _write(os.path.join(newer, "message.db"), b"\xec" + b"\x00" * 4096)
        _write(os.path.join(older, "message.db"), b"\x04" + b"\x00" * 4096)
        _write(os.path.join(plain, "message.db"), b"SQLite format 3\x00" + b"\x00" * 4080)
        os.utime(os.path.join(older, "message.db"), (1, 1))
        os.utime(os.path.join(newer, "message.db"), (100, 100))
        os.utime(older, (1, 1))
        os.utime(newer, (100, 100))
        with patch.dict(os.environ, {"WXWORK_AUTO_SELECT_DB": "1"}):
            selected = auto_detect_wxwork_db_dir([root])
        assert selected == newer


def test_auto_detect_returns_none_when_no_encrypted_db():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "WXWork", "1688855370846072", "Data")
        _write(os.path.join(data_dir, "message.db"), b"SQLite format 3\x00" + b"\x00" * 4080)
        assert auto_detect_wxwork_db_dir([os.path.join(tmp, "WXWork")]) is None


def test_load_wxwork_config_raises_instead_of_exiting():
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = os.path.join(tmp, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"wxwork_db_dir": os.path.join(tmp, "missing")}, f)
        with patch("config._config_file_path", return_value=cfg_path), \
                patch("find_wxwork_keys.auto_detect_wxwork_db_dir", return_value=None):
            try:
                _load_wxwork_config()
            except RuntimeError as exc:
                assert "未能自动检测企业微信数据目录" in str(exc)
            else:
                raise AssertionError("expected RuntimeError")
