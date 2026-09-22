# -*- coding: utf-8 -*-
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import _wecom_chat_block, resolve_watch_llm
from app.llm import _strip_think
from app.wecom_jev import judgment_prompt
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


def test_resolve_needs_key():
    # 有本地 config 时只要函数可调用；缺密钥会抛错，有密钥则返回 profile
    try:
        p = resolve_watch_llm()
        assert p.get("model")
        assert p.get("enableThinking") is False
        assert p.get("stream") is False
    except ValueError as e:
        assert "值班模型" in str(e)


if __name__ == "__main__":
    test_watch_block()
    test_parse_watch_json()
    test_strip_think()
    test_rule_skips_chatter()
    test_judgment_prompt_keeps_chinese()
    test_resolve_needs_key()
    print("ok")
