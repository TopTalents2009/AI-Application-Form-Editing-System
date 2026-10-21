# -*- coding: utf-8 -*-
"""人才库字段 JSON：结构与 8091/831 database.json 对齐，版本从 v1 递增。"""
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
    stamp_export, list_recent, as_ingest_item, build_plain_preview, unmask_export,
    is_masked_value, opinions_of_task, load_task_for_opinions, attach_task_opinions,
)
from app.wecom_talent_db import list_board
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
    assert talent["payload"]["last_application"]["申报信息"]["申报类型"] == "青年人才"
    assert "回国前职务" not in talent["payload"]["申报人基本信息"]
    assert "引进企业基本情况" not in talent["payload"]
    assert talent["payload"]["申报人基本信息"]["引进企业基本情况"] == "企业简介"
    assert export["enterprise"]["company_name"] == "示例公司"
    assert export.get("attach_id") == "10001"
    record = build_talent_export(snap, edits, applied)
    assert record["payload"]["申报信息"]["申报类型"] == "创新人才"
    print("database export wrap ok")


def test_last_application_inside_payload_not_rewritten():
    snap = {
        "talent": {
            "attach_id": "8091",
            "name": "示例",
            "mode": "HJ",
            "source_year": "26年",
            "payload": {
                "申报信息": {"申报类型": "青年人才"},
                "last_application": {"申报信息": {"申报类型": "青年人才"}},
            },
        }
    }
    edits = [{"find": "青年人才", "replace": "创新人才"}]
    applied = [{"status": "hit"}]
    export = build_database_export(snap, edits, applied)
    talent = export["talent"]
    assert talent["payload"]["申报信息"]["申报类型"] == "创新人才"
    assert talent["last_application"]["申报信息"]["申报类型"] == "青年人才"
    assert talent["payload"]["last_application"]["申报信息"]["申报类型"] == "青年人才"
    assert export["attach_id"] == "8091"
    print("last_application nested ok")


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
    assert data1["version"] == "v1", data1
    assert data1["version_n"] == 1
    assert data1["attach_id"] == aid
    assert data1["talent"]["payload"]["申报人基本信息"]["技术专长"] == "v1专长"
    assert data1["talent"]["last_application"]["申报信息"]["申报类型"] == "火炬计划"
    stamped = stamp_export(data1, version="v9", version_n=9, attach_id=aid)
    assert stamped["version"] == "v9"
    assert stamped["talent"]["attach_id"] == aid

    export2 = json.loads(json.dumps(export1, ensure_ascii=False))
    export2["talent"]["payload"]["申报人基本信息"]["技术专长"] = "v2专长"
    t["id"] = "test-task-v2"
    row2 = save_from_task(t, export2)
    assert row2["version"] == "v2", row2
    data2 = load_export(row2)
    assert data2["version"] == "v2", data2
    assert data2["version_n"] == 2
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


def test_ingest_board_lists_app_edit():
    init_db()
    aid = "__test_board__"
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM talent_app_files WHERE attach_id=%s", (aid,))
    finally:
        conn.close()
    t = {
        "id": "board-task-1",
        "dir": str(ROOT / "talent_outputs" / "_test_board"),
        "app": {"name": "8091申报书.docx", "attachId": aid, "personName": "看板测试", "mode": "HJ"},
    }
    row = save_from_task(t, {
        "talent": {
            "attach_id": aid, "name": "看板测试", "mode": "HJ",
            "payload": {"申报人基本信息": {"有效证件姓名": "看板测试"}},
        }
    })
    assert row and row["version"] == "v1"
    recent = list_recent(q=aid)
    assert recent and recent[0]["attach_id"] == aid
    item = as_ingest_item(recent[0])
    assert item["source"] == "app_edit"
    assert item["version"] == "v1"
    assert item["label"] == "申报书 v1"
    assert item["url"].endswith("/file")
    board = list_board(q=aid, source="app_edit", ok_only=True)
    assert board.get("appCount") >= 1
    groups = {str(g.get("attachId") or ""): g for g in (board.get("groups") or [])}
    assert aid in groups
    labels = [f.get("label") for f in groups[aid]["files"]]
    assert "申报书 v1" in labels
    hit = [f for f in groups[aid]["files"] if f.get("source") == "app_edit"][0]
    assert isinstance(hit.get("opinions"), list)
    assert isinstance(hit.get("clauses"), list)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM talent_app_files WHERE attach_id=%s", (aid,))
    finally:
        conn.close()
    print("ingest board app_edit ok")


def test_opinions_of_task_reads_txt_and_plan():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    txt = tmp / "work" / "txt"
    txt.mkdir(parents=True)
    (txt / "修改意见.txt").write_text("请改护照号为 E1234567。形式审核部分不填。", encoding="utf-8")
    plan = tmp / "work" / "tmp"
    plan.mkdir(parents=True)
    (plan / "plan.json").write_text(json.dumps({
        "edits": [
            {"clause": "要求改护照号", "opinion": "护照号改为 E1234567"},
            {"clause": "形式审核缺项", "opinionGemini": "形式审核部分不填"},
        ]
    }, ensure_ascii=False), encoding="utf-8")
    packed = opinions_of_task({
        "id": "x",
        "dir": str(tmp),
        "app": {"name": "51104申报书.docx"},
        "opinions": [{"name": "修改意见.docx"}],
    })
    assert packed["appName"] == "51104申报书.docx"
    assert packed["opinions"][0]["name"] == "修改意见.docx"
    assert "护照号" in packed["opinions"][0]["snippet"]
    assert [c["clause"] for c in packed["clauses"]] == ["要求改护照号", "形式审核缺项"]
    empty = opinions_of_task(None)
    assert empty["opinions"] == [] and empty["appName"] == ""
    shutil.rmtree(tmp, ignore_errors=True)
    print("opinions of task ok")


def test_attach_task_opinions_from_disk():
    from app.config import TASKS_DIR
    tid = "_test_ig_opinions"
    root = TASKS_DIR / tid
    shutil.rmtree(root, ignore_errors=True)
    txt = root / "work" / "txt"
    txt.mkdir(parents=True)
    (txt / "群聊修改意见.txt").write_text("核对薪资是否与申报书一致", encoding="utf-8")
    (root / "meta.json").write_text(json.dumps({
        "id": tid,
        "app": {"name": "40989申报书.docx"},
        "opinions": [{"name": "群聊修改意见.txt"}],
    }, ensure_ascii=False), encoding="utf-8")
    loaded = load_task_for_opinions(tid)
    assert loaded and loaded.get("app", {}).get("name") == "40989申报书.docx"
    item = attach_task_opinions({"taskId": tid, "source": "app_edit", "opinions": []})
    assert item["appName"] == "40989申报书.docx"
    assert item["opinionCount"] == 1
    assert "薪资" in item["opinions"][0]["snippet"]
    assert load_task_for_opinions("../etc") is None
    shutil.rmtree(root, ignore_errors=True)
    print("attach task opinions ok")


def test_plain_preview_shows_chapter_text():
    exp = {
        "talent": {
            "name": "张三",
            "profile_summary": "履历摘要明文",
            "payload": {
                "申报人基本信息": {
                    "有效证件姓名": "张三",
                    "出生日期": "***",
                    "申报人基本情况": "申报人博士毕业于某某大学，长文段落。",
                },
                "科研情况": "代表性论文与贡献长文",
                "last_application": {"申报人推荐理由": "不应重复"},
            },
            "last_application": {"申报人推荐理由": "推荐理由长文"},
        }
    }
    secs = build_plain_preview(exp)
    texts = "\n".join(str(s.get("text") or "") for s in secs)
    assert "申报人博士毕业" in texts
    assert "代表性论文" in texts
    assert "推荐理由长文" in texts
    assert "不应重复" not in texts
    assert "履历摘要明文" in texts
    assert any(s.get("masked") for s in secs if s.get("text") == "***")
    print("plain preview chapters ok")


def test_unmask_export_uses_sibling_and_matching_form():
    assert is_masked_value("T***@gmail.com")
    assert is_masked_value("*****5227")
    assert not is_masked_value("Tphayuh256@gmail.com")
    exp = {
        "talent": {
            "name": "张三",
            "email_masked": "z***@x.com",
            "payload": {
                "申报人基本信息": {
                    "有效证件姓名": "张三",
                    "出生日期": "***",
                    "电子邮件": "z***@x.com",
                    "电子邮箱": "zhangsan@x.com",
                    "手机号": "*****5227",
                    "证件号码": "N******3",
                }
            },
        }
    }
    got = unmask_export(exp, "")
    basic = got["talent"]["payload"]["申报人基本信息"]
    assert basic["电子邮件"] == "zhangsan@x.com"
    assert got["talent"]["email_masked"] == "zhangsan@x.com"
    assert basic["出生日期"] == "***"
    form = "有效证件姓名\n张三\n出生日期 1984-07-30\n手机号 13800135227\n证件号码 N1234563\n电子邮件 zhangsan@x.com\n"
    got2 = unmask_export(exp, form)
    basic2 = got2["talent"]["payload"]["申报人基本信息"]
    assert basic2["出生日期"] == "1984-07-30"
    assert basic2["手机号"] == "13800135227"
    assert basic2["证件号码"] == "N1234563"
    other = "有效证件姓名\nANGELOPOULOS IOANNIS\n出生日期 1986-02-13\n手机号 13900001111\n证件号码 BA2249629\n"
    got3 = unmask_export(exp, other)
    basic3 = got3["talent"]["payload"]["申报人基本信息"]
    assert basic3["出生日期"] == "***"
    assert basic3["手机号"] == "*****5227"
    print("unmask export ok")


if __name__ == "__main__":
    test_version_label()
    test_build_database_export_wraps_talent()
    test_last_application_inside_payload_not_rewritten()
    test_save_increments_version()
    test_ingest_board_lists_app_edit()
    test_opinions_of_task_reads_txt_and_plan()
    test_attach_task_opinions_from_disk()
    test_plain_preview_shows_chapter_text()
    test_unmask_export_uses_sibling_and_matching_form()
    print("ALL OK")
