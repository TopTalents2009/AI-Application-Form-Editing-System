# -*- coding: utf-8 -*-
"""AI 解读流水线：先扫全部文件、规则分类，再按时间绑定版本与意见。不调模型。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_ai_bind import (
    PROMPT_TIME_WINDOW,
    _bind_result_for_prompt,
    build_talent_versions,
    extract_catalog,
)
from app.wecom_ai_read import _slim_case, scan_files_then_pair
from app.wecom_ai_store import match_ai_read_file
from app.wecom_file_class import list_file_entries, make_cluster_kind_of


def _m(mid, name="", text="", sender="陈侃", time="2026-09-01 10:00:00"):
    row = {
        "message_id": mid,
        "text": text,
        "sender": sender,
        "time_text": time,
    }
    if name:
        row["attachment_name"] = name
        row["has_attachment"] = True
    return row


def test_scan_classifies_all_files_with_rules():
    msgs = [
        _m(1, text="这是护照扫描件，请归档", time="2026-09-01 09:59:00"),
        _m(2, "张三.pdf", time="2026-09-01 10:00:00"),
        _m(3, "51104申报书.docx", time="2026-09-01 11:00:00"),
        _m(4, "51104_修改意见.txt", time="2026-09-01 12:00:00"),
        _m(5, "海外工作证明.pdf", time="2026-09-01 13:00:00"),
    ]
    got = scan_files_then_pair(msgs, session_id="R:x", session_name="测试群")
    entries = got["entries"]
    by_name = {it["filename"]: it for it in entries}
    assert len(entries) == 4
    assert by_name["张三.pdf"]["label"] == "护照"
    assert by_name["51104申报书.docx"]["label"] == "申报书"
    assert by_name["51104_修改意见.txt"]["label"] == "修改意见"
    assert by_name["海外工作证明.pdf"]["label"] == "工作证明"
    assert got["fileTypes"]["fileCount"] == 4
    assert got["fileTypes"]["usedGemini"] is False
    assert got["scan"]["appFileCount"] == 1
    assert got["scan"]["opinionFileCount"] == 1
    cases = got["cases"]
    assert len(cases) == 1
    assert cases[0]["app"]["filename"] == "51104申报书.docx"
    ops = [o.get("filename") for o in (cases[0].get("opinions") or [])]
    assert "51104_修改意见.txt" in ops
    assert "张三.pdf" not in ops
    assert "海外工作证明.pdf" not in [c["app"]["filename"] for c in cases]


def test_catalog_and_time_bind_use_classified_types():
    msgs = [
        _m(1, text="这是护照扫描件", time="2026-09-01 09:59:00"),
        _m(2, "张三.pdf", time="2026-09-01 10:00:00"),
        _m(3, "51104申报书.docx", time="2026-09-01 11:00:00"),
        _m(4, "51104_修改意见.txt", time="2026-09-01 12:00:00"),
        _m(5, "51104申报书_v2.docx", time="2026-09-03 10:00:00"),
        _m(6, "51104_修改意见2.txt", time="2026-09-03 11:00:00"),
    ]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    cat = extract_catalog(msgs, session_id="R:x", session_name="测试群", kind_of=kind_of)
    app_names = [a["filename"] for a in cat["apps"]]
    op_names = [o["filename"] for o in cat["opinions"]]
    assert "张三.pdf" not in app_names
    assert "51104申报书.docx" in app_names
    assert "51104申报书_v2.docx" in app_names
    assert "51104_修改意见.txt" in op_names
    assert "51104_修改意见2.txt" in op_names
    talents = build_talent_versions(cat["apps"])
    bound = _bind_result_for_prompt(
        PROMPT_TIME_WINDOW, talents, cat["opinions"], window_hours=48,
    )
    vers = bound["talents"][0]["versions"]
    assert len(vers) == 2
    assert [x["filename"] for x in vers[0]["opinions"]] == ["51104_修改意见.txt"]
    assert [x["filename"] for x in vers[1]["opinions"]] == ["51104_修改意见2.txt"]


def test_ai_read_ignores_receipt_and_passport_chat():
    msgs = [
        _m(1, "51104申报书.docx", time="2026-09-01 11:00:00"),
        _m(2, text="请查收申报书", time="2026-09-01 11:01:00"),
        _m(3, text="这是护照扫描件，请归档", time="2026-09-01 11:02:00"),
        _m(4, text="请把英文名改成 SANIYA ARFIN", time="2026-09-01 11:03:00"),
        _m(5, "51104_修改意见.txt", time="2026-09-01 12:00:00"),
    ]
    got = scan_files_then_pair(msgs, session_id="R:x", session_name="测试群")
    texts = [o.get("text") or "" for c in got["cases"] for o in (c.get("opinions") or []) if o.get("kind") == "text"]
    blob = "\n".join(texts)
    assert "请把英文名改成" in blob
    assert "请查收申报书" not in blob
    assert "护照扫描件" not in blob
    cat = extract_catalog(msgs, session_id="R:x", kind_of=got["kind_of"])
    op_files = [o.get("filename") for o in cat["opinions"]]
    op_texts = [o.get("text") or "" for o in cat["opinions"]]
    assert "51104_修改意见.txt" in op_files
    assert any("英文名" in t for t in op_texts)
    assert not any("请查收申报书" in t for t in op_texts)


def test_slim_case_keeps_copies_for_upload():
    raw = {
        "id": "c1",
        "session_id": "R:x",
        "ready": True,
        "existing": {"id": "t1"},
        "app": {
            "filename": "51104申报书.docx",
            "time": "2026-09-01 11:00",
            "sender": "陈侃",
            "message_id": 3,
            "copies": [{"source_id": "pc1", "message_id": 3}],
        },
        "opinions": [{
            "kind": "file",
            "filename": "意见.docx",
            "message_id": 4,
            "copies": [{"source_id": "pc1", "message_id": 4}],
        }],
    }
    got = _slim_case(raw, "测试群")
    assert got["app"]["copies"][0]["message_id"] == 3
    assert got["opinions"][0]["copies"][0]["message_id"] == 4
    assert got["existing"]["id"] == "t1"


def test_match_ai_read_file():
    payload = {
        "fileTypes": {"items": [
            {"messageId": "11", "filename": "51104申报书.docx", "label": "申报书"},
            {"messageId": "22", "filename": "护照.pdf", "label": "护照", "copies": [{"message_id": 99}]},
        ]},
        "cases": [{"app": {"message_id": 11, "filename": "51104申报书.docx"}}],
    }
    assert match_ai_read_file(payload, message_id="11")["filename"] == "51104申报书.docx"
    assert match_ai_read_file(payload, message_id="99")["filename"] == "护照.pdf"
    assert match_ai_read_file(payload, filename="护照.pdf")["messageId"] == "22"
    assert match_ai_read_file(payload, message_id="404") is None
    nested = {"groups": [{"fileTypes": {"items": [{"messageId": "7", "filename": "意向协议.pdf"}]}}]}
    assert match_ai_read_file(nested, message_id="7")["filename"] == "意向协议.pdf"
    bind = {
        "bindPrompts": {
            "time_window": {
                "talents": [{
                    "versions": [{"messageId": "88", "filename": "51104申报书.docx"}]
                }]
            }
        }
    }
    assert match_ai_read_file(bind, message_id="88")["filename"] == "51104申报书.docx"
    assert match_ai_read_file(payload, filename="[文件] 护照.pdf")["messageId"] == "22"


if __name__ == "__main__":
    test_scan_classifies_all_files_with_rules()
    test_catalog_and_time_bind_use_classified_types()
    test_ai_read_ignores_receipt_and_passport_chat()
    test_slim_case_keeps_copies_for_upload()
    test_match_ai_read_file()
    print("ALL OK")
