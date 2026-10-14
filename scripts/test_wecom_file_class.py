# -*- coding: utf-8 -*-
"""文件分类：用前后聊天上下文纠偏，不调用模型。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_cases import cluster_messages
from app.wecom_file_class import (
    classify_name_with_context,
    gather_context,
    image_media_type,
    is_image_message,
    list_file_entries,
    make_cluster_kind_of,
)


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


def test_person_pdf_passport_from_prev_chat():
    msgs = [
        _m(1, text="这是护照扫描件，请归档"),
        _m(2, "张三.pdf"),
    ]
    items = list_file_entries(msgs)
    assert len(items) == 1
    assert items[0]["label"] == "护照", items[0]
    assert items[0]["source"] == "context"


def test_explicit_app_not_stolen_by_passport_chat():
    msgs = [
        _m(1, text="护照也发一下"),
        _m(2, "51104申报书.docx"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "申报书", items[0]


def test_same_sender_next_line_caption():
    msgs = [
        _m(1, "杜垚.pdf"),
        _m(2, text="这是护照扫描件"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "护照", items[0]


def test_meeting_minutes_not_opinion():
    msgs = [_m(1, "评审会议纪要.docx")]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "其他", items[0]


def test_opinion_from_following_chat():
    msgs = [
        _m(1, "辅导.docx"),
        _m(2, text="这是修改意见，请按意见改申报书"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "修改意见", items[0]


def test_project_plan_not_project_proof():
    msgs = [_m(1, "XX项目计划.xlsx")]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "其他", items[0]


def test_work_proof_from_filename():
    msgs = [_m(1, "海外工作证明.pdf")]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "工作证明", items[0]


def test_soft_chat_passport_on_person_pdf():
    msgs = [
        _m(1, text="把护照发来"),
        _m(2, "张三.pdf"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "护照", items[0]


def test_soft_education_before_scan():
    msgs = [
        _m(1, text="先发学历"),
        _m(2, "scan.jpg"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "学历证明", items[0]


def test_gather_includes_neighbors():
    msgs = [
        _m(1, text="先发学历"),
        _m(2, "scan.jpg"),
        _m(3, text="毕业证复印件"),
    ]
    ctx = gather_context(msgs, 1)
    blob = ctx["blob"]
    assert "学历" in blob
    assert "毕业证" in blob
    hit = classify_name_with_context("scan.jpg", ctx)
    assert hit["label"] == "学历证明", hit


def test_watch_kind_passport_not_app():
    msgs = [
        _m(1, text="这是护照扫描件，请归档"),
        _m(2, "张三.pdf"),
    ]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[1]) == "ignore"
    cases = cluster_messages(msgs, session_id="R:w", session_name="值班", kind_of=kind_of)
    assert cases == []


def test_watch_kind_chat_opinion():
    msgs = [
        _m(1, "辅导.docx"),
        _m(2, text="这是修改意见，请按意见改申报书"),
        _m(3, "51104申报书.docx"),
    ]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[0]) == "opinion"
    assert kind_of(msgs[2]) == "app"
    cases = cluster_messages(msgs, session_id="R:w", session_name="值班", kind_of=kind_of)
    assert len(cases) == 1
    ops = [o.get("filename") for o in (cases[0].get("opinions") or [])]
    assert "辅导.docx" in ops


def test_watch_kind_meeting_not_opinion():
    msgs = [_m(1, "评审会议纪要.docx")]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[0]) == "ignore"


def test_watch_kind_person_pdf_stays_app_without_chat():
    msgs = [_m(1, "张三.pdf")]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[0]) == "app"


def test_image_message_and_mime():
    assert is_image_message({"media_url": "https://wework.qpic.cn/x"})
    assert is_image_message({"msg_type": 4, "attachment_name": "图片"})
    assert is_image_message({"msg_type": 4, "has_attachment": True, "attachment_name": "图片"})
    assert is_image_message(filename="截图.png")
    assert not is_image_message(filename="51104申报书.docx")
    assert not is_image_message(filename="申报书.wps", media_url="https://wework.qpic.cn/cover")
    assert not is_image_message({"has_attachment": True, "media_url": "https://wework.qpic.cn/cover", "attachment_name": "材料.wps"})
    assert not is_image_message(filename="说明.pdf", media_url="https://wework.qpic.cn/cover")
    assert image_media_type("a.png") == "image/png"
    assert image_media_type("a.bin", content=b"\x89PNG\r\n\x1a\nxxxx") == "image/png"
    assert image_media_type("a.bin", content=b"%PDF-1.4") == ""


def test_list_file_entries_keeps_screenshot_preview():
    msgs = [
        _m(1, "截图.png"),
        {"message_id": 2, "text": "", "sender": "陈侃", "time_text": "2026-09-01 10:00:00", "media_url": "https://wework.qpic.cn/p"},
    ]
    items = list_file_entries(msgs)
    by_mid = {str(it["messageId"]): it for it in items}
    assert by_mid["1"]["isImage"] is True
    assert by_mid["2"]["isImage"] is True
    assert by_mid["2"]["mediaUrl"].startswith("https://wework.qpic.cn")


def test_later_app_filename_does_not_reclassify_passport():
    msgs = [
        _m(1, text="这是护照扫描件，请归档"),
        _m(2, "张三.pdf"),
        _m(3, "51104申报书.docx"),
        _m(4, "51104_修改意见.txt"),
    ]
    items = list_file_entries(msgs)
    by_name = {it["filename"]: it for it in items}
    assert by_name["张三.pdf"]["label"] == "护照", by_name["张三.pdf"]
    assert by_name["51104申报书.docx"]["label"] == "申报书"
    kind_of = make_cluster_kind_of(items)
    assert kind_of(msgs[1]) == "ignore"
    assert kind_of(msgs[2]) == "app"


if __name__ == "__main__":
    test_person_pdf_passport_from_prev_chat()
    test_explicit_app_not_stolen_by_passport_chat()
    test_same_sender_next_line_caption()
    test_meeting_minutes_not_opinion()
    test_opinion_from_following_chat()
    test_project_plan_not_project_proof()
    test_work_proof_from_filename()
    test_soft_chat_passport_on_person_pdf()
    test_soft_education_before_scan()
    test_gather_includes_neighbors()
    test_watch_kind_passport_not_app()
    test_watch_kind_chat_opinion()
    test_watch_kind_meeting_not_opinion()
    test_watch_kind_person_pdf_stays_app_without_chat()
    test_image_message_and_mime()
    test_list_file_entries_keeps_screenshot_preview()
    test_later_app_filename_does_not_reclassify_passport()
    print("ALL OK")
