# -*- coding: utf-8 -*-
"""意见点名护照/学历证明/工作经历证明等无法改正文的附件时，产出任务清单。"""
from __future__ import annotations
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.attachments import (
    LOCAL_ATTACH_ROOT,
    build_task_list,
    extract_needed_kinds,
    format_task_list_md,
    scan_local_attachments,
)
from app.report_docx import write_task_list_docx


def _ids(items):
    return [x.get("id") for x in items]


def test_modify_education_and_work_proof():
    items = build_task_list(["请修改学历证明，工作经历证明请重新上传"])
    assert "education" in _ids(items)
    assert "work_proof" in _ids(items)


def test_passport_scan_is_task():
    items = build_task_list(["护照扫描件不清晰，请更换"])
    assert "passport" in _ids(items)


def test_passport_number_is_not_attachment_task():
    kinds = extract_needed_kinds(["护照号码填错，请改为 E1234567"])
    assert "passport" not in [k["id"] for k in kinds]
    items = build_task_list(["护照号码填错，请改为 E1234567"])
    assert "passport" not in _ids(items)


def test_work_proof_time_is_task():
    items = build_task_list(["需要修改工作证明的时间"])
    assert "work_proof" in _ids(items)
    items = build_task_list(["工作经历证明起止时间与申报书不一致"])
    assert "work_proof" in _ids(items)
    items = build_task_list(["海外工作证明入职时间请改为2018年1月"])
    assert "work_proof" in _ids(items)
    items = build_task_list(["请核对该人工作证明，其中海外任职起止时间与申报书不一致，须按劳动合同修改证明上的时间"])
    assert "work_proof" in _ids(items)


def test_education_proof_date_is_task():
    items = build_task_list(["学历证明上的毕业时间有误，请更正"])
    assert "education" in _ids(items)


def test_no_task_when_only_form_rewrite():
    items = build_task_list(["工作经历请按职务职责/贡献改写，限 300 字"])
    assert "work_proof" not in _ids(items)
    items = build_task_list(["工作经历时间请按年月填写"])
    assert "work_proof" not in _ids(items)


def test_md_output():
    items = build_task_list(["缺护照附件", "请补充学位证扫描件"])
    md = format_task_list_md(items, app_name="张三.pdf", app_no="16049")
    assert "任务清单" in md
    assert "护照" in md
    assert "学历证明" in md
    assert "16049" in md


def test_found_download_status():
    attach = {
        "needed": ["护照"],
        "items": [{
            "kind": "护照", "filename": "passport.pdf",
            "download": "/api/tasks/x/ext-files/1", "source": "pool",
        }],
    }
    items = build_task_list(["请补传护照扫描件"], attach=attach)
    hit = next(x for x in items if x["id"] == "passport")
    assert hit["status"] == "found"
    assert hit["statusLabel"] == "人才库已找到"
    assert hit["downloads"]


def test_local_found_preferred():
    attach = {
        "needed": ["护照"],
        "items": [{
            "kind": "护照", "filename": "51104护照.pdf",
            "download": "/api/tasks/x/ext-files/a1", "source": "local",
            "localPath": str(LOCAL_ATTACH_ROOT / "51104" / "护照" / "51104护照.pdf"),
        }],
    }
    items = build_task_list(["请更换护照扫描件"], attach=attach)
    hit = next(x for x in items if x["id"] == "passport")
    assert hit["statusLabel"] == "本地附件已找到"
    md = format_task_list_md(items, app_no="51104")
    assert "本地附件" in md


def test_local_scan_51104():
    if not (LOCAL_ATTACH_ROOT / "51104").is_dir():
        print("skip local scan: 附件/51104 missing")
        return
    files, notes = scan_local_attachments("51104")
    assert files, notes
    kids = {f["kind_id"] for f in files}
    assert "passport" in kids
    names = " ".join(f["filename"] for f in files)
    assert "护照" in names or any(f["kind_id"] == "passport" for f in files)


def test_wecom_status_and_person_filter():
    from app.attachments import _wecom_person_ok
    hit = {"attachment_name": "51104护照.pdf", "text": "", "session_name": "材料群"}
    assert _wecom_person_ok(hit, ["51104"], [])
    assert not _wecom_person_ok({"attachment_name": "护照.pdf", "text": ""}, ["51104"], ["林启明"])
    assert _wecom_person_ok({"attachment_name": "护照.pdf", "text": "林启明补传"}, ["51104"], ["林启明"])
    attach = {
        "needed": ["护照"],
        "items": [{
            "kind": "护照", "filename": "51104护照.pdf",
            "download": "/api/tasks/x/ext-files/1", "source": "wecom",
        }],
    }
    items = build_task_list(["请更换护照扫描件"], attach=attach)
    hit = next(x for x in items if x["id"] == "passport")
    assert hit["statusLabel"] == "聊天记录已找到"


def test_write_task_list_docx():
    local = LOCAL_ATTACH_ROOT / "51104" / "护照" / "51104护照.pdf"
    items = [{
        "title": "护照",
        "status": "found",
        "statusLabel": "本地附件已找到",
        "action": "请替换护照扫描件",
        "snippet": "护照扫描件不清晰",
        "downloads": [{
            "filename": "51104护照.pdf",
            "source": "local",
            "download": "/api/tasks/demo/ext-files/a1",
            "localPath": str(local) if local.is_file() else "",
        }],
    }]
    path = ROOT / "scripts" / "_tmp_task_list.docx"
    try:
        write_task_list_docx(path, items=items, app_name="测试.pdf", app_no="51104")
        assert path.is_file() and path.stat().st_size > 800
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
        assert "任务清单" in xml
        assert "Target=" in rels
        assert "file:" in rels.lower() or "ext-files" in rels or "127.0.0.1" in rels
    finally:
        if path.exists():
            path.unlink()


if __name__ == "__main__":
    test_modify_education_and_work_proof()
    test_passport_scan_is_task()
    test_passport_number_is_not_attachment_task()
    test_work_proof_time_is_task()
    test_education_proof_date_is_task()
    test_no_task_when_only_form_rewrite()
    test_md_output()
    test_found_download_status()
    test_local_found_preferred()
    test_local_scan_51104()
    test_wecom_status_and_person_filter()
    test_write_task_list_docx()
    print("ok")
