# -*- coding: utf-8 -*-
"""整机迁移脚本的离线检查。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import migrate  # noqa: E402


def test_export_source_follows_junction():
    exp = ROOT / "parser" / "export"
    assert exp.exists()
    src = migrate.export_source(ROOT)
    assert src.is_dir()
    if migrate.is_reparse(exp):
        assert src != exp
        assert (src / "synced").is_dir() or any(src.iterdir())
    print("export_source ok", src)


def test_required_missing_flags_empty():
    with tempfile.TemporaryDirectory() as td:
        pack = Path(td)
        miss = migrate.required_missing(pack)
        assert "run.py" in " ".join(miss) or any("run.py" in m for m in miss)
        assert any("shenbaoshu.sql" in m for m in miss)
    print("required_missing empty pack ok")


def test_rewrite_abs_paths(tmp_path=None):
    folder = Path(tempfile.mkdtemp())
    cfg = folder / "config.json"
    old = r"C:\Users\1\Desktop\work\825\agent修改申报书_py"
    payload = {
        "scanPdf": {"hybrid": {"tesseractCmd": old + r"\tesseract.exe"}},
        "projectProof": {"codebuddy": {"cmd": "codebuddy.cmd"}},
        "mysql": {"password": "keep-me"},
    }
    cfg.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    notes = migrate.rewrite_abs_paths(cfg, old, str(folder))
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mysql"]["password"] == "keep-me"
    tess = data["scanPdf"]["hybrid"]["tesseractCmd"]
    assert old not in tess
    assert notes
    print("rewrite_abs_paths ok")


def test_skip_dirs_and_mysql_bin():
    assert ".venv" in migrate.SKIP_DIR_NAMES
    assert "node_modules" in migrate.SKIP_DIR_NAMES
    dump = migrate.find_mysql_bin("mysqldump")
    assert dump is not None and dump.is_file(), dump
    print("mysql bin", dump)


def test_pack_parser():
    p = migrate.build_parser()
    ns = p.parse_args(["pack", "--out", "D:\\tmp\\x", "--no-restart"])
    assert ns.cmd == "pack"
    assert ns.no_restart
    ns = p.parse_args(["restore", "--from", "D:\\pack", "--check"])
    assert ns.check
    print("cli ok")


if __name__ == "__main__":
    test_export_source_follows_junction()
    test_required_missing_flags_empty()
    test_rewrite_abs_paths()
    test_skip_dirs_and_mysql_bin()
    test_pack_parser()
    print("test_migrate ok")
