# -*- coding: utf-8 -*-
"""按人才编号汇总各群痕迹、同一文件、交付记录和各版修改意见。"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app import auth
from app import wecom_id_trace as T


def _patch_users():
    def fake(token: str):
        if token == "t-user":
            return {"id": 2, "role": "user", "username": "bob", "status": "active"}
        return None
    return patch.object(auth, "user_by_token", side_effect=fake)


def test_normalize_attach_id():
    assert T.normalize_attach_id("42413") == "42413"
    assert T.normalize_attach_id("编号 42413 张三") == "42413"
    try:
        T.normalize_attach_id("")
        assert False
    except ValueError:
        pass
    try:
        T.normalize_attach_id("abc")
        assert False
    except ValueError:
        pass
    print("normalize ok")


def test_aid_match_and_same_file():
    assert T._aid_match("42413", "42413申报书.docx")
    assert not T._aid_match("42413", "42414申报书.docx")
    assert T._aid_match("42413", "", "请看 42413 的材料")
    files = T._merge_same_files([
        {"filename": "42413申报书.docx", "sessionId": "R:1", "sessionName": "A群", "source": "file_summary"},
        {"filename": "42413 申报书.docx", "sessionId": "R:2", "sessionName": "交付-张三", "isDelivery": True, "source": "chat"},
    ])
    assert len(files) == 1, files
    assert len(files[0]["copies"]) == 2
    sess = T._merge_sessions(files[0]["copies"], [])
    assert any(s.get("isDelivery") for s in sess)
    print("match/merge ok")


def test_extract_aids_and_person():
    assert T._extract_aids("42413申报书.docx") == ["42413"]
    assert "54578" in T._extract_aids("", "请看 54578 的材料")
    assert T._extract_aids("企业微信截图_17773671632174(1).png") == []
    assert T._person_from_filename("42413张三.docx") == "张三"
    assert T._keep_summary_file("42413", "修改意见.docx", "opinion", True)
    assert not T._keep_summary_file("42413", "修改意见.docx", "opinion", False)
    assert T._keep_summary_file("42413", "42413申报书.docx", "app", False)
    print("extract ok")


def test_bind_screenshot_opinion_onto_app():
    messages = [
        {
            "message_id": 1, "time_text": "2026-09-01 10:00", "sender": "A",
            "attachment_name": "42413申报书.docx", "has_attachment": True,
        },
        {
            "message_id": 2, "time_text": "2026-09-01 10:05", "sender": "B",
            "text": "这是修改意见，请按此意见改",
        },
        {
            "message_id": 3, "time_text": "2026-09-01 10:06", "sender": "B",
            "attachment_name": "企业微信截图_17773671632174(1).png", "has_attachment": True,
        },
    ]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "msgs.json"
        path.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
        with patch("app.wecom_file_class.load_type_cache", return_value={}):
            vers, files = T._bind_one_session("42413", "R:1", "交付群", path)
    assert vers, vers
    ops = vers[0].get("opinions") or []
    assert ops, vers
    assert any("企业微信截图" in str(o.get("filename") or "") for o in ops), ops
    assert any(f.get("kind") == "opinion" for f in files), files
    print("bind screenshot opinion ok")


def test_bind_company_intro_is_not_app():
    messages = [
        {
            "message_id": 1, "time_text": "2026-10-09 11:23", "sender": "A",
            "text": "42413 请看公司材料",
            "attachment_name": "20260303广东康德威电气股份有限公司简介.docx",
            "has_attachment": True,
        },
        {
            "message_id": 2, "time_text": "2026-10-09 11:24", "sender": "A",
            "attachment_name": "42413申报书.docx", "has_attachment": True,
        },
    ]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "msgs.json"
        path.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
        with patch("app.wecom_file_class.load_type_cache", return_value={}):
            vers, files = T._bind_one_session("42413", "R:1", "交付群", path)
    names = [str(v.get("filename") or "") for v in vers]
    assert any("申报书" in n for n in names), vers
    assert all("简介" not in n for n in names), names
    print("company intro not app ok")


def test_merge_fills_empty_opinions():
    ai = [{
        "sessionId": "R:1", "sessionName": "交付群", "filename": "42413申报书.docx",
        "chatVersion": "chat-1", "opinions": [],
    }]
    bound = [{
        "sessionId": "R:1", "sessionName": "交付群", "filename": "42413申报书.docx",
        "chatVersion": "chat-1",
        "opinions": [{"kind": "file", "filename": "修改意见.docx", "text": "改摘要"}],
    }]
    got = T._merge_chat_versions(ai, bound)
    assert got[0]["opinions"][0]["filename"] == "修改意见.docx"
    print("merge opinions ok")


def test_collect_binds_session_opinions():
    with patch.object(T, "_watch_list", return_value=["交付群"]), patch.object(
        T, "_watched_sessions", return_value=[{"sourceId": "p", "sessionId": "R:1", "sessionName": "交付群"}]
    ), patch.object(T, "_from_ai_reads", return_value=[{
        "sessionId": "R:1", "sessionName": "交付群", "filename": "42413申报书.docx",
        "chatVersion": "chat-1", "opinions": [], "time": "2026-09-02",
    }]), patch.object(T, "_from_summaries", return_value=[{
        "filename": "42413申报书.docx", "sessionId": "R:1", "sessionName": "交付群",
        "kind": "app", "time": "2026-09-02",
    }]), patch.object(T, "_from_synced", return_value=[]), patch.object(
        T, "_from_versions", return_value=[]
    ), patch.object(T, "_bind_hit_sessions", return_value=([{
            "sessionId": "R:1", "sessionName": "交付群", "filename": "42413申报书.docx",
            "chatVersion": "chat-1", "time": "2026-09-02",
            "opinions": [{"kind": "file", "filename": "修改意见.docx", "text": "改标题"}],
        }], [{
            "filename": "修改意见.docx", "kind": "opinion", "label": "修改意见",
            "sessionId": "R:1", "sessionName": "交付群", "source": "chat_bind",
        }])
    ):
        got = T.collect("42413", lite=True)
    assert got["chatVersions"][0]["opinions"]
    assert any(f.get("kind") == "opinion" for f in got["files"])
    print("collect bind ok")


def test_collect_merges_groups_delivery_opinions():
    with patch.object(T, "_watch_list", return_value=["交付-张三"]), patch.object(
        T, "_watched_sessions", return_value=[{"sourceId": "p", "sessionId": "R:1", "sessionName": "交付-张三"}]
    ), patch.object(T, "_from_ai_reads", return_value=[{
        "sessionId": "R:1", "sessionName": "交付-张三", "filename": "42413张三.docx",
        "chatVersion": "chat-1", "isDelivery": True, "time": "2026-09-02",
        "personName": "张三",
        "opinions": [{"kind": "text", "text": "改标题", "sender": "陈侃"}],
    }]), patch.object(T, "_from_summaries", return_value=[{
        "filename": "42413张三.docx", "sessionId": "R:1", "sessionName": "交付-张三",
        "isDelivery": True, "source": "file_summary", "kind": "app", "label": "申报书",
        "time": "2026-09-02", "personName": "张三",
    }]), patch.object(T, "_from_synced", return_value=[]), patch.object(
        T, "_bind_hit_sessions", return_value=([], [])
    ), patch.object(T, "_from_versions", return_value=[{
        "id": "f1", "version": "v1", "versionN": 1, "filename": "42413.docx",
        "personName": "张三", "taskId": "t1", "at": "2026-09-01",
        "opinions": [{"name": "意见.docx", "snippet": "改摘要"}], "clauses": [{"clause": "改标题"}],
    }]), patch.object(T, "_from_ingest") as ingest, patch.object(
        T, "_from_tasks"
    ) as tasks:
        got = T.collect("42413")
    ingest.assert_not_called()
    tasks.assert_not_called()
    assert got["ok"] is True
    assert got["attachId"] == "42413"
    assert got["personName"] == "张三"
    assert got["sessions"], got
    assert any(s.get("isDelivery") for s in got["sessions"])
    assert got["deliveries"]
    assert got["files"]
    assert got["files"][0]["copies"]
    assert got["versions"][0]["opinions"]
    assert got["tasks"] == []
    assert got["chatVersions"]
    print("collect ok")


def test_overview_lists_ids():
    with patch.object(T, "_watch_list", return_value=["国家级交付质检群"]), patch.object(
        T, "_watched_sessions", return_value=[{"sourceId": "p", "sessionId": "R:1", "sessionName": "国家级交付质检群"}]
    ), patch.object(T, "_from_summaries", return_value=[{
        "attachId": "54578", "filename": "a.docx", "sessionId": "R:1",
        "sessionName": "国家级交付质检群", "time": "2026-09-01",
        "kind": "app", "personName": "Kristen",
    }]), patch.object(T, "_from_synced", return_value=[]), patch.object(
        T, "_from_ai_reads", return_value=[{
            "attachId": "54578", "sessionId": "R:1", "sessionName": "国家级交付质检群",
            "filename": "a.docx", "opinions": [{"text": "改"}], "personName": "Kristen",
        }]
    ), patch("app.wecom_talent_db.list_board") as board:
        got = T.overview()
    board.assert_not_called()
    assert got["ok"] is True
    assert got["overview"] is True
    assert got["watchCount"] >= 1
    assert got["items"], got
    row = got["items"][0]
    assert row["attachId"] == "54578"
    assert row["personName"] == "Kristen"
    assert row["sessionCount"] >= 1
    assert row["deliveryCount"] >= 1
    assert row["versionCount"] >= 1
    assert row["fileCount"] >= 1
    print("overview ok")


def test_overview_empty_without_watch():
    with patch.object(T, "_watch_list", return_value=[]), patch.object(
        T, "_from_summaries"
    ) as summaries, patch("app.wecom_talent_db.list_board") as board:
        got = T.overview()
    summaries.assert_not_called()
    board.assert_not_called()
    assert got["items"] == []
    assert got["watchCount"] == 0
    assert "关注会话" in (got.get("note") or "")
    print("overview empty ok")


def test_collect_lite_skips_synced():
    with patch.object(T, "_watch_list", return_value=["交付"]), patch.object(
        T, "_watched_sessions", return_value=[{"sourceId": "p", "sessionId": "R:1", "sessionName": "交付"}]
    ), patch.object(T, "_from_ai_reads", return_value=[]), patch.object(
        T, "_from_summaries", return_value=[]
    ), patch.object(T, "_from_versions", return_value=[]), patch.object(
        T, "_bind_hit_sessions", return_value=([], [])
    ), patch.object(T, "_from_synced") as synced:
        got = T.collect("54578", lite=True)
    synced.assert_not_called()
    assert got["ok"] is True
    assert got["lite"] is True
    assert got["attachId"] == "54578"
    print("lite ok")


def test_gemini_classifies_opinion_files():
    strong = {
        "files": [{
            "filename": "54578修改意见.docx",
            "kind": "file",
            "copies": [{"sessionId": "R:1", "sessionName": "交付群", "time": "2026-09-01"}],
        }]
    }

    async def boom(*_a, **_k):
        raise AssertionError("strong opinion name should not call gemini")

    with patch("app.wecom_file_class._gemini_overlay", side_effect=boom), patch(
        "app.wecom_file_class.load_type_cache", return_value={}
    ), patch("app.wecom_file_class.save_type_cache"):
        got = asyncio.run(T.attach_gemini_kinds(strong))
    assert got["fileOpinions"], got
    assert "修改意见" in got["fileOpinions"][0]["filename"]

    other = {
        "files": [{
            "filename": "54578材料.docx",
            "kind": "file",
            "copies": [{"sessionId": "R:1", "sessionName": "交付群"}],
        }]
    }

    async def fake(entries):
        for it in entries:
            it["type"] = "opinion"
            it["label"] = "修改意见"
            it["source"] = "gemini"
            it["reason"] = "聊天说按此意见改"
        return entries, "已按聊天上下文复核 1 个文件类型"

    with patch("app.wecom_file_class._gemini_overlay", side_effect=fake), patch(
        "app.wecom_file_class.load_type_cache", return_value={}
    ), patch("app.wecom_file_class.save_type_cache"):
        got = asyncio.run(T.attach_gemini_kinds(other))
    assert got["fileOpinions"], got
    assert got["fileOpinions"][0]["classSource"] == "gemini"
    assert "复核" in (got.get("geminiNote") or "")
    print("gemini classify ok")


def test_route_and_ui():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    admin = (ROOT / "static" / "admin.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "wecom-board.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    home_board = index.index('class="wecom-board"')
    home_panel = index.index('id="idTracePanel"')
    home_end = index.index('id="agentReviewHome"')
    assert home_board < home_panel < home_end
    admin_board = admin.index('class="wecom-board"')
    admin_panel = admin.index('id="idTracePanel"')
    admin_wecom_end = admin.find("</section>", admin.index('id="sec-wecom"'))
    assert admin_board < admin_panel < admin_wecom_end
    assert "编号足迹" in index and "编号足迹" in admin
    assert "function loadIdTrace" in js
    assert "function loadIdTraceOverview" in js
    assert "function initIdTracePanel" in js
    assert "loadIdTraceOverview()" in js
    assert "ID_TRACE_PAGE_SIZE = 10" in js
    assert "id-trace-id" in js
    assert "idTraceEnsureDetail" in js
    assert "function idTraceByGroupHtml" in js
    assert "lite=1" in js
    assert "idTraceOverviewInfo" in js
    assert "关注会话中的编号" in js
    assert "/api/wecom/id-trace" in js
    assert "id-trace-list" in js
    assert "function idTraceTable" in js
    assert "正在查阅全部关注会话" in index and "正在查阅全部关注会话" in admin
    assert "从全部关注会话归纳编号" in index
    assert "每页 10 条" in index
    assert ".id-trace-wrap" in css
    assert ".id-trace-id" in css
    assert "wecom-board.js?v=py95" in index
    assert "wecom-board.js?v=py95" in admin
    assert "idTraceIsOpinion" in js
    assert "Gemini" in js
    assert "style.css?v=py135" in index
    with _patch_users():
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/wecom/id-trace", params={"q": "42413"})
        assert r.status_code == 401, r.status_code
        headers = {"Authorization": "Bearer t-user"}
        r = c.get("/api/wecom/id-trace", params={"q": "abc"}, headers=headers)
        assert r.status_code == 400, r.text
        with patch("app.wecom_id_trace.overview", return_value={"ok": True, "overview": True, "items": [{"attachId": "54578"}]}):
            r = c.get("/api/wecom/id-trace", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json().get("overview") is True
            assert r.json().get("items")[0]["attachId"] == "54578"
        with patch("app.wecom_id_trace.collect", return_value={"ok": True, "attachId": "42413", "sessions": [], "lite": True}) as col:
            r = c.get("/api/wecom/id-trace", params={"q": "42413", "lite": "1"}, headers=headers)
            assert r.status_code == 200, r.text
            assert r.json().get("attachId") == "42413"
            assert col.call_args.kwargs.get("lite") is True or col.call_args[1].get("lite") is True
    print("route/ui ok")


if __name__ == "__main__":
    test_normalize_attach_id()
    test_aid_match_and_same_file()
    test_extract_aids_and_person()
    test_bind_screenshot_opinion_onto_app()
    test_bind_company_intro_is_not_app()
    test_merge_fills_empty_opinions()
    test_collect_binds_session_opinions()
    test_collect_merges_groups_delivery_opinions()
    test_overview_lists_ids()
    test_overview_empty_without_watch()
    test_collect_lite_skips_synced()
    test_gemini_classifies_opinion_files()
    test_route_and_ui()
    print("ALL OK")
