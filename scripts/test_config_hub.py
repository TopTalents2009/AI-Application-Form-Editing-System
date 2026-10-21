# -*- coding: utf-8 -*-
"""后台模型配置：全部可配项都能读出并单独保存。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config


HUB_KEYS = (
    "grok", "gemini", "doubao", "pool", "papers", "ps", "projectProof",
    "wecomChat", "alert", "mysql", "portal", "scanPdf", "clientInbox", "classifyModel",
)


def test_editor_exposes_all_hub_fields():
    ed = (config.editor_config().get("edit") or {})
    for key in HUB_KEYS:
        assert key in ed, key
    wecom = ed["wecomChat"]
    assert "readMode" in wecom
    assert "dataRoot" in wecom
    assert isinstance(wecom.get("intent"), dict)
    assert isinstance(wecom.get("watch"), dict)
    mysql = ed["mysql"]
    assert "host" in mysql
    assert "hasPassword" in mysql
    assert "password" not in mysql
    scan = ed["scanPdf"]
    assert "converter" in scan
    print("editor hub fields ok")


def test_save_hub_roundtrip():
    src = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dest = tmp / "config.json"
        dest.write_text(json.dumps(src, ensure_ascii=False, indent=2), encoding="utf-8")
        default = tmp / "config.default.json"
        with patch.object(config, "CONFIG_PATH", dest), patch.object(config, "DEFAULT_CONFIG_PATH", default):
            got = config.save_config({
                "mysql": {"host": "10.0.0.8", "port": 3307, "database": "hub_test"},
                "portal": {"sdkUrl": "http://example.test/sdk.js", "portalId": "pid-1"},
                "clientInbox": {"path": "D:/inbox"},
                "scanPdf": {
                    "converter": "hybrid",
                    "hybrid": {"pageMinChars": 90, "criticalKeywords": ["护照", "性别"]},
                },
                "wecomChat": {
                    "readMode": "local",
                    "dataRoot": "D:/wecom-data",
                    "intent": {"model": "jev-hub", "timeoutSec": 45},
                },
            })
            ed = got.get("edit") or {}
            assert ed["mysql"]["host"] == "10.0.0.8"
            assert ed["mysql"]["port"] == 3307
            assert ed["mysql"]["database"] == "hub_test"
            assert ed["portal"]["portalId"] == "pid-1"
            assert ed["clientInbox"] == "D:/inbox"
            assert ed["scanPdf"]["pageMinChars"] == 90
            assert "护照" in (ed["scanPdf"].get("criticalKeywords") or [])
            assert ed["wecomChat"]["readMode"] == "local"
            assert ed["wecomChat"]["dataRoot"] == "D:/wecom-data"
            assert ed["wecomChat"]["intent"]["model"] == "jev-hub"
            raw_cfg = json.loads(dest.read_text(encoding="utf-8"))
            assert raw_cfg["mysql"]["host"] == "10.0.0.8"
            old_pw = (src.get("mysql") or {}).get("password")
            if old_pw:
                assert raw_cfg["mysql"].get("password") == old_pw
    print("save hub roundtrip ok")


def test_socks_all_proxy_to_http():
    old = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")}
    try:
        for k in old:
            os.environ.pop(k, None)
        os.environ["ALL_PROXY"] = "socks5://127.0.0.1:7897"
        assert config.normalize_httpx_proxy("socks5://127.0.0.1:7897") == "http://127.0.0.1:7897"
        assert config.httpx_proxy() == "http://127.0.0.1:7897"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1080"
        assert config.httpx_proxy() == "http://127.0.0.1:1080"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("socks all_proxy ok")


if __name__ == "__main__":
    test_editor_exposes_all_hub_fields()
    test_save_hub_roundtrip()
    test_socks_all_proxy_to_http()
    print("ok")
