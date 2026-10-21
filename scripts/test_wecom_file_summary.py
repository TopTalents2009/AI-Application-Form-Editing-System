# -*- coding: utf-8 -*-
"""会话汇总：鉴权、图片代理域名白名单、本机预览路径穿越。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app import auth
from app.wecom_client import media_url_allowed
from app.routes.wecom_board import _file_upload_public


def _patch_users():
    def fake(token: str):
        if token == "t-admin":
            return {"id": 1, "role": "admin", "username": "admin", "status": "active"}
        if token == "t-user":
            return {"id": 2, "role": "user", "username": "bob", "status": "active"}
        return None
    return patch.object(auth, "user_by_token", side_effect=fake)


def test_media_url_allowlist():
    assert media_url_allowed("https://wework.qpic.cn/orig/abc")
    assert media_url_allowed("https://p.qpic.cn/x")
    assert media_url_allowed("http://pic.weixin.qq.com/x")
    assert not media_url_allowed("http://127.0.0.1:8767/secret")
    assert not media_url_allowed("http://localhost/x")
    assert not media_url_allowed("https://evil.example/qpic.cn")
    assert not media_url_allowed("https://wework.qpic.cn.evil.com/x")
    assert not media_url_allowed("https://user:pass@wework.qpic.cn/x")
    assert not media_url_allowed("file:///C:/Windows/win.ini")
    assert not media_url_allowed("")
    print("media allowlist ok")


def test_upload_public_hides_endpoint():
    info = _file_upload_public()
    assert "baseUrl" not in info
    assert "via" not in info
    assert "configured" in info
    assert "hasKey" in info
    print("upload public ok")


def test_auth_and_path_gates():
    with _patch_users():
        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/wecom/file-summary", json={"session_id": "R:x"})
        assert r.status_code == 401, r.status_code
        r = c.get("/api/wecom/local-image", params={"name": "企业微信截图_1.png"})
        assert r.status_code == 401, r.status_code
        r = c.get("/api/wecom/media", params={"url": "https://wework.qpic.cn/x"})
        assert r.status_code == 401, r.status_code

        headers = {"Authorization": "Bearer t-user"}
        r = c.post("/api/wecom/file-summary", json={}, headers=headers)
        assert r.status_code == 400, r.text

        r = c.get("/api/wecom/media", params={"url": "http://127.0.0.1:1/"}, headers=headers)
        assert r.status_code == 400, r.text

        r = c.get(
            "/api/wecom/local-image",
            params={"name": r"..\..\..\Windows\win.ini"},
            headers=headers,
        )
        assert r.status_code == 404, r.status_code

        r = c.get(
            "/api/wecom/local-image",
            params={"name": "企业微信截图_*.png"},
            headers=headers,
        )
        assert r.status_code == 404, r.status_code

        r = c.get(
            "/api/wecom/local-image",
            params={"name": "secret.txt", "source_id": ".."},
            headers=headers,
        )
        assert r.status_code == 404, r.status_code
    print("auth/path gates ok")


def _sse_events(text: str):
    out = []
    for part in (text or "").split("\n\n"):
        line = part.strip()
        if not line.startswith("data:"):
            continue
        out.append(json.loads(line[5:].strip()))
    return out


def test_progress_ui_wired():
    js = (ROOT / "static" / "wecom-board.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert "function wecomProgressHtml" in js
    assert "function wecomPostProgress" in js
    assert "body.stream = true" in js
    assert "wecom-board.js?v=py86" in index
    assert ".wecom-progress" in css
    print("progress ui wired")


def test_file_summary_and_ai_read_stream():
    async def fake_list(*_a, **_k):
        return {"items": [], "display_name": "测试会话", "total": 0}

    async def fake_peers(*_a, **_k):
        return []

    async def fake_read(**kw):
        progress = kw.get("progress")
        if progress:
            maybe = progress({"phase": "scan", "percent": 40, "label": "扫描中", "files": 3})
            if hasattr(maybe, "__await__"):
                await maybe
        return {"ok": True, "scope": "session", "cases": [], "summary": {"summary": "ok"}}

    with _patch_users():
        c = TestClient(app, raise_server_exceptions=False)
        headers = {"Authorization": "Bearer t-user"}
        with patch("app.routes.wecom_board.W.list_merged_messages", side_effect=fake_list), patch(
            "app.routes.wecom_board.W.session_peers", side_effect=fake_peers
        ), patch("app.wecom_file_class.session_data_stamp", return_value=""), patch(
            "app.wecom_file_class.load_summary_state", return_value=None
        ):
            r = c.post(
                "/api/wecom/file-summary",
                json={"session_id": "R:x", "stream": True},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert "text/event-stream" in (r.headers.get("content-type") or "")
            evs = _sse_events(r.text)
            assert evs, r.text[:400]
            assert any(e.get("phase") == "done" for e in evs), evs
            assert any((e.get("percent") or 0) > 0 and e.get("label") for e in evs)
            result = [e for e in evs if e.get("phase") == "done"][-1].get("result") or {}
            assert result.get("ok") is True

            r = c.post("/api/wecom/file-summary", json={"session_id": "R:x"}, headers=headers)
            assert r.status_code == 200, r.text
            assert r.json().get("ok") is True

        with patch("app.wecom_ai_read.ai_read_chat", side_effect=fake_read), patch(
            "app.wecom_ai_store.save_ai_read", return_value={"id": "r1", "at": "t"}
        ):
            r = c.post(
                "/api/wecom/ai-read",
                json={"session_id": "R:x", "stream": True, "bind": False},
                headers=headers,
            )
            assert r.status_code == 200, r.text
            assert "text/event-stream" in (r.headers.get("content-type") or "")
            evs = _sse_events(r.text)
            assert any(e.get("phase") == "scan" for e in evs), evs
            assert any(e.get("phase") == "done" for e in evs), evs
    print("sse progress ok")


if __name__ == "__main__":
    test_media_url_allowlist()
    test_upload_public_hides_endpoint()
    test_auth_and_path_gates()
    test_progress_ui_wired()
    test_file_summary_and_ai_read_stream()
    print("ALL OK")
