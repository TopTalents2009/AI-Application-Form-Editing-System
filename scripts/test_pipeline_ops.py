# -*- coding: utf-8 -*-
"""管线落盘门槛与运维告警入口。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.form_reqs import hard_limit_notes
from app.parser_proc import _alert_parser
from app.runner import collect_apply_notes
from app.wecom_watch import _clear_watch_fault, _note_watch_fault
from app import wecom_watch as WW
from app import alert as AL


def test_partial_find_blocks():
    app = "姓名：林启明。工作单位：某某公司。"
    edits = [
        {"find": "姓名：林启明", "replace": "姓名：林启明", "section": "其他"},
        {"find": "这段正文里根本没有", "replace": "改掉", "section": "其他"},
    ]
    info, blocks = collect_apply_notes(edits, app)
    assert any("拦截写入" in x for x in blocks), blocks
    assert not any("拦截写入" in x for x in info)
    print("partial find blocks ok")


def test_all_hit_allows():
    app = "姓名：林启明。工作单位：某某公司。"
    edits = [
        {"find": "姓名：林启明", "replace": "姓名：林小明", "section": "其他"},
    ]
    info, blocks = collect_apply_notes(edits, app)
    assert not any("拦截写入" in x for x in blocks), blocks
    print("all hit allows ok")


def test_hard_limit_blocks():
    notes = [
        "第 1 条「工作设想」超过印刷上限 1500 字，已压缩（修改后 1800→1400 字）。",
        "第 2 条「工作设想」replace 仍 1600 字，超过上限 1500 字，须人工精简后再确认落盘。",
    ]
    hard = hard_limit_notes(notes, [], "")
    assert len(hard) == 1
    assert "须人工精简" in hard[0]
    print("hard limit blocks ok")


def test_ops_fault_helper():
    got = AL.ops_fault(kind="parser", title="解析服务未找到", lines=["测试不发真实 webhook"])
    assert isinstance(got, dict)
    assert got.get("skipped") in ("disabled", "no_webhook", "below_min_level", "deduped") or got.get("ok") in (True, False)
    print("ops_fault helper ok")


def test_parser_alert_skips_healthy():
    called = []
    real = AL.ops_fault
    AL.ops_fault = lambda **kw: called.append(kw) or {"ok": True}
    try:
        assert _alert_parser({"ok": True, "skipped": True, "reason": "already"})["skipped"] is True
        assert _alert_parser({"ok": True, "port": 8767})["ok"] is True
        assert not called
        out = _alert_parser({"ok": False, "reason": "not_found"})
        assert out["reason"] == "not_found"
        assert called and called[0]["kind"] == "parser"
        assert "未找到" in called[0]["title"]
    finally:
        AL.ops_fault = real
    print("parser alert skip/emit ok")


def test_watch_fault_streak():
    called = []
    real = AL.ops_fault
    AL.ops_fault = lambda **kw: called.append(kw) or {"ok": True}
    try:
        WW._fail_streak = 0
        _note_watch_fault("第一次")
        assert WW._fail_streak == 1
        assert not called
        _note_watch_fault("第二次")
        assert WW._fail_streak == 2
        assert called and called[0]["kind"] == "wecom_watch"
        _clear_watch_fault()
        assert WW._fail_streak == 0
    finally:
        AL.ops_fault = real
        WW._fail_streak = 0
    print("watch streak ok")


if __name__ == "__main__":
    test_partial_find_blocks()
    test_all_hit_allows()
    test_hard_limit_blocks()
    test_ops_fault_helper()
    test_parser_alert_skips_healthy()
    test_watch_fault_streak()
    print("ALL OK")
