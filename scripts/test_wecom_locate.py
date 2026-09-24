# -*- coding: utf-8 -*-
"""文件汇总定位原文：按 message_id 切到各群记录所在页。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_client import find_message_index, slice_around, message_ids_of, merge_copy_peers
from app.wecom_locate import pick_copy_locate


def test_message_ids_include_copies():
    m = {"message_id": 11, "copies": [{"message_id": 11}, {"message_id": 99}]}
    assert message_ids_of(m) == ["11", "99"]


def test_find_by_copy_id():
    merged = [
        {"message_id": 1, "time_text": "2026-09-01 10:00"},
        {"message_id": 2, "copies": [{"message_id": 88}], "time_text": "2026-09-01 11:00"},
        {"message_id": 3, "time_text": "2026-09-01 12:00"},
    ]
    assert find_message_index(merged, "88") == 1
    assert find_message_index(merged, "", "2026-09-01 12:00") == 2
    assert find_message_index(merged, "404") == -1


def test_slice_around_keeps_context():
    merged = [{"message_id": i, "time_text": str(i)} for i in range(1, 41)]
    got = slice_around(merged, limit=10, around_id="25")
    assert got
    page = merged[got["start"]:got["end"]]
    ids = [str(x["message_id"]) for x in page]
    assert "25" in ids
    assert got["hit"] == ids.index("25")
    assert got["end"] - got["start"] == 10
    assert got["from_end"] == 40 - got["end"]


def test_slice_around_end_of_list():
    merged = [{"message_id": i} for i in range(1, 8)]
    got = slice_around(merged, limit=10, around_id="7")
    assert got["start"] == 0
    assert got["end"] == 7
    assert got["from_end"] == 0
    assert got["hit"] == 6


def test_merge_copy_peers_adds_session_computers():
    copies = [{
        "source_id": "pc1",
        "source_label": "前台",
        "message_id": 88,
        "session_id": "R:abc",
    }]
    peers = [
        {"source_id": "pc1", "label": "前台"},
        {"source_id": "pc2", "label": "财务室", "kind": "remote"},
        {"id": "pc3", "label": "研发"},
    ]
    got = merge_copy_peers(copies, peers)
    ids = [x["source_id"] for x in got]
    assert ids == ["pc1", "pc2", "pc3"]
    by = {x["source_id"]: x for x in got}
    assert by["pc2"]["message_id"] == 88
    assert by["pc2"]["session_id"] == "R:abc"
    assert by["pc3"]["message_id"] == 88


def test_pick_copy_locate_uses_clicked_session():
    copies = [
        {"source_id": "pc1", "source_label": "前台", "message_id": 11, "session_id": "R:aaa"},
        {"source_id": "pc2", "source_label": "财务", "message_id": 99, "session_id": "R:bbb"},
    ]
    got = pick_copy_locate(copies[1], fallback_session_id="R:zzz", fallback_session_name="默认群")
    assert got["sessionId"] == "R:bbb"
    assert got["messageId"] == "99"
    assert got["sessionName"] == "默认群"


def test_pick_copy_locate_fallback():
    got = pick_copy_locate({"message_id": 7}, fallback_session_id="R:f", fallback_session_name="群F")
    assert got["sessionId"] == "R:f"
    assert got["messageId"] == "7"
    assert got["sessionName"] == "群F"


if __name__ == "__main__":
    test_message_ids_include_copies()
    test_find_by_copy_id()
    test_slice_around_keeps_context()
    test_slice_around_end_of_list()
    test_merge_copy_peers_adds_session_computers()
    test_pick_copy_locate_uses_clicked_session()
    test_pick_copy_locate_fallback()
    print("ALL OK")
