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
from app.wecom_ai_read import scan_files_then_pair
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


if __name__ == "__main__":
    test_scan_classifies_all_files_with_rules()
    test_catalog_and_time_bind_use_classified_types()
    print("ALL OK")
