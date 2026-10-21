import os
import tempfile
import unittest

from shared import message_search, synced_store


class MessageSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = synced_store.SYNCED_ROOT
        synced_store.SYNCED_ROOT = os.path.join(self.tmp.name, "synced")

    def tearDown(self):
        synced_store.SYNCED_ROOT = self._orig
        self.tmp.cleanup()

    def test_tokenize_and_match(self):
        tokens = message_search.tokenize("  申报 清单 ")
        self.assertEqual(tokens, ["申报", "清单"])
        self.assertTrue(message_search.matches("材料申报建档清单", tokens))
        self.assertFalse(message_search.matches("只有申报", tokens))

    def test_snippet_centers_on_hit(self):
        text = "前面很多字" + "目标关键词" + "后面很多字"
        out = message_search.snippet(text, ["目标关键词"], radius=2)
        self.assertIn("目标关键词", out)
        self.assertTrue(out.startswith("…") or "前面" in out)

    def test_search_synced_messages(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-S",
            "operator_name": "张三",
            "sessions": [{
                "id": "R:1",
                "display_name": "家",
                "messages": [
                    {"text": "今晚吃饭", "sender": "bob", "time_text": "2026-09-16 12:00", "message_id": 1},
                    {"text": "申报材料已发", "sender": "alice", "time_text": "2026-09-16 13:00", "message_id": 2},
                ],
            }],
        })
        hits = message_search.search("申报", source_id=result["source_id"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["sender"], "alice")
        self.assertEqual(hits[0]["session_name"], "家")
        self.assertIn("申报", hits[0]["snippet"])

    def test_search_by_sender_and_session(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-S2",
            "sessions": [
                {
                    "id": "R:1",
                    "display_name": "家",
                    "messages": [{"text": "hello", "sender": "李四", "time_text": "2026-09-16 10:00"}],
                },
                {
                    "id": "R:2",
                    "display_name": "工作",
                    "messages": [{"text": "hello", "sender": "李四", "time_text": "2026-09-16 11:00"}],
                },
            ],
        })
        hits = message_search.search("李四", source_id=result["source_id"], session_id="R:2")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["session_id"], "R:2")

    def test_strips_form_label_noise(self):
        self.assertEqual(message_search.tokenize("(Name of ID) 护照"), ["护照"])
        self.assertEqual(message_search.tokenize("(Name of ID) : KALIY"), ["kaliy"])

    def test_partial_match_when_and_misses(self):
        result = synced_store.save_ingest({
            "computer_name": "PC-OR",
            "sessions": [{
                "id": "R:3",
                "display_name": "材料",
                "messages": [
                    {"text": "护照扫描件", "sender": "alice", "time_text": "2026-09-16 12:00", "message_id": 1},
                    {"text": "护照和工作证明", "sender": "bob", "time_text": "2026-09-16 13:00", "message_id": 2},
                ],
            }],
        })
        both = message_search.search("护照 工作证明", source_id=result["source_id"])
        self.assertEqual(len(both), 1)
        self.assertEqual(both[0]["sender"], "bob")
        partial = message_search.search("护照 不存在的词", source_id=result["source_id"])
        self.assertEqual(len(partial), 2)
