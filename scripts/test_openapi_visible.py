# -*- coding: utf-8 -*-
"""开放 API 创建的任务全体登录用户可见，来源显示 API 名称。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app import auth
from app import apikeys as K
from app import main as M


def test_openapi_label():
    assert K.is_openapi_task({"source": "openapi", "apiKeyId": 3, "apiKeyName": "8091"})
    assert K.openapi_label({"apiKeyName": "8091"}) == "8091"
    assert K.openapi_label({"apiActor": "API:人才库"}) == "人才库"
    assert K.openapi_label({"owner": "api:8091", "source": "openapi"}) == "8091"
    assert not K.is_openapi_task({"source": "wecom", "owner": "bob"})
    print("label ok")


def test_guard_and_list_visible_to_all_users():
    api_task = {
        "id": "t-api", "owner": "api:8091", "source": "openapi",
        "apiKeyId": 9, "apiKeyName": "8091", "app": {"name": "a.docx"},
    }
    own_task = {"id": "t-bob", "owner": "bob", "source": "", "app": {"name": "b.docx"}}
    other_task = {"id": "t-ann", "owner": "ann", "source": "", "app": {"name": "c.docx"}}

    def fake(token: str):
        if token == "t-user":
            return {"id": 2, "role": "user", "username": "bob", "status": "active"}
        if token == "t-admin":
            return {"id": 1, "role": "admin", "username": "admin", "status": "active"}
        return None

    bag = {x["id"]: x for x in (api_task, own_task, other_task)}
    with patch.object(auth, "user_by_token", side_effect=fake), patch.object(
        M.runner, "list_meta", return_value=[dict(api_task), dict(own_task), dict(other_task)]
    ), patch.object(M.runner, "get", side_effect=lambda tid: dict(bag[tid]) if tid in bag else None):
        c = TestClient(app, raise_server_exceptions=False)
        headers = {"Authorization": "Bearer t-user"}
        r = c.get("/api/tasks", headers=headers)
        assert r.status_code == 200, r.text
        ids = [x["id"] for x in r.json().get("tasks") or []]
        assert "t-api" in ids, ids
        assert "t-bob" in ids, ids
        assert "t-ann" not in ids, ids
        api_row = next(x for x in r.json()["tasks"] if x["id"] == "t-api")
        assert api_row.get("apiKeyName") == "8091" or api_row.get("source") == "openapi"
        r = c.get("/api/tasks/t-api", headers=headers)
        assert r.status_code == 200, r.text
        r = c.get("/api/tasks/t-ann", headers=headers)
        assert r.status_code == 404, r.text
    print("visible ok")


def test_ui_origin():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    admin = (ROOT / "static" / "admin-overview.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "apiKeyName" in js
    assert "openapi" in js
    assert "apiKeyName" in admin
    assert "app.js?v=py85" in index
    print("ui ok")


if __name__ == "__main__":
    test_openapi_label()
    test_guard_and_list_visible_to_all_users()
    test_ui_origin()
    print("ALL OK")
