# -*- coding: utf-8 -*-
"""生成计划前：已有论文全文时不得空等论文系统构建。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.attachments import PAPERS_PLAN_SEARCH_SEC, PAPERS_PLAN_WAIT_SEC, resolve_missing
from app.papers import PapersError, search_authors


def test_skip_papers_job_when_fulltext_found():
    called = {"ensure": 0}

    async def boom(*_a, **_k):
        called["ensure"] += 1
        raise AssertionError("ensure_talent should not run when 论文全文 already found")

    snap = {"keys": {"names": ["Demo"], "attachIds": ["ZZSKIP1"]}, "talent": {}, "hit": {}}
    prev = {
        "items": [{
            "kind": "论文全文", "source": "pool", "filename": "7733_论文.pdf",
            "download": "/d/a", "found": True,
        }],
        "private": {},
        "notes": [],
        "papersFetched": False,
        "codebuddyFetched": True,
        "generateFetched": True,
    }
    with patch("app.attachments.load_config", return_value={"poolConfigured": False, "papersConfigured": True}), \
         patch("app.attachments.scan_wecom_attachments", new=AsyncMock(return_value=([], []))), \
         patch("app.attachments.POOL.resolve_attach_ids", new=AsyncMock(return_value=([], []))), \
         patch("app.attachments.P.ensure_talent", new=boom), \
         patch("app.attachments.P.get_talent", new=AsyncMock(side_effect=AssertionError("get_talent skipped"))):
        r = asyncio.run(resolve_missing(
            "t-skip-papers",
            ["请补充论文全文 PDF"],
            snap,
            "ZZSKIP1",
            prev=prev,
            app_text="",
        ))
    assert called["ensure"] == 0
    assert any("跳过论文系统构建等待" in n for n in (r.get("notes") or [])), r.get("notes")
    assert r.get("papersFetched") is True
    print("[OK] skip papers job when 论文全文 already found")


def test_plan_wait_caps_passed():
    seen = {}

    async def fake_ensure(aid, **kwargs):
        seen.update(kwargs)
        raise PapersError("HTTP_500", "stop after capturing kwargs")

    snap = {"keys": {"names": ["Demo Author"], "attachIds": ["ZZWAIT1"]}, "talent": {}, "hit": {}}
    prev = {
        "items": [],
        "private": {},
        "notes": [],
        "papersFetched": False,
        "codebuddyFetched": True,
        "generateFetched": True,
    }
    with patch("app.attachments.load_config", return_value={"poolConfigured": False, "papersConfigured": True}), \
         patch("app.attachments.scan_wecom_attachments", new=AsyncMock(return_value=([], []))), \
         patch("app.attachments.POOL.resolve_attach_ids", new=AsyncMock(return_value=(["ZZWAIT1"], []))), \
         patch("app.attachments.P.ensure_talent", new=fake_ensure):
        r = asyncio.run(resolve_missing(
            "t-cap-wait",
            ["请补充论文全文 PDF"],
            snap,
            "ZZWAIT1",
            prev=prev,
            app_text="",
        ))
    assert seen.get("timeout_sec") == PAPERS_PLAN_WAIT_SEC, seen
    assert seen.get("search_timeout") == PAPERS_PLAN_SEARCH_SEC, seen
    assert any("论文系统" in n for n in (r.get("notes") or [])), r.get("notes")
    print("[OK] plan pipeline caps papers wait at", PAPERS_PLAN_WAIT_SEC, "s")


def test_search_authors_caps_names():
    n = {"n": 0}

    async def fake_search(author, *, per_page=50, timeout=90.0):
        n["n"] += 1
        from app.papers import PapersError
        raise PapersError("HTTP_500", "down")

    with patch("app.papers.search_author", new=fake_search):
        rows, used = asyncio.run(search_authors(
            ["Alice Alpha", "Bob Beta", "Carol Gamma"],
            timeout=20.0, max_names=2,
        ))
    assert rows == []
    assert used == ""
    assert n["n"] == 2
    print("[OK] search_authors respects max_names=2")


if __name__ == "__main__":
    test_skip_papers_job_when_fulltext_found()
    test_plan_wait_caps_passed()
    test_search_authors_caps_names()
    print("all ok")
