# -*- coding: utf-8 -*-
"""缺失附件二次检索不应把同一文件再追加一遍。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.attachments import (
    _dedupe_attach_items,
    _name_key,
    _seed_known_keys,
    leftover_lines,
)


def test_seed_local_roundtrip():
    path = r"C:\附件\4497\护照\护照.pdf"
    result = {
        "items": [{
            "kind": "护照", "source": "local", "filename": "护照.pdf",
            "localPath": path, "download": "/api/tasks/t/ext-files/a1",
        }],
        "private": {"a1": {"source": "local", "url": "", "filename": "护照.pdf", "path": path}},
    }
    keys = _seed_known_keys(result)
    assert "local:" + path in keys
    assert path in keys
    assert _name_key("local", "护照.pdf", "护照") in keys
    assert _name_key("local", "护照.pdf", "") in keys
    print("local known keys ok")


def test_seed_wecom_from_copies():
    result = {
        "items": [{"kind": "护照", "source": "wecom", "filename": "护照.pdf"}],
        "private": {
            "a1": {
                "source": "wecom", "url": "", "filename": "护照.pdf",
                "copies": [{"source_id": "pc1", "message_id": 88}],
            }
        },
    }
    keys = _seed_known_keys(result)
    assert "wecom:pc1:88" in keys
    assert _name_key("wecom", "护照.pdf", "") in keys
    print("wecom known keys ok")


def test_dedupe_same_filename():
    items = [
        {"source": "wecom", "kind": "护照", "filename": "护照.pdf", "id": "a", "download": "/d/a"},
        {"source": "wecom", "kind": "护照", "filename": "护照.pdf", "id": "b", "download": "/d/b"},
        {"source": "pool", "kind": "护照", "filename": "护照.pdf", "id": "c", "download": "/d/c"},
    ]
    got = _dedupe_attach_items(items)
    assert [x["id"] for x in got] == ["a", "c"], got
    print("item dedupe ok")


def test_leftover_unique_hits():
    result = {
        "needed": ["护照"],
        "items": [
            {"kind": "护照", "source": "wecom", "filename": "护照.pdf", "download": "/d/a"},
            {"kind": "护照", "source": "wecom", "filename": "护照.pdf", "download": "/d/a"},
        ],
    }
    lines = leftover_lines(result)
    assert len(lines) == 1
    assert lines[0].count("护照.pdf") == 1
    print("leftover unique ok")


if __name__ == "__main__":
    test_seed_local_roundtrip()
    test_seed_wecom_from_copies()
    test_dedupe_same_filename()
    test_leftover_unique_hits()
    print("all ok")
