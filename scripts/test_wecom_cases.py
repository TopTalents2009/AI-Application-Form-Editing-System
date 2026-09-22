# -*- coding: utf-8 -*-
"""企业微信拆解：聊天短名 vs 缓存原名，避免把意向协议当成申报书。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_cases import (
    annotate_existing,
    classify_file,
    cluster_messages,
    resolve_app_upload,
    strip_cache_prefix,
)
from app.wecom_locate import identity_of, _name_hit, _file_overlap


def test_strip_message_id_prefix():
    assert strip_cache_prefix("69230_人才引进意向协议书-杜垚.pdf", 69230) == "人才引进意向协议书-杜垚.pdf"
    assert strip_cache_prefix("16049申报书（修订）.docx", 69230) == "16049申报书（修订）.docx"
    assert strip_cache_prefix("17169_AI设备_申报书.docx", 17169) == "AI设备_申报书.docx"


def test_chat_short_name_is_app_until_cache_name_arrives():
    assert classify_file("杜垚.pdf") == "app"
    assert classify_file("张三.docx") == "app"
    assert classify_file("16049申报书（修订）.docx") == "app"


def test_intent_agreement_never_app():
    assert classify_file("人才引进意向协议书-杜垚.pdf") == "ignore"
    assert classify_file("69230_人才引进意向协议书-杜垚.pdf") == "ignore"
    assert classify_file("杜垚.pdf", "69230_人才引进意向协议书-杜垚.pdf", message_id=69230) == "ignore"
    assert classify_file("护照.pdf") == "ignore"
    assert classify_file("杜垚反馈意见.docx") == "opinion"
    assert classify_file("260325人才供应统计(1).xlsx") == "ignore"
    assert classify_file("25041_0313HW高层次人才引进名单.xlsx") == "ignore"


def test_upload_rejects_intent_cached_as_person_pdf():
    got = resolve_app_upload("杜垚.pdf", "69230_人才引进意向协议书-杜垚.pdf", 69230)
    assert got["ok"] is False, got
    assert "人才引进意向协议书-杜垚.pdf" in got["filename"]
    assert "不是申报书" in got["detail"]

    ok = resolve_app_upload("杜垚.pdf", "69230_杜垚.pdf", 69230)
    assert ok["ok"] is True, ok
    assert ok["filename"] == "杜垚.pdf"

    form = resolve_app_upload("杜垚.pdf", "49749_杜垚申报书.pdf", 123)
    assert form["ok"] is True, form
    assert "申报书" in form["filename"]


def test_cluster_person_pdf_warns_and_intent_name_ignored():
    msgs = [
        {
            "message_id": 69230,
            "attachment_name": "杜垚.pdf",
            "has_attachment": True,
            "time_text": "2026-05-18 14:00",
            "sender": "陈侃",
            "copies": [{"source_id": "local", "message_id": 69230}],
        },
        {
            "message_id": 69231,
            "attachment_name": "人才引进意向协议书-杜垚.pdf",
            "has_attachment": True,
            "time_text": "2026-05-18 14:01",
            "sender": "陈侃",
            "copies": [{"source_id": "local", "message_id": 69231}],
        },
    ]
    cases = cluster_messages(msgs, session_id="R:1", session_name="49749 杜垚")
    assert len(cases) == 1, [c["app"]["filename"] for c in cases]
    assert cases[0]["app"]["filename"] == "杜垚.pdf"
    blob = " ".join(cases[0].get("warnings") or [])
    assert "缓存原名复核" in blob


def test_existing_task_stays_ready_for_reupload():
    class R:
        def list_meta(self):
            return [{"id": "t1", "status": "failed", "wecom": {"caseId": "abc123"}}]

    cases = [{
        "id": "abc123",
        "ready": True,
        "app": {"filename": "张三申报书.docx", "cached": True},
        "warnings": [],
    }]
    annotate_existing(cases, R())
    assert cases[0]["existing"]["id"] == "t1"
    assert cases[0]["ready"] is True
    assert any("重新上传" in w for w in cases[0]["warnings"])


def test_company_docx_without_shenbaoshu_word_is_app():
    assert classify_file("2ZG-55016-扬州栩脉智慧科技有限公司.docx") == "app"
    assert classify_file("31009-QM-江门市安诺特炊具制造有限公司.wps") == "app"
    assert classify_file("仪征枣林湾修改意见总结.wps") == "opinion"
    assert classify_file("雨花对接-修改意见.docx") == "opinion"


def test_cue_pairs_company_app_with_wps_opinion():
    msgs = [
        {"message_id": 1, "text": "新申报书", "time_text": "2026-09-22 15:24", "sender": "吴贤腾"},
        {
            "message_id": 2,
            "attachment_name": "2ZG-55016-扬州栩脉智慧科技有限公司.docx",
            "has_attachment": True,
            "time_text": "2026-09-22 15:24",
            "sender": "吴贤腾",
            "copies": [{"source_id": "pc", "message_id": 2}],
        },
        {"message_id": 3, "text": "修改意见", "time_text": "2026-09-22 15:24", "sender": "吴贤腾"},
        {
            "message_id": 4,
            "text": "仪征枣林湾修改意见总结.wps\ndoc",
            "time_text": "2026-09-22 15:24",
            "sender": "吴贤腾",
            "copies": [{"source_id": "pc", "message_id": 4}],
        },
    ]
    cases = cluster_messages(msgs, session_id="S:1", session_name="申报一组唐文秀")
    assert len(cases) == 1, [c["app"]["filename"] for c in cases]
    assert cases[0]["app"]["filename"].endswith("有限公司.docx")
    names = [o.get("filename") for o in cases[0]["opinions"]]
    assert any(str(n).endswith(".wps") for n in names), names


def test_exported_form_pdf_and_not_stamp():
    assert classify_file("宁波+宁波一彬电子科技股份有限公司+杜垚.pdf") == "app"
    assert classify_file("宁波市慈溪市-杜垚-青年人才.pdf") == "app"
    assert classify_file("单位签章.pdf") == "ignore"
    assert classify_file("关于申报人工作年限不足的破格说明 杜垚.pdf") == "ignore"


def test_two_char_name_locate():
    t = {"app": {"name": "69230_人才引进意向协议书-杜垚.pdf", "no": "69230"}}
    ident = identity_of(t)
    assert ident["personName"] == "杜垚", ident
    ok, val = _name_hit(["杜垚"], ident["personName"], ident["appName"])
    assert ok, (ok, val, ident)
    score, hit = _file_overlap("杜垚.pdf", ident["appName"])
    assert score > 0 and "杜垚" in hit, (score, hit)


if __name__ == "__main__":
    test_strip_message_id_prefix()
    test_chat_short_name_is_app_until_cache_name_arrives()
    test_intent_agreement_never_app()
    test_upload_rejects_intent_cached_as_person_pdf()
    test_cluster_person_pdf_warns_and_intent_name_ignored()
    test_existing_task_stays_ready_for_reupload()
    test_company_docx_without_shenbaoshu_word_is_app()
    test_cue_pairs_company_app_with_wps_opinion()
    test_exported_form_pdf_and_not_stamp()
    test_two_char_name_locate()
    print("ALL OK")
