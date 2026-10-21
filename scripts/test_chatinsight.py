# -*- coding: utf-8 -*-
"""ChatInsight 托管与 /parser-api 管理员反代。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app.routes import chatinsight as ci
from app import auth


def _patch_users():
    def fake(token: str):
        if token == "t-admin":
            return {"id": 1, "role": "admin", "username": "admin", "status": "active"}
        if token == "t-user":
            return {"id": 2, "role": "user", "username": "bob", "status": "active"}
        return None
    return patch.object(auth, "user_by_token", side_effect=fake)


def test_homeview_has_client_download():
    vue = (ROOT / "parser" / "web" / "src" / "views" / "HomeView.vue").read_text(encoding="utf-8")
    assert "下载 WeComSyncAgent.exe" in vue
    assert "agentDownloadHref" in vue
    built = ROOT / "static" / "chatinsight"
    blob = ""
    for p in built.rglob("*.js"):
        blob += p.read_text(encoding="utf-8", errors="ignore")
    assert "WeComSyncAgent.exe" in blob
    print("download button in console ok")


def test_bundled_parser_tree():
    from app.parser_proc import BUNDLED_PARSER, find_parser_root
    root = find_parser_root()
    assert root is not None
    assert root == BUNDLED_PARSER
    assert (root / "api" / "server.py").is_file()
    assert (root / "core-wecom" / "chat_platform.py").is_file()
    assert (root / "web" / "package.json").is_file()
    print("bundled parser ok")


def test_safe_rel_and_upstream():
    assert ci.safe_rel_path("../etc/passwd") is None
    assert ci.safe_rel_path("assets/app.js") == "assets/app.js"
    assert ci.safe_rel_path("") == ""
    assert ci.looks_like_asset("assets/foo.js")
    assert not ci.looks_like_asset("search")
    with patch("app.wecom_client._root", return_value="http://127.0.0.1:8767"):
        assert ci.parser_upstream_url("sources") == "http://127.0.0.1:8767/api/sources"
        assert ci.parser_upstream_url("agent-exe") == "http://127.0.0.1:8767/api/agent-exe"
        assert ci.parser_upstream_url("", "q=1") == "http://127.0.0.1:8767/api?q=1"
        try:
            ci.parser_upstream_url("../v1/secret")
            raise AssertionError("expected 400")
        except Exception as e:
            assert getattr(e, "status_code", None) == 400
    print("path helpers ok")


def test_unauth_and_non_admin():
    with _patch_users():
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/parser-api/platforms")
        assert r.status_code == 401, r.text
        r = c.get("/chatinsight/", follow_redirects=False)
        assert r.status_code == 303, r.status_code
        loc = r.headers.get("location") or ""
        assert "/login" in loc and "next=" in loc and "chatinsight" in loc, loc

        r = c.get("/parser-api/platforms", headers={"Authorization": "Bearer t-user"})
        assert r.status_code == 403, r.text
        r = c.get("/parser-api/agent-exe")
        assert r.status_code in (303, 401), r.status_code
        r = c.get("/chatinsight/", headers={"Authorization": "Bearer t-user"})
        assert r.status_code == 403, r.text
    print("auth gates ok")


def test_admin_spa_and_proxy_down():
    with _patch_users():
        c = TestClient(app, raise_server_exceptions=False)
        headers = {"Authorization": "Bearer t-admin"}
        r = c.get("/chatinsight/", headers=headers)
        assert r.status_code in (200, 503), r.status_code
        if r.status_code == 200:
            body = r.text or ""
            assert "chatinsight" in body.lower() or "<div id=" in body.lower() or "ChatInsight" in body
        r = c.get("/parser-api/__chatinsight_probe__", headers=headers)
        assert r.status_code in (404, 502, 200), r.status_code
        r = c.get("/parser-api/../v1/health", headers=headers)
        assert r.status_code in (400, 404), r.status_code
    print("admin spa/proxy ok", r.status_code)


if __name__ == "__main__":
    test_homeview_has_client_download()
    test_bundled_parser_tree()
    test_safe_rel_and_upstream()
    test_unauth_and_non_admin()
    test_admin_spa_and_proxy_down()
    print("ok")
