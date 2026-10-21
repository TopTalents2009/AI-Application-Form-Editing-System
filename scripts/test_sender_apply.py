# -*- coding: utf-8 -*-
"""发送人字面量 null 清洗，以及同一任务禁止并发确认写入。"""
from __future__ import annotations
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.party import clean_sender, sender_of, scrub_sender
from app.wecom_client import _normalize_msg
from app.runner import ApplyBusy, TaskStore


def test_clean_sender():
    assert clean_sender("null") == ""
    assert clean_sender("NULL") == ""
    assert clean_sender("None") == ""
    assert clean_sender("undefined") == ""
    assert clean_sender("未知") == ""
    assert clean_sender("  ") == ""
    assert clean_sender("张三") == "张三"
    assert sender_of({"sender": "null"}, "未知") == "未知"
    assert sender_of({"sender": "null"}) == ""
    m = {"sender": "null"}
    scrub_sender(m)
    assert m["sender"] == ""
    print("clean_sender ok")


def test_normalize_msg_null_sender():
    out = _normalize_msg({"sender": "null", "text": "你好"})
    assert out["sender"] == ""
    out2 = _normalize_msg({"sender": "刘晶", "text": "你好"})
    assert out2["sender"] == "刘晶"
    out3 = _normalize_msg({"sender": "None"})
    assert out3["sender"] == ""
    print("normalize_msg ok")


def test_apply_lock():
    async def run():
        with tempfile.TemporaryDirectory() as td:
            store = TaskStore(root=td, concurrency=1)
            t = {"id": "t1", "status": "planned"}
            store.tasks["t1"] = t
            started = asyncio.Event()
            hold = asyncio.Event()
            calls = []

            async def fake_locked(task, edits, leftovers, actor=""):
                calls.append(actor)
                started.set()
                await hold.wait()
                task["status"] = "done"

            store._apply_confirmed_locked = fake_locked

            async def first():
                await store.apply_confirmed(t, [], [], actor="a")

            async def second():
                await started.wait()
                try:
                    await store.apply_confirmed(t, [], [], actor="b")
                    return "ok"
                except ApplyBusy as e:
                    return str(e)

            t1 = asyncio.create_task(first())
            t2 = asyncio.create_task(second())
            await started.wait()
            assert t["status"] == "planned"
            got = await t2
            assert "正在写入" in got
            assert t["status"] == "planned"
            hold.set()
            await t1
            assert t["status"] == "done"
            assert calls == ["a"]
            try:
                await store.apply_confirmed(t, [], [], actor="c")
                raise AssertionError("should busy")
            except ApplyBusy as e:
                assert "不能应用" in str(e)
            assert t["status"] == "done"
            assert not issubclass(ApplyBusy, ValueError)

    asyncio.run(run())
    print("apply lock ok")


if __name__ == "__main__":
    test_clean_sender()
    test_normalize_msg_null_sender()
    test_apply_lock()
    print("all ok")
