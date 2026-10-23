import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "wecom_sync_agent", os.path.join(ROOT, "agent", "wecom_sync_agent.py")
)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class WecomSyncAgentTests(unittest.TestCase):
    def test_splits_group_and_direct_chats(self):
        rows = [
            {"id": "R:1", "session_type": 2, "display_name": "家"},
            {"id": "1688", "session_type": 1, "display_name": "张三"},
            {"id": "wxid_a@chatroom", "session_type": 0, "display_name": "旧群"},
        ]
        groups, singles = agent.split_conversations(rows)
        self.assertEqual([g["display_name"] for g in groups], ["家", "旧群"])
        self.assertEqual([s["display_name"] for s in singles], ["张三"])

    def test_default_server_replaces_localhost(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = os.path.join(tmp, "settings.json")
            with open(settings, "w", encoding="utf-8") as f:
                json.dump({"server_url": "http://127.0.0.1:8767", "token": "old"}, f)
            with mock.patch.object(agent, "SETTINGS_FILE", settings):
                data = agent._load_settings()
            self.assertEqual(data["server_url"], agent.DEFAULT_SERVER_URL)

    def test_fetch_ingest_info_reads_token(self):
        payload = json.dumps({"token": "abc123", "urls": ["http://192.168.2.25:8767"]}).encode()

        class FakeResp:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch.object(agent, "_urlopen", return_value=FakeResp()):
            info = agent.fetch_ingest_info("http://192.168.2.25:8767")
        self.assertEqual(info["token"], "abc123")

    def test_direct_opener_ignores_http_proxy(self):
        proxies = {}
        for handler in agent._DIRECT.handlers:
            if isinstance(handler, agent.request.ProxyHandler):
                proxies = dict(handler.proxies)
        self.assertEqual(proxies, {})

    def test_auto_sync_all_when_none_checked(self):
        rows = [
            {"id": "R:1", "display_name": "家"},
            {"id": "2", "display_name": "张三"},
        ]
        selected = agent.sessions_for_auto_sync(rows, [])
        self.assertEqual([s["id"] for s in selected], ["R:1", "2"])

    def test_auto_sync_includes_new_direct_chats(self):
        rows = [
            {"id": "R:1", "display_name": "家"},
            {"id": "2", "display_name": "张三"},
        ]
        selected = agent.sessions_for_auto_sync(rows, ["R:1"], seen_ids=["R:1"])
        self.assertEqual([s["id"] for s in selected], ["R:1", "2"])

    def test_auto_sync_filters_checked_ids(self):
        rows = [
            {"id": "R:1", "display_name": "家"},
            {"id": "2", "display_name": "张三"},
        ]
        selected = agent.sessions_for_auto_sync(rows, ["2"], seen_ids=["R:1", "2"])
        self.assertEqual([s["id"] for s in selected], ["2"])

    def test_checked_ids_for_render_includes_unseen_direct(self):
        rows = [
            {"id": "R:1", "display_name": "家"},
            {"id": "2", "display_name": "张三"},
        ]
        checked = agent.checked_ids_for_render(rows, ["R:1"], ["R:1"])
        self.assertEqual(checked, ["R:1", "2"])

    def test_auto_sync_interval_is_five_minutes(self):
        self.assertEqual(agent.AUTO_SYNC_INTERVAL_MS, 5 * 60 * 1000)

    def test_hidden_run_uses_no_window_flag(self):
        if os.name != "nt":
            self.skipTest("Windows only")
        self.assertTrue(agent._no_window_flags() & __import__("subprocess").CREATE_NO_WINDOW)
        captured = {}

        def fake_run(*args, **kwargs):
            captured.update(kwargs)
            class Result:
                stdout = ""
            return Result()

        with mock.patch.object(agent.subprocess, "run", side_effect=fake_run):
            agent._wxwork_running()
        self.assertTrue(captured.get("creationflags", 0) & __import__("subprocess").CREATE_NO_WINDOW)

    def test_autostart_command_uses_background_flag(self):
        cmd = agent.autostart_command()
        self.assertIn("--background", cmd)
        self.assertTrue(cmd.startswith('"'))

    def test_disable_autostart_deletes_run_value(self):
        deleted = []

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        fake = mock.MagicMock()
        fake.OpenKey.return_value = FakeKey()
        fake.DeleteValue.side_effect = lambda _key, name: deleted.append(name)
        fake.HKEY_CURRENT_USER = 1
        fake.KEY_SET_VALUE = 2
        with mock.patch.dict("sys.modules", {"winreg": fake}):
            agent.disable_autostart()
        self.assertEqual(deleted, [agent.AUTOSTART_NAME])

    def test_name_from_direct_chat_id(self):
        users = {1688854697859602: "陈旭", 1688855370846072: "吴贤腾"}
        name = agent._name_from_conversation_id(
            "S:1688854697859602_1688855370846072", users, 1688855370846072
        )
        self.assertEqual(name, "陈旭")

    def test_resolve_sender_uses_user_map(self):
        users = {1688855087850576: "张浪"}
        self.assertEqual(
            agent._resolve_sender(1688855087850576, "R:1", users, {}),
            "张浪",
        )
        ident = agent.client_identity("张三")
        self.assertEqual(ident["operator_name"], "张三")
        self.assertEqual(ident["username"], "张三")
        self.assertTrue(ident["computer_name"])

    def test_windows_username_is_not_real_name(self):
        with mock.patch.dict(os.environ, {"USERNAME": "Administrator"}):
            self.assertFalse(agent.looks_like_real_name(""))
            self.assertFalse(agent.looks_like_real_name("A"))
            self.assertFalse(agent.looks_like_real_name("Administrator"))
            self.assertTrue(agent.looks_like_real_name("张三"))

    def test_load_settings_clears_windows_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = os.path.join(tmp, "settings.json")
            with open(settings, "w", encoding="utf-8") as f:
                json.dump({"operator_name": "Administrator"}, f)
            with mock.patch.object(agent, "SETTINGS_FILE", settings):
                with mock.patch.dict(os.environ, {"USERNAME": "Administrator"}):
                    data = agent._load_settings()
            self.assertEqual(data["operator_name"], "")

    def test_heartbeat_interval_is_five_seconds(self):
        self.assertEqual(agent.HEARTBEAT_INTERVAL_MS, 5 * 1000)

    def test_process_file_job_reports_missing(self):
        with mock.patch.object(agent, "find_local_attachment", return_value=""):
            with mock.patch.object(agent, "report_file_job", return_value={"ok": True}) as report:
                status = agent.process_file_job("http://x", "tok", {
                    "job_id": "abc",
                    "message_id": 1,
                    "filename": "a.zip",
                })
        self.assertEqual(status, "missing")
        report.assert_called_once_with(
            "http://x", "tok", "abc", "missing", "对方电脑未缓存该文件"
        )

    def test_process_file_job_uploads_when_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.zip")
            with open(path, "wb") as f:
                f.write(b"hello")
            with mock.patch.object(agent, "find_local_attachment", return_value=path):
                with mock.patch.object(agent, "upload_attachment", return_value={"ok": True}) as up:
                    status = agent.process_file_job("http://x", "tok", {
                        "job_id": "abc",
                        "message_id": 12,
                        "filename": "a.zip",
                    })
        self.assertEqual(status, "ready")
        up.assert_called_once()

    def test_version_newer(self):
        self.assertTrue(agent.version_newer("2026.09.16.2", "2026.09.16.1"))
        self.assertFalse(agent.version_newer("2026.09.16.1", "2026.09.16.1"))
        self.assertFalse(agent.version_newer("2026.09.16.1", "2026.09.16.2"))

    def test_should_apply_update_skips_same_hash(self):
        info = {"version": "9.9.9", "sha256": "abc", "size": 12}
        self.assertFalse(agent.should_apply_update("1.0", info, "ABC"))
        self.assertTrue(agent.should_apply_update("1.0", info, "ddd"))
        self.assertTrue(agent.should_apply_update("9.9.9", info, "ddd"))
        self.assertFalse(agent.should_apply_update("9.9.9", {"version": "1.0", "sha256": "ddd", "size": 12}, "abc"))
        self.assertFalse(agent.should_apply_update("9.9.9", {"version": "9.9.9", "sha256": "", "size": 1}))

    def test_resolve_update_url(self):
        self.assertEqual(
            agent.resolve_update_url("http://192.168.2.25:8767", "/api/ingest/agent-exe"),
            "http://192.168.2.25:8767/api/ingest/agent-exe",
        )
        self.assertEqual(
            agent.resolve_update_url("http://x", "http://cdn/a.exe"),
            "http://cdn/a.exe",
        )

    def test_download_agent_exe_checks_hash(self):
        payload = b"new-exe"
        sha = hashlib.sha256(payload).hexdigest()

        class FakeResp:
            def read(self, _n=None):
                if getattr(self, "done", False):
                    return b""
                self.done = True
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "WeComSyncAgent.exe")
            with mock.patch.object(agent, "_urlopen", return_value=FakeResp()):
                path = agent.download_agent_exe("http://x/a", "tok", dest, len(payload), sha)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), payload)

    def test_write_updater_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(agent, "WORK_DIR", tmp):
                script = agent.write_updater_script(123, os.path.join(tmp, "new.exe"), r"C:\app\WeComSyncAgent.exe", ["--background"])
            self.assertTrue(os.path.isfile(script))
            with open(script, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Copy-Item", text)
            self.assertIn("--background", text)
            self.assertIn("Start-Process", text)
            self.assertIn("Stop-Process", text)
            self.assertIn("agent.lock", text)
            self.assertIn("copy failed", text)
            self.assertNotIn("launching existing exe", text)
            plan = os.path.join(tmp, "update", "plan.json")
            with open(plan, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["pid"], 123)
            self.assertEqual(data["args"], ["--background"])

    def test_changelog_since_and_notice_text(self):
        entries = agent.parse_changelog(
            "## 2026.09.17.2\n- 打开助手可看更新说明\n\n## 2026.09.17.1\n- 修复短回复\n"
        )
        items = agent.changelog_since(entries, "2026.09.16.2", "2026.09.17.2")
        self.assertEqual([i["version"] for i in items], ["2026.09.17.2", "2026.09.17.1"])
        text = agent.format_update_notice("2026.09.17.2", items)
        self.assertIn("已更新到 2026.09.17.2", text)
        self.assertIn("修复短回复", text)
        self.assertEqual(agent.changelog_since(entries, "2026.09.17.2", "2026.09.17.2"), [])

    def test_existing_install_without_seen_version(self):
        self.assertTrue(agent.looks_like_existing_install({"setup_done": True}))
        self.assertFalse(agent.looks_like_existing_install({}))

    def test_incremental_export_skips_synced_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_dir = os.path.join(tmp, "wxwork_decrypted")
            os.makedirs(db_dir)
            db_path = os.path.join(db_dir, "message.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE message_table ("
                "message_id INTEGER, conversation_id TEXT, send_time INTEGER, "
                "sender_id INTEGER, content_type INTEGER, content TEXT, "
                "extra_content TEXT, local_extra_content TEXT)"
            )
            conn.executemany(
                "INSERT INTO message_table VALUES (?, ?, ?, 1, 2, ?, '', '')",
                [
                    (1, "R:1", 1700000000, "old"),
                    (2, "R:1", 1700000060, "new"),
                    (3, "R:2", 1700000120, "other"),
                ],
            )
            conn.commit()
            conn.close()
            with mock.patch.object(agent, "DECRYPTED_DIR", db_dir):
                first, pending = agent.export_incremental(["R:1", "R:2"], {}, {})
                self.assertEqual([m["message_id"] for m in first["R:1"]], [1, 2])
                self.assertEqual(pending["R:1"]["message_table"], 2)
                cursors = {"R:1": {"message_table": 1}, "R:2": {"message_table": 3}}
                second, pending2 = agent.export_incremental(["R:1", "R:2"], {}, {}, cursors)
            self.assertEqual(list(second), ["R:1"])
            self.assertEqual([m["text"] for m in second["R:1"]], ["new"])
            self.assertEqual(pending2["R:1"]["message_table"], 2)
            self.assertNotIn("R:2", pending2)

    def test_push_advances_cursor_only_after_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_dir = os.path.join(tmp, "wxwork_decrypted")
            os.makedirs(db_dir)
            conn = sqlite3.connect(os.path.join(db_dir, "message.db"))
            conn.execute(
                "CREATE TABLE message_table ("
                "message_id INTEGER, conversation_id TEXT, send_time INTEGER, "
                "sender_id INTEGER, content_type INTEGER, content TEXT, "
                "extra_content TEXT, local_extra_content TEXT)"
            )
            conn.execute(
                "INSERT INTO message_table VALUES (7, 'R:1', 1700000000, 1, 2, 'hi', '', '')"
            )
            conn.commit()
            conn.close()
            settings = os.path.join(tmp, "settings.json")
            cursors = {}
            with mock.patch.object(agent, "DECRYPTED_DIR", db_dir), \
                    mock.patch.object(agent, "SETTINGS_FILE", settings), \
                    mock.patch.object(agent, "_post_json", side_effect=RuntimeError("down")):
                with self.assertRaises(RuntimeError):
                    agent.push_sessions(
                        "http://127.0.0.1:8767", "tok",
                        [{"id": "R:1", "display_name": "家", "session_type": 2, "last_time": "", "msg_count": 1}],
                        {}, lambda _msg: None, cursors=cursors,
                    )
            self.assertEqual(cursors, {})
            with mock.patch.object(agent, "DECRYPTED_DIR", db_dir), \
                    mock.patch.object(agent, "SETTINGS_FILE", settings), \
                    mock.patch.object(agent, "_post_json", return_value={"saved_sessions": 1}):
                result = agent.push_sessions(
                    "http://127.0.0.1:8767", "tok",
                    [{"id": "R:1", "display_name": "家", "session_type": 2, "last_time": "", "msg_count": 1}],
                    {}, lambda _msg: None, cursors=cursors,
                )
            self.assertEqual(result["new_messages"], 1)
            self.assertEqual(cursors["R:1"]["message_table"], 7)
            with mock.patch.object(agent, "DECRYPTED_DIR", db_dir), \
                    mock.patch.object(agent, "SETTINGS_FILE", settings), \
                    mock.patch.object(agent, "_post_json", return_value={"saved_sessions": 1}) as post:
                again = agent.push_sessions(
                    "http://127.0.0.1:8767", "tok",
                    [{"id": "R:1", "display_name": "家", "session_type": 2, "last_time": "", "msg_count": 1}],
                    {}, lambda _msg: None, cursors=cursors,
                )
            post.assert_not_called()
            self.assertEqual(again["new_messages"], 0)

    def test_decrypt_failure_message_includes_tool_output(self):
        msg = agent.decrypt_failure_message(
            "提取密钥失败",
            ["[+] scanning", "[!] 未能自动检测企业微信数据目录"],
            1,
        )
        self.assertIn("退出码 1", msg)
        self.assertIn("未能自动检测企业微信数据目录", msg)

    def test_guard_systemexit_converts_nonzero(self):
        with self.assertRaises(RuntimeError) as ctx:
            agent._guard_systemexit(
                lambda: (_ for _ in ()).throw(SystemExit(1)),
                "提取密钥失败",
                ["未能自动检测企业微信数据目录"],
            )
        self.assertIn("提取密钥失败", str(ctx.exception))
        self.assertIn("未能自动检测", str(ctx.exception))

    def test_guard_systemexit_zero_is_ok(self):
        self.assertIsNone(agent._guard_systemexit(lambda: (_ for _ in ()).throw(SystemExit(0)), "x"))

    def test_account_roots_follow_redirected_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, "Docs")
            account = os.path.join(docs, "WXWork", "1688850279961055")
            os.makedirs(os.path.join(account, "Cache"))

            def fake_reg(_subkey, name):
                if name == "Personal":
                    return docs
                return ""

            env = {
                "USERPROFILE": os.path.join(tmp, "profile"),
                "APPDATA": os.path.join(tmp, "appdata"),
            }
            with mock.patch("shared.wxwork_paths._reg_value", side_effect=fake_reg), \
                    mock.patch.dict(os.environ, env):
                roots = agent.wxwork_account_roots()
            self.assertEqual(roots, [account])


if __name__ == "__main__":
    unittest.main()
