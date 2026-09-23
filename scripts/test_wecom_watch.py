# -*- coding: utf-8 -*-
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import _wecom_chat_block, resolve_watch_llm
from app.llm import _strip_think
from app.wecom_intent import parse_intent_json, parse_intent_list
from app.wecom_jev import judgment_prompt
from app.wecom_watch import (
    _has_new_material,
    _public_pending,
    _remember_uploaded,
    _repeat_without_new,
    _sync_mark,
    _open_pending_count,
)
from app.wecom_watch_llm import parse_watch_json, rule_opinion_ids


def test_watch_block():
    wecom = _wecom_chat_block({
        "wecomChat": {
            "watch": {
                "enabled": True,
                "mode": "auto",
                "baseUrl": "https://api.siliconflow.cn/v1",
                "apiKey": "sk-test",
                "model": "Qwen/Qwen3.5-4B",
            }
        }
    })
    w = wecom["watch"]
    assert w["model"] == "Qwen/Qwen3.5-4B"
    assert w["configured"] is True
    assert w["enabled"] is True
    assert "v1" in w["baseUrl"]


def test_parse_watch_json():
    got = parse_watch_json('{"hasOpinion":true,"opinions":[{"id":3,"confidence":"high"}],"reason":"改护照"}')
    assert got["hasOpinion"] is True
    assert got["opinions"][0]["id"] == 3
    assert got["opinions"][0]["confidence"] == "high"
    empty = parse_watch_json("```json\n{\"hasOpinion\":false,\"opinions\":[]}\n```")
    assert empty["hasOpinion"] is False


def test_strip_think():
    assert json.loads(_strip_think('<think>abc</think>{"ok":true}'))["ok"] is True


def test_rule_skips_chatter():
    hits = rule_opinion_ids([
        {"text": "收到", "has_attachment": False},
        {"text": "请把护照号码改成 E123", "has_attachment": False, "message_id": 2},
    ])
    assert len(hits) == 1
    assert "护照" in hits[0]["text"]


def test_judgment_prompt_keeps_chinese():
    text = judgment_prompt([{"id": 2, "text": "请把护照号码改成 E123", "rule": "text", "sender": "审核"}])
    assert "has_opinion" in text
    assert "请把护照号码改成 E123" in text
    got = parse_watch_json('{"hasOpinion":true,"opinions":[{"id":2,"confidence":"high"}],"pairReady":true,"reason":"改护照"}')
    assert got["pairReady"] is True


def test_pending_origin():
    rows = _public_pending([{
        "caseId": "abc",
        "sessionId": "g1",
        "sessionName": "冶金园群",
        "filename": "42616申报书.xlsm",
        "reason": "待人审",
        "at": "2026/09/23 09:00:00",
        "case": {
            "app": {
                "sender": "唐文秀",
                "time": "2026-09-22 17:10",
                "copies": [
                    {"source_id": "pc1", "source_label": "前台电脑", "message_id": 9},
                    {"source_id": "pc1", "source_label": "前台电脑", "message_id": 9},
                ],
            }
        },
    }])
    assert rows[0]["sender"] == "唐文秀"
    assert rows[0]["messageTime"] == "2026-09-22 17:10"
    assert rows[0]["sources"] == ["前台电脑"]
    assert rows[0]["sessionName"] == "冶金园群"


def test_uploaded_book_stays_out_of_review_until_new_material():
    case = {
        "id": "c1",
        "app": {"filename": "42616申报书.xlsm", "time": "2026-09-22 17:10"},
        "opinions": [{"kind": "file", "filename": "意见.docx", "time": "2026-09-22 17:20"}],
    }
    state = {"uploaded": []}
    assert _repeat_without_new(state, {**case, "existing": {"id": "t1"}}, "s1")
    assert _repeat_without_new(state, {
        **case,
        "similar": {"filename": "副本42616申报书.xlsm"},
    }, "s1")
    assert not _repeat_without_new(state, {
        "id": "c2",
        "app": {"filename": "另一份申报书.pdf", "time": "2026-09-23 09:00"},
        "similar": {"filename": "42616申报书.xlsm"},
        "opinions": [],
    }, "s1")
    _remember_uploaded(state, case, "s1")
    assert _repeat_without_new(state, case, "s1")
    newer = {
        "id": "c1",
        "app": {"filename": "42616申报书.xlsm", "time": "2026-09-22 17:10"},
        "opinions": case["opinions"] + [{
            "kind": "text",
            "text": "年薪改成150万",
            "time": "2026-09-23 10:00",
        }],
    }
    assert _has_new_material(state["uploaded"][0], newer)
    assert not _repeat_without_new(state, newer, "s1")
    revised = {
        "id": "c3",
        "app": {"filename": "42616申报书.xlsm", "time": "2026-09-23 11:00"},
        "opinions": case["opinions"],
    }
    assert not _repeat_without_new(state, revised, "s1")


def test_intent_bubble_is_read():
    got = parse_intent_json('{"read":true,"intent":"request_revision","need":"改年薪","action":"改正文"}')
    assert got["read"] is True
    assert got["readLabel"] == "已读"
    assert got["intentLabel"] == "要求修改申报书"
    assert got["need"] == "改年薪"
    bad = parse_intent_json('{"intent":"nope"}')
    assert bad["readLabel"] == "已读"
    assert bad["intentLabel"] == "其他"
    rows = parse_intent_list('[{"intent":"confirm","need":"","action":""},{"intent":"send_form","need":"申报书","action":"接收"}]', 2)
    assert rows[0]["intentLabel"] == "确认收到"
    assert rows[1]["intent"] == "send_form"
    short = parse_intent_list('[{"intent":"chat"}]', 2)
    assert len(short) == 2 and short[1]["error"]


def test_resolve_needs_key():
    # 有本地 config 时只要函数可调用；缺密钥会抛错，有密钥则返回 profile
    try:
        p = resolve_watch_llm()
        assert p.get("model")
        assert p.get("enableThinking") is False
        assert p.get("stream") is False
    except ValueError as e:
        assert "值班模型" in str(e)


def test_sync_mark_changes_only_when_chat_updates():
    base = [{
        "username": "S:1",
        "last_time": "2026-09-23 10:00:00",
        "msg_count": 3,
        "replicas": [{"source_id": "pc", "synced_at": "2026-09-23 10:00:00", "last_time": "2026-09-23 10:00:00", "msg_count": 3}],
    }]
    same = [{
        "username": "S:1",
        "msg_count": 3,
        "last_time": "2026-09-23 10:00:00",
        "replicas": [{"msg_count": 3, "source_id": "pc", "last_time": "2026-09-23 10:00:00", "synced_at": "2026-09-23 10:00:00"}],
    }]
    newer = [{
        "username": "S:1",
        "last_time": "2026-09-23 11:00:00",
        "msg_count": 4,
        "replicas": [{"source_id": "pc", "synced_at": "2026-09-23 11:00:00", "last_time": "2026-09-23 11:00:00", "msg_count": 4}],
    }]
    assert _sync_mark(base) == _sync_mark(same)
    assert _sync_mark(base) != _sync_mark(newer)
    assert _sync_mark([]) == ""


def test_reviewed_file_drops_out_of_scan_pending():
    live = {"c1"}
    sessions = {"s1"}
    mixed = {
        "sessionId": "s1",
        "pendingCount": 2,
        "pending": [{"caseId": "c1", "filename": "a.pdf"}, {"caseId": "c2", "filename": "b.pdf"}],
    }
    assert _open_pending_count(mixed, live, sessions) == 1
    cleared = {"sessionId": "s1", "pendingCount": 14}
    assert _open_pending_count(cleared, set(), set()) == 0
    assert _open_pending_count(cleared, {"other"}, {"s1"}) == 14


def test_intent_store_survives_reload():
    from app.wecom_intent_store import load_many, save_many, _path
    sid = "R:intent-store-test"
    key = sid + "|88|2026-09-23 16:00|张三"
    n = save_many(sid, [(key, {"intent": "confirm", "intentLabel": "确认收到", "need": "", "action": ""})])
    assert n == 1
    try:
        got = load_many(sid, [key, "missing"])
        assert got[key]["intentLabel"] == "确认收到"
        assert "missing" not in got
        again = load_many(sid, [key])
        assert again[key]["intent"] == "confirm"
    finally:
        path = _path(sid)
        if path and path.is_file():
            path.unlink()


if __name__ == "__main__":
    test_watch_block()
    test_parse_watch_json()
    test_strip_think()
    test_rule_skips_chatter()
    test_judgment_prompt_keeps_chinese()
    test_pending_origin()
    test_uploaded_book_stays_out_of_review_until_new_material()
    test_intent_bubble_is_read()
    test_intent_store_survives_reload()
    test_resolve_needs_key()
    test_sync_mark_changes_only_when_chat_updates()
    test_reviewed_file_drops_out_of_scan_pending()
    print("ok")
