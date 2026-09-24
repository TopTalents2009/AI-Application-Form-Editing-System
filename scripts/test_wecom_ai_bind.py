# -*- coding: utf-8 -*-
"""版本绑定：不调 Gemini，校验清洗与时间窗纯函数。"""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_ai_bind import (
    DEFAULT_BIND_PROMPTS,
    PROMPT_NAME_ID,
    PROMPT_SEMANTIC,
    PROMPT_SEMANTIC_STRICT,
    PROMPT_TIME_WINDOW,
    _SEMANTIC_PROMPT,
    _SEMANTIC_PROMPT_TEXTS,
    _SEMANTIC_STRICT_PROMPT,
    bind_opinions_time_window,
    build_talent_versions,
    collect_valid_message_ids,
    extract_catalog,
    sanitize_bind_payload,
)


def test_prompt_ids():
    assert PROMPT_TIME_WINDOW == "time_window"
    assert PROMPT_NAME_ID == "name_id"
    assert PROMPT_SEMANTIC == "semantic"
    assert PROMPT_SEMANTIC_STRICT == "semantic_strict"
    assert DEFAULT_BIND_PROMPTS == [
        "time_window",
        "name_id",
        "semantic",
        "semantic_strict",
    ]


def test_semantic_prompt_texts_exist():
    assert PROMPT_SEMANTIC in _SEMANTIC_PROMPT_TEXTS
    assert PROMPT_SEMANTIC_STRICT in _SEMANTIC_PROMPT_TEXTS
    assert _SEMANTIC_PROMPT_TEXTS[PROMPT_SEMANTIC] == _SEMANTIC_PROMPT
    assert _SEMANTIC_PROMPT_TEXTS[PROMPT_SEMANTIC_STRICT] == _SEMANTIC_STRICT_PROMPT
    assert "禁止用人名" in _SEMANTIC_STRICT_PROMPT
    assert "宁可少绑" in _SEMANTIC_STRICT_PROMPT
    assert len(_SEMANTIC_STRICT_PROMPT) > 80


def test_sanitize_drops_unknown_message_id():
    valid = {"11", "22", "33"}
    raw = {
        "talents": [{
            "attachId": "51104",
            "label": "51104",
            "versions": [{
                "chatVersion": "chat-1",
                "messageId": "11",
                "opinions": [
                    {"messageId": "22", "kind": "file", "filename": "a.txt"},
                    {"messageId": "999", "kind": "file", "filename": "编造"},
                ],
            }, {
                "chatVersion": "chat-2",
                "messageId": "888",
                "opinions": [],
            }],
        }],
        "unboundOpinions": [{"messageId": "33"}, {"messageId": "404"}],
        "conflicts": [{"messageId": "777", "reason": "x"}],
    }
    got = sanitize_bind_payload(raw, valid)
    assert len(got["talents"]) == 1
    assert len(got["talents"][0]["versions"]) == 1
    assert got["talents"][0]["versions"][0]["messageId"] == "11"
    assert len(got["talents"][0]["versions"][0]["opinions"]) == 1
    assert got["talents"][0]["versions"][0]["opinions"][0]["messageId"] == "22"
    assert [x["messageId"] for x in got["unboundOpinions"]] == ["33"]
    assert got["conflicts"] == []


def test_time_window_between_versions():
    versions = [
        {"chatVersion": "chat-1", "time": "2026-09-01 10:00", "opinions": []},
        {"chatVersion": "chat-2", "time": "2026-09-03 10:00", "opinions": []},
    ]
    opinions = [
        {"messageId": "o1", "time": "2026-09-01 11:00", "kind": "file", "filename": "意见1"},
        {"messageId": "o2", "time": "2026-09-02 12:00", "kind": "file", "filename": "意见2"},
        {"messageId": "o3", "time": "2026-09-03 11:00", "kind": "file", "filename": "意见3"},
        {"messageId": "o4", "time": "2026-08-31 09:00", "kind": "file", "filename": "太早"},
    ]
    bound, unbound = bind_opinions_time_window(versions, opinions, window_hours=48)
    v1_ops = bound[0]["opinions"]
    v2_ops = bound[1]["opinions"]
    assert [x["messageId"] for x in v1_ops] == ["o1", "o2"]
    assert [x["messageId"] for x in v2_ops] == ["o3"]
    assert [x["messageId"] for x in unbound] == ["o4"]


def test_time_window_last_version_uses_window_hours():
    versions = [{"chatVersion": "chat-1", "time": "2026-09-01 10:00", "opinions": []}]
    opinions = [
        {"messageId": "ok", "time": "2026-09-02 09:00", "kind": "file", "filename": "内"},
        {"messageId": "late", "time": "2026-09-05 10:01", "kind": "file", "filename": "超窗"},
    ]
    bound, unbound = bind_opinions_time_window(versions, opinions, window_hours=48)
    assert [x["messageId"] for x in bound[0]["opinions"]] == ["ok"]
    assert [x["messageId"] for x in unbound] == ["late"]


def test_time_window_unbounded_last_version():
    versions = [{"chatVersion": "chat-1", "time": "2026-09-01 10:00", "opinions": []}]
    opinions = [
        {"messageId": "late", "time": "2026-09-20 10:00", "kind": "file", "filename": "很晚"},
    ]
    bound, unbound = bind_opinions_time_window(versions, opinions, window_hours=0)
    assert [x["messageId"] for x in bound[0]["opinions"]] == ["late"]
    assert unbound == []


def test_mock_list_by_attach():
    rows = [
        {"id": 2, "attach_id": "51104", "version": "v2", "version_n": 2,
         "created_at": datetime(2026, 9, 2, 12, 0, 0), "person_name": "张三"},
        {"id": 1, "attach_id": "51104", "version": "v1", "version_n": 1,
         "created_at": datetime(2026, 9, 1, 9, 0, 0), "person_name": "张三"},
    ]

    def mock_list(aid: str):
        return rows if aid == "51104" else []

    apps = [
        {"attachId": "51104", "messageId": "101", "time": "2026-09-01 10:00", "filename": "51104_申报书.docx"},
        {"attachId": "51104", "messageId": "102", "time": "2026-09-02 15:00", "filename": "51104_申报书2.docx"},
    ]
    talents = build_talent_versions(apps, list_by_attach=mock_list, can_read=lambda r: True)
    assert len(talents) == 1
    vers = talents[0]["versions"]
    assert vers[0]["chatVersion"] == "chat-1"
    assert vers[1]["chatVersion"] == "chat-2"
    assert vers[0]["libraryVersion"]["version"] == "v1"
    assert vers[1]["libraryVersion"]["version"] == "v2"
    assert vers[0]["libraryVersion"]["source"] == "talent_app_files"


def test_extract_catalog_valid_ids():
    messages = [
        {"message_id": 1, "time_text": "2026-09-01 10:00", "attachment_name": "51104_申报书.docx", "has_attachment": True},
        {"message_id": 2, "time_text": "2026-09-01 11:00", "attachment_name": "51104_修改意见.txt", "has_attachment": True},
    ]
    cat = extract_catalog(messages, session_id="R:x", session_name="测试群")
    ids = collect_valid_message_ids(cat)
    assert "1" in ids
    assert "2" in ids
    assert len(cat.get("opinions") or []) == 1


if __name__ == "__main__":
    test_prompt_ids()
    test_semantic_prompt_texts_exist()
    test_sanitize_drops_unknown_message_id()
    test_time_window_between_versions()
    test_time_window_last_version_uses_window_hours()
    test_time_window_unbounded_last_version()
    test_mock_list_by_attach()
    test_extract_catalog_valid_ids()
    print("ALL OK")
