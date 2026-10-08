# -*- coding: utf-8 -*-
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import parse_due_times, _due_broadcast_block, _merge_alert
from app.due_broadcast import add_workdays, build_message, parse_dt


def test_parse_times():
    assert parse_due_times("9:30,18:00") == ["09:30", "18:00"]
    assert parse_due_times(["07:05", "07:05", "25:00"]) == ["07:05"]
    assert parse_due_times("") == []
    print("times ok")


def test_workdays():
    fri = datetime(2026, 10, 9, 10, 0, 0)  # Friday
    assert add_workdays(fri, 1).date() == datetime(2026, 10, 12).date()
    assert add_workdays(fri, 0) == fri
    print("workdays ok")


def test_parse_dt():
    assert parse_dt("2026/10/08 13:32:00").year == 2026
    assert parse_dt("2026-10-08").month == 10
    print("parse_dt ok")


def test_config_block_defaults():
    got = _due_broadcast_block({})
    assert got["enabled"] is False
    assert got["times"] == ["09:30"]
    assert got["slaWorkdays"] == 1
    assert got["skipWeekend"] is True
    assert got["recentDays"] == 14
    raw = {}
    _merge_alert(raw, {"dueBroadcast": {"enabled": True, "times": "09:30,18:00", "recentDays": 7}})
    db = raw["alert"]["dueBroadcast"]
    assert db["enabled"] is True
    assert db["times"] == ["09:30", "18:00"]
    assert db["recentDays"] == 7
    print("config ok")


def test_build_message_no_send():
    msg = build_message(test=True, c={
        "slaWorkdays": 1, "recentDays": 14, "maxLines": 5,
        "wecomOnly": False, "sendIfEmpty": False,
    })
    assert "交付播报" in msg["text"]
    assert "pending" in msg
    print("message ok")


if __name__ == "__main__":
    test_parse_times()
    test_workdays()
    test_parse_dt()
    test_config_block_defaults()
    test_build_message_no_send()
    print("ALL OK")
