# -*- coding: utf-8 -*-
"""会话列表不要把企业微信内部号当成展示名。"""
from __future__ import annotations
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.wecom_client import merge_groups
from app.wecom_local import (
    better_session_name,
    doc_title_from_text,
    fill_session_names,
    infer_session_display_name,
    looks_like_session_id,
)


def test_doc_title_from_system_text():
    assert doc_title_from_text("你创建了表格") == "智能表格"
    assert doc_title_from_text("你创建了智能文档") == "智能文档"
    assert doc_title_from_text("你编辑了表格") == "智能表格"
    assert doc_title_from_text("你通过行政人事杨志琴分享的链接加入了此表格") == "行政人事杨志琴的表格"
    assert doc_title_from_text("你通过申报四组组长-小袁（小袁13075708553）分享的链接加入了此表格") == "申报四组组长-小袁的表格"
    assert "已导入此文档" in "summary\":\"已导入此文档\""
    assert doc_title_from_text('user":{"name":"汪伦"},"type":15,"summary":"已导入此文档"') == "智能文档"


def test_infer_sheet_session():
    import app.wecom_local as L

    L._NAME_CACHE.clear()
    L._SELF_CACHE.clear()
    old = _patch_local(
        L,
        _msg_path=lambda source_id, session_id: object(),
        _read_tail=lambda path, need: (
            [{"sender": "汪伦", "text": "你通过尚明耀分享的链接加入了此表格"}],
            False,
        ),
        list_sessions=lambda *a, **k: [],
    )
    try:
        name = infer_session_display_name(
            "M:sheet",
            [{"source_id": "pc-wang"}],
            [{"id": "pc-wang", "operator_name": "汪伦"}],
        )
        assert name == "尚明耀的表格"
    finally:
        _restore_local(L, old)


def test_looks_like_session_id():
    assert looks_like_session_id("M:13102692256222396")
    assert looks_like_session_id("R:123")
    assert looks_like_session_id("S:abc")
    assert looks_like_session_id("13102692256222396")
    assert looks_like_session_id("")
    assert not looks_like_session_id("汪伦")
    assert not looks_like_session_id("3月6日奶茶统计")
    assert not looks_like_session_id("林志浩")


def test_better_prefers_human_over_id():
    assert better_session_name("M:13102692256222396", "奶茶统计") == "奶茶统计"
    assert better_session_name("奶茶统计", "M:13102692256222396") == "奶茶统计"
    assert better_session_name("", "M:1") == ""
    assert better_session_name("短名", "更长一点的备注") == "更长一点的备注"


def test_merge_groups_keeps_human_name():
    pairs = [
        (
            {
                "username": "M:13102692256222396",
                "display_name": "M:13102692256222396",
                "msg_count": 1,
                "last_time": "2026-01-30 09:14",
                "session_type": 1,
            },
            {"id": "pc-wang", "operator_name": "汪伦"},
        ),
        (
            {
                "username": "M:13102692256222396",
                "display_name": "奶茶统计",
                "msg_count": 3,
                "last_time": "2026-01-30 10:00",
                "session_type": 1,
            },
            {"id": "pc-lin", "operator_name": "林志浩"},
        ),
    ]
    items = merge_groups(pairs)
    assert len(items) == 1
    assert items[0]["display_name"] == "奶茶统计"
    assert items[0]["username"] == "M:13102692256222396"


def test_merge_groups_blank_when_only_ids():
    pairs = [
        (
            {
                "username": "M:9",
                "display_name": "M:9",
                "msg_count": 1,
                "session_type": 1,
            },
            {"id": "pc1"},
        )
    ]
    items = merge_groups(pairs)
    assert items[0]["display_name"] == ""


def _patch_local(L, **kwargs):
    old = {k: getattr(L, k) for k in kwargs}
    for k, v in kwargs.items():
        setattr(L, k, v)
    return old


def _restore_local(L, old):
    for k, v in old.items():
        setattr(L, k, v)


def test_infer_skips_operator_and_ids():
    import app.wecom_local as L

    L._NAME_CACHE.clear()
    L._SELF_CACHE.clear()

    def fake_tail(path, need):
        return (
            [
                {"sender": "汪伦", "text": '{"name":"汪伦"}'},
                {"sender": "申报一组汪伦", "text": '{"name":"申报一组汪伦"}'},
                {"sender": "M:1310", "text": "hi"},
                {"sender": "张三", "text": "你好"},
            ],
            False,
        )

    old = _patch_local(
        L,
        _msg_path=lambda source_id, session_id: object(),
        _read_tail=fake_tail,
        list_sessions=lambda *a, **k: [],
    )
    try:
        name = infer_session_display_name(
            "M:13102692256222396",
            [{"source_id": "pc-wang"}],
            [{"id": "pc-wang", "operator_name": "汪伦", "username": "申报一组汪伦"}],
        )
        assert name == "张三"
    finally:
        _restore_local(L, old)


def test_infer_self_only_stays_blank():
    import app.wecom_local as L

    L._NAME_CACHE.clear()
    L._SELF_CACHE.clear()
    old = _patch_local(
        L,
        _msg_path=lambda source_id, session_id: object(),
        _read_tail=lambda path, need: (
            [{"sender": "申报一组唐文秀", "text": '{"name":"申报一组唐文秀"}'}],
            False,
        ),
        list_sessions=lambda *a, **k: [],
    )
    try:
        name = infer_session_display_name(
            "M:99",
            [{"source_id": "pc-tang"}],
            [{"id": "pc-tang", "operator_name": "唐文秀"}],
        )
        assert name == ""
    finally:
        _restore_local(L, old)


def test_frequent_sender_treated_as_self():
    import app.wecom_local as L

    L._NAME_CACHE.clear()
    L._SELF_CACHE.clear()
    n = {"i": 0}

    def fake_tail(path, need):
        n["i"] += 1
        if n["i"] <= 20:
            return (
                [
                    {"sender": "申报一组组长-小唐18198156910", "text": "x"},
                    {"sender": "联系人%s" % n["i"], "text": "hi"},
                ],
                False,
            )
        return (
            [
                {"sender": "申报一组组长-小唐18198156910", "text": "x"},
                {"sender": "张三", "text": "你好"},
            ],
            False,
        )

    old = _patch_local(
        L,
        _msg_path=lambda source_id, session_id: object(),
        _read_tail=fake_tail,
        list_sessions=lambda *a, **k: [{"username": "M:%s" % i} for i in range(20)],
    )
    try:
        name = infer_session_display_name(
            "M:target",
            [{"source_id": "pc-liping"}],
            [{"id": "pc-liping", "operator_name": "唐利萍"}],
        )
        assert name == "张三"
    finally:
        _restore_local(L, old)


def test_fill_skips_groups():
    import app.wecom_local as L

    called = []
    old = L.infer_session_display_name
    L.infer_session_display_name = lambda *a, **k: called.append(a) or "X"
    old_src = L.list_sources
    L.list_sources = lambda: []
    try:
        rows = [
            {"username": "R:group", "display_name": "R:group"},
            {"username": "M:1", "display_name": "M:1", "replicas": []},
        ]
        fill_session_names(rows)
        assert rows[0]["display_name"] == "R:group"
        assert rows[1]["display_name"] == "X"
        assert len(called) == 1
    finally:
        L.infer_session_display_name = old
        L.list_sources = old_src


if __name__ == "__main__":
    test_doc_title_from_system_text()
    test_looks_like_session_id()
    test_better_prefers_human_over_id()
    test_merge_groups_keeps_human_name()
    test_merge_groups_blank_when_only_ids()
    test_infer_skips_operator_and_ids()
    test_infer_self_only_stays_blank()
    test_frequent_sender_treated_as_self()
    test_infer_sheet_session()
    test_fill_skips_groups()
    print("ok")
