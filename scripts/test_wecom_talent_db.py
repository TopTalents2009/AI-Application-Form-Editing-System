# -*- coding: utf-8 -*-
"""本地入库库：建库建表、写入值班/文件汇总记录、按 key 去重。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_talent_db import get_ok, init_db, list_board, upsert, wecom_db_name
from app.wecom_talent_upload import already_uploaded, file_key, remember


def test_remember_still_in_memory():
    state = {"items": {}}
    key = file_key("R:db", 7, "护照.pdf")
    remember(state, key, attach_id="51104", kind="passport", filename="护照.pdf", ok=True)
    assert already_uploaded(state, key)
    print("memory remember ok")


def test_local_mysql_ingest():
    info = init_db()
    assert info.get("ok")
    db = wecom_db_name()
    assert db == "shenbaoshu_wecom" or db
    key = file_key("R:dbtest", 90001, "学历证明.pdf")
    upsert({
        "file_key": key,
        "source": "watch",
        "session_id": "R:dbtest",
        "session_name": "测试群",
        "message_id": "90001",
        "filename": "学历证明.pdf",
        "kind": "education",
        "label": "学历证明",
        "attach_id": "51104",
        "ok": True,
        "status": "uploaded",
        "detail": "unit-test",
    })
    hit = get_ok(key)
    assert hit and hit.get("ok")
    assert hit.get("attachId") == "51104"
    upsert({
        "file_key": key,
        "source": "file_summary",
        "ok": False,
        "status": "error",
        "detail": "should-not-overwrite-ok",
        "attach_id": "51104",
        "filename": "学历证明.pdf",
    })
    hit2 = get_ok(key)
    assert hit2 and hit2.get("ok")
    board = list_board(q="51104", source="watch", ok_only=True)
    assert board.get("ok")
    groups = {str(g.get("attachId") or ""): g for g in (board.get("groups") or [])}
    assert "51104" in groups, list(groups)[:8]
    names = [f.get("filename") for f in (groups["51104"].get("files") or [])]
    assert "学历证明.pdf" in names
    print("mysql ingest ok", db)


if __name__ == "__main__":
    test_remember_still_in_memory()
    test_local_mysql_ingest()
    print("ALL OK")
