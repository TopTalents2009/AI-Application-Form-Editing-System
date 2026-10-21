# -*- coding: utf-8 -*-
"""本机附件按申报书编号归组，入库看板一一对应。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.attachments import (
    LOCAL_ATTACH_ROOT,
    group_attach_id,
    list_local_ingest_items,
)
from app.wecom_talent_db import list_board, _row_attach_id, _group_files


def test_group_attach_id():
    assert group_attach_id("42413") == "42413"
    assert group_attach_id("0515") == "515"
    assert group_attach_id("无编号") == ""
    assert group_attach_id("") == ""
    print("group attach id ok")


def test_list_local_matches_folder():
    assert LOCAL_ATTACH_ROOT.is_dir(), "缺少本机附件目录"
    items = list_local_ingest_items(q="40989", limit=80)
    assert items, "40989 目录应有文件"
    assert all(x.get("attachId") == "40989" for x in items)
    assert all(x.get("source") == "local" and x.get("url") for x in items)
    kinds = {x.get("kind") for x in items}
    assert "passport" in kinds or "work_proof" in kinds, kinds
    print("local list 40989 ok", len(items))


def test_filename_fills_attach_id():
    assert _row_attach_id({"filename": "40989护照.pdf", "attach_id": ""}) == "40989"
    assert _row_attach_id({"filename": "护照.pdf", "attach_id": ""}) == ""
    print("filename attach id ok")


def test_board_groups_local_with_same_id():
    groups, files = _group_files([
        {"attachId": "0515", "source": "app_edit", "ok": True, "filename": "515-人才库-v1.json", "versionN": 1},
        {"attachId": "515", "source": "local", "ok": True, "filename": "1.护照.pdf", "label": "护照"},
    ])
    assert len(groups) == 1
    assert groups[0]["attachId"] == "515"
    names = [f.get("filename") for f in groups[0]["files"]]
    assert "515-人才库-v1.json" in names
    assert "1.护照.pdf" in names
    print("group merge ok")


def test_list_board_includes_local():
    board = list_board(q="40989", source="local", ok_only=True)
    assert board.get("localCount") >= 1
    groups = {str(g.get("attachId") or ""): g for g in (board.get("groups") or [])}
    assert "40989" in groups
    assert any(f.get("source") == "local" for f in groups["40989"]["files"])
    print("board local 40989 ok", board.get("localCount"))


if __name__ == "__main__":
    test_group_attach_id()
    test_list_local_matches_folder()
    test_filename_fills_attach_id()
    test_board_groups_local_with_same_id()
    test_list_board_includes_local()
    print("ALL OK")
