# -*- coding: utf-8 -*-
"""PS 修图工单：任务清单预上传的纯函数测试。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ps_tickets import (
    allowed_filename,
    build_note,
    collect_download_refs,
    compose_note,
    normalize_base,
    public_ticket,
    slim_ps_note,
    ticket_web_url,
    PREFIX,
)


def test_normalize_base():
    assert normalize_base("http://192.168.2.50:18427") == "http://192.168.2.50:18427" + PREFIX
    assert normalize_base("http://192.168.2.50:18427/api/external/v1") == "http://192.168.2.50:18427" + PREFIX
    assert normalize_base("http://192.168.2.50:18427/api/external") == "http://192.168.2.50:18427" + PREFIX
    assert normalize_base("") == ""


def test_allowed_filename():
    assert allowed_filename("护照.pdf")
    assert allowed_filename("scan.JPG")
    assert allowed_filename("note.txt")
    assert allowed_filename("证明.docx")
    assert not allowed_filename("x.zip")
    assert not allowed_filename("x.exe")


def test_build_note():
    note = build_note(
        task_id="abc",
        app_name="54578申报书.docx",
        talent_id="54578",
        talent_name="张三",
        items=[{
            "title": "工作证明",
            "statusLabel": "本地附件已找到",
            "snippet": "请同步修改工作证明",
            "locations": [
                "发文时间改为2027年2月-3月",
                "工作结束时间须不早于2027年12月或改为至今",
            ],
            "downloads": [{"filename": "3.海外工作证明_扫描版.pdf"}],
            "action": "请按意见更正后另行上传",
        }],
    )
    assert note == "发文时间改为2027年2月-3月\n工作结束时间须不早于2027年12月或改为至今"
    assert "申报书" not in note
    assert "54578" not in note
    assert "需要修改的位置" not in note
    assert "3.海外工作证明_扫描版.pdf" not in note
    assert "请按意见更正" not in note
    assert "任务提交人" not in note
    packed = compose_note(note, "李四")
    assert packed.startswith("任务提交人：李四")
    assert "发文时间改为2027年2月-3月" in packed
    empty = build_note(items=[{
        "title": "护照",
        "action": "护照扫描件无法写入申报书正文。请按审核意见更新护照材料后，在申报系统或材料包中替换/补传。",
        "downloads": [{"filename": "护照.pdf"}],
    }])
    assert empty == ""
    verbose = (
        "申报书 54578 张三　54578申报书.docx\n"
        "需要修改的位置：\n"
        "1. 工作证明（3.海外工作证明_扫描版.pdf）\n"
        "   - 发文时间改为2027年2月-3月\n"
        "   - 请按意见更正后另行上传\n"
    )
    assert slim_ps_note(verbose) == "发文时间改为2027年2月-3月"


def test_collect_download_refs():
    items = [{
        "title": "护照",
        "downloads": [
            {"filename": "护照.pdf", "download": "/api/tasks/x/ext-files/a1b2c3", "source": "local", "localPath": "C:/a/护照.pdf"},
            {"filename": "护照.pdf", "download": "/api/tasks/x/ext-files/a1b2c3", "source": "local", "localPath": "C:/a/护照.pdf"},
        ],
    }]
    refs = collect_download_refs(items)
    assert len(refs) == 1
    assert refs[0]["fid"] == "a1b2c3"
    assert refs[0]["filename"] == "护照.pdf"


def test_public_ticket():
    row = public_ticket({
        "ok": True,
        "id": 12,
        "status": "pending",
        "talentId": "7622",
        "talentName": "张三",
        "uploaded": 1,
        "assets": [{"id": 3, "filename": "护照.jpg", "kind": "original"}],
        "skipped": [],
        "error": "",
        "at": "2026/10/15 13:00:00",
    })
    assert row["ok"] is True
    assert row["id"] == 12
    assert row["statusLabel"] == "待处理"
    assert row["assets"][0]["filename"] == "护照.jpg"
    assert public_ticket(None) == {}
    assert public_ticket("x") == {}


def test_ticket_web_url_empty_without_id():
    assert ticket_web_url("") == ""
    assert ticket_web_url(None) == ""


def test_static_ui():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    admin = (ROOT / "static" / "admin.html").read_text(encoding="utf-8")
    admin_js = (ROOT / "static" / "admin-config.js").read_text(encoding="utf-8")
    assert "app.js?v=py83" in index
    assert "function slimPsNote" in js
    assert 'id="psUploadBtn">上传' in js
    assert "style.css?v=py130" in index
    assert "PS 修图工单" in js
    assert "function psTicketHtml" in js
    assert "function openPsUploadModal" in js
    assert "psUploadMask" in index
    assert "任务提交人姓名" in index
    assert "/api/tasks/' + ctx.t.id + '/ps-upload" in js or "ps-upload" in js
    assert "plan.psTicket" in js
    assert ".ps-box" in css
    assert 'id="psCfgCard"' in admin
    assert "renderPsCard" in admin_js
    assert "/api/ps/health" in admin_js


if __name__ == "__main__":
    test_normalize_base()
    test_allowed_filename()
    test_build_note()
    test_collect_download_refs()
    test_public_ticket()
    test_ticket_web_url_empty_without_id()
    test_static_ui()
    print("ok")
