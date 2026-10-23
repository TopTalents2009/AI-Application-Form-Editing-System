import os
import tempfile
import unittest

from shared import synced_store


class SyncedStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = synced_store.SYNCED_ROOT
        synced_store.SYNCED_ROOT = os.path.join(self.tmp.name, "synced")

    def tearDown(self):
        synced_store.SYNCED_ROOT = self._orig
        self.tmp.cleanup()

    def test_save_and_list_source(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-A",
            "account_id": "1234567890",
            "operator_name": "alice",
            "username": "alice",
            "host": "192.168.2.14",
            "sessions": [{
                "id": "R:1",
                "display_name": "家",
                "session_type": 2,
                "last_time": "2026-09-16 10:00",
                "messages": [{"text": "hello", "sender": "bob", "time_text": "10:00"}],
            }],
        })
        self.assertEqual(result["saved_sessions"], 1)
        sources = synced_store.list_sources()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["computer_name"], "PC-A")
        self.assertEqual(sources[0]["operator_name"], "alice")
        sessions = synced_store.list_sessions(result["source_id"])
        self.assertEqual(sessions[0]["display_name"], "家")
        messages = synced_store.list_messages(result["source_id"], "R:1")
        self.assertEqual(messages[0]["text"], "hello")

    def test_image_message_gets_media_url(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-IMG",
            "sessions": [{
                "id": "R:9",
                "display_name": "图",
                "messages": [{
                    "text": "[图片] http://wework.qpic.cn/wwpic3az/1_a_2/0P",
                    "msg_type": 4,
                    "sender": "bob",
                }],
            }],
        })
        messages = synced_store.list_messages(result["source_id"], "R:9")
        self.assertEqual(messages[0]["text"], "[图片]")
        self.assertEqual(messages[0]["media_url"], "https://wework.qpic.cn/wwpic3az/1_a_2/0")

    def test_file_message_gets_attachment_name(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-FILE",
            "sessions": [{
                "id": "R:8",
                "display_name": "文件",
                "messages": [{
                    "text": "[图片/文件] 文件] 文件] frp_0.70.1_windows_amd64.zip",
                    "msg_type": 15,
                    "sender": "bob",
                    "message_id": 99,
                }],
            }],
        })
        messages = synced_store.list_messages(result["source_id"], "R:8")
        self.assertEqual(messages[0]["attachment_name"], "frp_0.70.1_windows_amd64.zip")
        self.assertTrue(messages[0]["has_attachment"])

    def test_rewrites_legacy_unknown_type_label(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-UNKNOWN",
            "sessions": [{
                "id": "R:10775879994473370",
                "display_name": "东莞大朗",
                "messages": [{
                    "text": "[未知(1002)]",
                    "msg_type": 1002,
                    "msg_type_label": "未知(1002)",
                    "sender": "小唐",
                    "time_text": "2026-09-29 10:59",
                    "message_id": 166066,
                }],
            }],
        })
        messages = synced_store.list_messages(result["source_id"], "R:10775879994473370")
        self.assertEqual(messages[0]["text"], "[名片]")
        self.assertEqual(messages[0]["msg_type_label"], "名片")

    def test_text_filename_mention_is_not_a_file(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-TXT",
            "sessions": [{
                "id": "R:3",
                "messages": [{
                    "text": "[文本] prompt_content.txt",
                    "msg_type": 2,
                    "attachment_name": "prompt_content.txt",
                    "has_attachment": True,
                }],
            }],
        })
        messages = synced_store.list_messages(result["source_id"], "R:3")
        self.assertEqual(messages[0]["attachment_name"], "")
        self.assertIn("prompt_content.txt", messages[0]["text"])

    def test_merge_sessions_keeps_old(self):
        synced_store.save_ingest({
            "computer_name": "PC-A",
            "sessions": [{"id": "R:1", "display_name": "家", "messages": [{"text": "a"}]}],
        })
        result = synced_store.save_ingest({
            "computer_name": "PC-A",
            "sessions": [{"id": "R:2", "display_name": "工作", "messages": [{"text": "b"}]}],
        })
        sessions = synced_store.list_sessions(result["source_id"])
        names = {s["display_name"] for s in sessions}
        self.assertEqual(names, {"家", "工作"})
        self.assertEqual(sessions[0]["display_name"], "工作")
        self.assertTrue(sessions[0]["synced_at"])

    def test_resynced_session_moves_to_top(self):
        synced_store.save_ingest({
            "computer_name": "PC-A",
            "sessions": [{"id": "R:1", "display_name": "家", "messages": [{"text": "a"}]}],
        })
        synced_store.save_ingest({
            "computer_name": "PC-A",
            "sessions": [{"id": "R:2", "display_name": "工作", "messages": [{"text": "b"}]}],
        })
        result = synced_store.save_ingest({
            "computer_name": "PC-A",
            "sessions": [{"id": "R:1", "display_name": "家", "messages": [{"text": "a2"}]}],
        })
        sessions = synced_store.list_sessions(result["source_id"])
        self.assertEqual(sessions[0]["display_name"], "家")

    def test_filter_messages_by_date(self):
        rows = [
            {"time_text": "2026-09-14 09:46", "text": "old"},
            {"time_text": "2026-09-22 09:22", "text": "new"},
            {"time_text": "09:22", "text": "nodate"},
        ]
        kept = synced_store.filter_messages_by_date(rows, "2026-09-22", "2026-09-22")
        self.assertEqual([item["text"] for item in kept], ["new"])

    def test_message_cursors_use_highest_id(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-C",
            "sessions": [{
                "id": "S:1",
                "display_name": "张三",
                "messages": [
                    {"message_id": 4, "text": "old", "time_text": "2026-09-22 08:30"},
                    {"message_id": 9, "text": "new", "time_text": "2026-09-22 09:22"},
                ],
            }],
        })
        synced_store.save_ingest({
            "computer_name": "PC-C",
            "sessions": [{
                "id": "S:1",
                "display_name": "张三",
                "messages": [{"message_id": 10, "text": "later", "time_text": "2026-09-22 10:00"}],
            }],
        })
        self.assertEqual(synced_store.message_cursors(result["source_id"]), {"S:1": 10})

    def test_merge_keeps_old_messages(self):
        first = synced_store.save_ingest({
            "computer_name": "PC-M",
            "sessions": [{
                "id": "S:1_2",
                "display_name": "张三",
                "session_type": 1,
                "messages": [{"message_id": 1, "text": "old", "time_text": "2026-01-01 10:00"}],
            }],
        })
        synced_store.save_ingest({
            "computer_name": "PC-M",
            "sessions": [{
                "id": "S:1_2",
                "display_name": "张三",
                "session_type": 1,
                "messages": [{"message_id": 2, "text": "new", "time_text": "2026-01-02 10:00"}],
            }],
        })
        messages = synced_store.list_messages(first["source_id"], "S:1_2", limit=0)
        self.assertEqual([m["text"] for m in messages], ["old", "new"])

    def test_list_messages_returns_latest_window(self):
        items = [
            {"message_id": i, "text": str(i), "time_text": f"2026-01-01 10:{i:02d}"}
            for i in range(5)
        ]
        result = synced_store.save_ingest({
            "computer_name": "PC-L",
            "sessions": [{"id": "S:9", "display_name": "李四", "messages": items}],
        })
        latest = synced_store.list_messages(result["source_id"], "S:9", limit=2)
        self.assertEqual([m["text"] for m in latest], ["3", "4"])

    def test_find_source_id_reuses_computer_without_account(self):
        synced_store.save_ingest({
            "computer_name": "SKY-PC",
            "account_id": "1688850000000001",
            "operator_name": "唐利萍",
            "host": "192.168.2.11",
            "sessions": [{"id": "S:1", "display_name": "张三", "messages": [{"text": "hi"}]}],
        })
        self.assertEqual(
            synced_store.find_source_id("SKY-PC", "", "192.168.2.11"),
            "SKY-PC-1688850000000001",
        )

    def test_delete_source_removes_folder(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-DEL",
            "operator_name": "alice",
            "sessions": [{"id": "S:1", "display_name": "家", "messages": [{"text": "hi"}]}],
        })
        sid = result["source_id"]
        self.assertTrue(os.path.isdir(os.path.join(synced_store.SYNCED_ROOT, sid)))
        self.assertTrue(synced_store.delete_source(sid))
        self.assertFalse(os.path.isdir(os.path.join(synced_store.SYNCED_ROOT, sid)))
        self.assertEqual(synced_store.list_sources(), [])
        self.assertFalse(synced_store.delete_source(sid))

    def test_delete_source_rejects_local(self):
        self.assertFalse(synced_store.delete_source("local"))
        self.assertFalse(synced_store.delete_source("../local"))

    def test_list_sources_skips_corrupt_meta(self):
        synced_store.save_ingest({
            "computer_name": "PC-OK",
            "sessions": [{"id": "S:1", "display_name": "ok", "messages": [{"text": "hi"}]}],
        })
        bad = os.path.join(synced_store.SYNCED_ROOT, "PC-BAD")
        os.makedirs(bad, exist_ok=True)
        with open(os.path.join(bad, "meta.json"), "wb") as handle:
            handle.write(b"\x00" * 64)
        with open(os.path.join(bad, "sessions.json"), "w", encoding="utf-8") as handle:
            handle.write("not-json")
        sources = {item["id"]: item for item in synced_store.list_sources()}
        self.assertIn("PC-OK", sources)
        self.assertEqual(sources["PC-BAD"]["computer_name"], "PC-BAD")
        self.assertEqual(sources["PC-BAD"]["session_count"], 0)


if __name__ == "__main__":
    unittest.main()
