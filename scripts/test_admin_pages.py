# -*- coding: utf-8 -*-
"""管理控制台一菜单一页。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app.routes.admin import ADMIN_PAGES
from app import auth, alert


def _patch_users():
    def fake(token: str):
        if token == "t-admin":
            return {"id": 1, "role": "admin", "username": "admin", "status": "active"}
        return None
    return patch.object(auth, "user_by_token", side_effect=fake)


def _admin_bundle():
    static = ROOT / "static"
    html = (static / "admin.html").read_text(encoding="utf-8")
    extras = []
    for name in (
        "admin.css", "admin-ingest.js", "admin-overview.js", "admin-users.js",
        "admin-openapi.js", "admin-system.js", "admin-feedback.js", "admin-config.js",
    ):
        extras.append((static / name).read_text(encoding="utf-8"))
    return html, html + "".join(extras)


def test_pages_in_html_and_urls():
    html, bundle = _admin_bundle()
    assert "href=\"/admin/chatinsight\"" in html
    assert "data-sec=\"chatinsight\"" in html
    assert "href=\"/admin/ingest\"" in html
    assert "data-sec=\"ingest\"" in html
    assert "入库看板" in html
    assert 'id="apiKeyEditModal"' in html
    assert 'data-api-act="edit"' in bundle
    assert "task-name-link" in bundle
    assert 'href="/t/\' + encodeURIComponent(t.id)' in bundle
    assert "AI 值班扫描配置" in html
    ingest_at = html.index('id="sec-ingest"')
    wecom_at = html.index('id="sec-wecom"')
    watch_at = html.index('id="wecomWatchPanel"')
    talent_at = html.index('id="ingestTalentPanel"')
    wecom_end = html.find('</section>', wecom_at)
    ingest_end = html.find('</section>', ingest_at)
    trace_at = html.index('id="idTracePanel"')
    assert wecom_at < watch_at < trace_at < wecom_end
    assert ingest_at < talent_at < ingest_end
    assert "编号足迹" in html[wecom_at:wecom_end]
    assert 'id="idTraceQ"' in html[wecom_at:wecom_end]
    assert "igTalentUpload" in html[ingest_at:ingest_end]
    assert "igStApps" in html[ingest_at:ingest_end]
    assert "igStLocal" in html[ingest_at:ingest_end]
    assert 'id="igPager"' in html[ingest_at:ingest_end]
    assert "INGEST_PAGE_SIZE" in bundle
    assert "ingest-group" in bundle
    assert "ingest-app" in bundle
    assert "rememberIngestOpen" in bundle
    assert "restoreIngestOpen" in bundle
    assert "keepPage" in bundle
    assert "renderIngestPage" in bundle
    assert 'value="app_edit"' in html[ingest_at:ingest_end]
    assert 'value="local"' in html[ingest_at:ingest_end]
    assert "确认写入" in html[ingest_at:ingest_end]
    cfg_at = html.index('id="sec-config"')
    cfg_end = html.find('</section>', cfg_at)
    cfg = html[cfg_at:cfg_end]
    for cid in ("cfgJump", "cfgDefault", "poolCfgCard", "papersCfgCard", "psCfgCard",
                "projectProofCfgCard", "wecomCfgCard", "alertCfgCard", "scanPdfCfgCard",
                "portalCfgCard", "mysqlCfgCard", "inboxCfgCard"):
        assert cid in cfg, cid
    for fn in ("renderAlertCfgCard", "renderScanPdfCard", "renderPortalCard",
               "renderMysqlCard", "renderInboxCard", "collectAlertCfg", "collectScanPdf"):
        assert fn in bundle, fn
    assert "wecomIntent" in bundle
    assert "talentUploadEnabled" in bundle[bundle.index('id="sec-config"'):]
    assert 'id="igPreviewModal"' in html
    assert "data-ig-preview" in bundle
    assert "renderIngestOpinions" in bundle
    assert "igPreviewOpinions" in bundle
    assert "ingest-ops" in bundle
    assert "对应修改意见" in bundle
    assert "admin-ingest.js?v=py3" in html
    assert "admin.css?v=py3" in html
    assert "预览" in html
    assert "ingest-acts" in bundle
    assert "ig-preview-plain" in bundle
    assert "igPreviewAny" in bundle
    assert "ig-preview-raw" in bundle
    assert "igUnmaskObj" in bundle
    assert "库内脱敏" not in html
    assert 'class="mini primary"' in html
    assert "wwEnabled" in html[wecom_at:wecom_end]
    assert "wwTalentUpload" not in html[wecom_at:wecom_end]
    users_js = (ROOT / "static" / "admin-users.js").read_text(encoding="utf-8")
    openapi_js = (ROOT / "static" / "admin-openapi.js").read_text(encoding="utf-8")
    assert "global.adminMe" in users_js
    assert "window.adminMe" in html
    assert "var me = global.adminMe" in users_js
    assert "global.fillApiOwnerSelect" in users_js
    assert "global.fillApiOwnerSelect = fillApiOwnerSelect" in openapi_js
    assert "showAdminPage" in html
    assert html.count('class="sidenav"') == 1
    assert 'class="admin-body"' in html
    assert html.index('class="sidenav"') < html.index('class="content"')
    assert 'html,body{height:100%;overflow:hidden}' in bundle
    assert '.content{flex:1;min-width:0;min-height:0;overflow:auto' in bundle
    assert 'href="/public/admin.css' in html
    assert 'src="/public/admin-config.js' in html
    assert 'src="/public/admin-ingest.js' in html
    assert 'position:sticky;top:var(--top-h)' not in bundle
    assert '.steps .step{' in bundle
    assert 'white-space:normal' in bundle
    assert 'flex-direction:column' in bundle
    assert html.count('id="arBar"') == 1
    for cap in ("工作", "会话", "系统"):
        assert cap in html, cap
    for slug in ADMIN_PAGES:
        assert 'href="/admin/' + slug + '"' in html, slug
    assert 'href="/admin/files"' not in html
    assert alert._admin_url("/feedback").endswith("/admin/feedback")
    assert alert._admin_url("/wecom").endswith("/admin/wecom")
    print("html/urls ok", len(ADMIN_PAGES))


def test_admin_routes():
    with _patch_users():
        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/admin/overview")
        assert r.status_code == 200, r.status_code
        assert "控制台菜单" in (r.text or "")
        r = c.get("/admin/chatinsight")
        assert r.status_code == 200
        headers = {"Authorization": "Bearer t-admin"}
        r = c.get("/admin/no-such-page", headers=headers, follow_redirects=False)
        assert r.status_code in (302, 303, 307), r.status_code
        loc = r.headers.get("location") or ""
        assert "overview" in loc, loc
        r = c.get("/admin/wecom", headers=headers)
        assert r.status_code == 200
        r = c.get("/admin/openapi", headers=headers)
        assert r.status_code == 200
        assert "apiKeyEditModal" in (r.text or "")
        r = c.get("/admin/ingest", headers=headers)
        assert r.status_code == 200
        r = c.get("/api/admin/talent-ingest", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert "groups" in body
        assert "appCount" in body
        r = c.get("/api/admin/talent-ingest")
        assert r.status_code == 401
        r = c.get("/admin/files", headers=headers, follow_redirects=False)
        assert r.status_code in (302, 303, 307), r.status_code
        assert "wecom" in (r.headers.get("location") or "")
    print("admin routes ok")


if __name__ == "__main__":
    test_pages_in_html_and_urls()
    test_admin_routes()
    print("ok")
