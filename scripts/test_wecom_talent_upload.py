# -*- coding: utf-8 -*-
"""会话汇总新文件按人才编号匹配入库：纯函数，不调网络。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_talent_upload import (
    attach_id_of,
    fill_attach_ids,
    file_key,
    annotate_files,
    already_uploaded,
    remember,
    uploadable_kind,
    pool_kind_of,
    select_fresh_files,
)


def test_attach_id_from_filename():
    assert attach_id_of(filename="51104_护照.pdf") == "51104"
    assert attach_id_of(filename="113255_广东拓斯达科技股份有限公司.pdf") == "113255"
    assert attach_id_of(filename="截图.png") == ""
    assert attach_id_of(filename="9Adam_申报书_20260317.docx") == ""


def test_uploadable_kinds():
    assert uploadable_kind("passport")
    assert uploadable_kind("photo")
    assert uploadable_kind("education")
    assert not uploadable_kind("app")
    assert not uploadable_kind("opinion")
    assert not uploadable_kind("other")
    assert pool_kind_of("education") == "edu"
    assert pool_kind_of("passport") == "passport"


def test_fill_inherit_from_nearby_app():
    files = [
        {"filename": "51104申报书.docx", "type": "app", "time": "2026-09-01 10:00:00", "messageId": 1},
        {"filename": "护照扫描件.png", "type": "passport", "time": "2026-09-01 10:20:00", "messageId": 2},
        {"filename": "白底照.jpg", "type": "photo", "time": "2026-09-01 11:00:00", "messageId": 3},
        {"filename": "52063申报书.docx", "type": "app", "time": "2026-09-02 09:00:00", "messageId": 4},
        {"filename": "学历.pdf", "type": "education", "time": "2026-09-02 10:00:00", "messageId": 5},
    ]
    got = fill_attach_ids(files)
    by = {str(x["messageId"]): x for x in got}
    assert by["1"]["attachId"] == "51104"
    assert by["2"]["attachId"] == "51104"
    assert by["3"]["attachId"] == "51104"
    assert by["5"]["attachId"] == "52063"


def test_annotate_and_remember():
    state = {"items": {}}
    remember(state, file_key("R:x", 2, "护照扫描件.png"), attach_id="51104", kind="passport", filename="护照扫描件.png", ok=True)
    files = [
        {"filename": "51104申报书.docx", "type": "app", "time": "2026-09-01 10:00:00", "messageId": 1, "sessionId": "R:x"},
        {"filename": "护照扫描件.png", "type": "passport", "time": "2026-09-01 10:20:00", "messageId": 2, "sessionId": "R:x"},
        {"filename": "其它.txt", "type": "other", "time": "2026-09-01 10:30:00", "messageId": 3, "sessionId": "R:x"},
        {"filename": "工作证明.pdf", "type": "work_proof", "time": "2026-09-01 10:40:00", "messageId": 4, "sessionId": "R:x"},
    ]
    got = annotate_files(files, state)
    by = {str(x["messageId"]): x for x in got}
    assert by["1"]["uploadStatus"] == "not_kind"
    assert by["2"]["uploadStatus"] == "uploaded"
    assert by["2"]["attachId"] == "51104"
    assert by["3"]["uploadStatus"] == "not_kind"
    assert by["4"]["uploadStatus"] == "pending"
    assert by["4"]["attachId"] == "51104"
    assert already_uploaded(state, file_key("R:x", 2, "护照扫描件.png"))


def test_select_fresh_files():
    entries = [
        {"messageId": 1, "filename": "旧护照.pdf"},
        {"messageId": 2, "filename": "新学历.pdf"},
        {"messageId": 3, "filename": "其它.pdf"},
    ]
    got = select_fresh_files(entries, [2, 9])
    assert [x["filename"] for x in got] == ["新学历.pdf"]


if __name__ == "__main__":
    test_attach_id_from_filename()
    test_uploadable_kinds()
    test_fill_inherit_from_nearby_app()
    test_annotate_and_remember()
    test_select_fresh_files()
    print("ALL OK")
