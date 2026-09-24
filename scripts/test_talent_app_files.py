# -*- coding: utf-8 -*-
"""人才库字段 JSON：结构与 831 database.json 对齐，版本从 v1 递增。"""
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import init_db
from app.pool import build_database_export, build_talent_export
from app.talent_app_files import (
    version_label, next_version_n, save_from_task, list_by_attach, load_export, get_row,
)
from app import db


def test_version_label():
    assert version_label(1) == "v1"
    assert version_label(2) == "v2"
    assert version_label(3) == "v3"
    print("version label ok")


def test_build_database_export_wraps_talent():
    snap = {
        "talent": {
            "id": 12,
            "attach_id": "10001",
            "name": "张三",
            "mode": "QM",
            "payload": {
                "申报人基本信息": {
                    "有效证件姓名": "张三",
                    "回国前职务中文": "副总裁",
                    "引进企业基本情况": "企业简介",
                },
                "引进企业基本情况": "企业简介",
                "申报信息": {"申报类型": "青年人才"},
            },
            "last_application": {"申报信息": {"申报类型": "青年人才"}},
        },
        "enterprise": {"id": 9, "company_name": "示例公司"},
    }
    edits = [{"find": "青年人才", "replace": "创新人才"}]
    applied = [{"status": "hit"}]
    export = build_database_export(snap, edits, applied)
    assert "talent" in export
    talent = export["talent"]
    assert talent["id"] == 12
    assert talent["attach_id"] == "10001"
    assert talent["payload"]["申报信息"]["申报类型"] == "创新人才"
    assert talent["last_application"]["申报信息"]["申报类型"] == "青年人才"
    assert "回国前职务" not in talent["payload"]["申报人基本信息"]
    assert "引进企业基本情况" not in talent["payload"]
    assert talent["payload"]["申报人基本信息"]["引进企业基本情况"] == "企业简介"
    assert export["enterprise"]["company_name"] == "示例公司"
    record = build_talent_export(snap, edits, applied)
    assert record["payload"]["申报信息"]["申报类型"] == "创新人才"
    print("database export wrap ok")


def test_save_increments_version():
    init_db()
    aid = "__test_ver__"
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM talent_app_files WHERE attach_id=%s", (aid,))
    finally:
        conn.close()
    tmp = ROOT / "talent_outputs" / "_test_task"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    t = {
        "id": "test-task-v1",
        "dir": str(tmp),
        "app": {"name": "54332申报书.docx", "no": "54332", "attachId": aid, "personName": "TEST", "mode": "HJ"},
    }
    export1 = {
        "talent": {
            "id": 588,
            "attach_id": aid,
            "name": "TEST",
            "mode": "HJ",
            "payload": {"申报人基本信息": {"有效证件姓名": "TEST", "技术专长": "v1专长"}},
            "last_application": {"申报信息": {"申报类型": "火炬计划"}},
        }
    }
    row1 = save_from_task(t, export1)
    assert row1, row1
    assert row1["version"] == "v1", row1
    assert row1["attach_id"] == aid
    data1 = load_export(row1)
    assert data1["talent"]["payload"]["申报人基本信息"]["技术专长"] == "v1专长"
    assert data1["talent"]["last_application"]["申报信息"]["申报类型"] == "火炬计划"

    export2 = json.loads(json.dumps(export1, ensure_ascii=False))
    export2["talent"]["payload"]["申报人基本信息"]["技术专长"] = "v2专长"
    t["id"] = "test-task-v2"
    row2 = save_from_task(t, export2)
    assert row2["version"] == "v2", row2
    data2 = load_export(row2)
    assert data2["talent"]["payload"]["申报人基本信息"]["技术专长"] == "v2专长"
    assert next_version_n(aid) == 3

    items = list_by_attach(aid)
    assert [x["version"] for x in items] == ["v2", "v1"], items
    assert get_row(int(row1["id"]))["version"] == "v1"
    assert load_export(row1)["talent"]["payload"]["申报人基本信息"]["技术专长"] == "v1专长"

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM talent_app_files WHERE attach_id=%s", (aid,))
    finally:
        conn.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("save increment ok")


if __name__ == "__main__":
    test_version_label()
    test_build_database_export_wraps_talent()
    test_save_increments_version()
    print("ALL OK")
