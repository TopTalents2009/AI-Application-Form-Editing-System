# -*- coding: utf-8 -*-
"""走一遍确认写入后的人才库入库流程：改字段 → 写入 MySQL → 标版本 → 再改一版。"""
from __future__ import annotations

import json
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db
from app.db import init_db
from app.pool import build_database_export
from app.talent_app_files import list_by_attach, load_export, save_from_task
from app.wecom_locate import identity_of

AID = "__flow_8091__"
SRC = ROOT / "tasks" / "1a0cd4ad7fb-526011" / "work" / "tmp" / "pool.json"
REPORT = ROOT / "_tmp_flow_report.txt"


def _cleanup():
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM talent_app_files WHERE attach_id=%s", (AID,))
    finally:
        conn.close()


def _write_like_runner(out_dir: Path, packed: dict, saved: dict) -> Path:
    aid = str(saved.get("attach_id") or "").strip()
    ver = str(saved.get("version") or "").strip()
    name = (aid + "-人才库-" + ver + ".json") if aid and ver else (
        (aid + "-人才库.json") if aid else "人才库.json"
    )
    for old in out_dir.glob("*人才库*.json"):
        if old.name != name:
            old.unlink()
    dest = out_dir / name
    dest.write_text(json.dumps(packed, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main() -> None:
    lines = []
    init_db()
    _cleanup()
    assert SRC.is_file(), "缺少 54332 pool.json，无法用真实 8091 结构测试"
    snap = json.loads(SRC.read_text(encoding="utf-8"))
    talent0 = snap["talent"]
    assert talent0["attach_id"] == "54332"
    assert isinstance(talent0["payload"].get("last_application"), dict)
    last_type = talent0["payload"]["last_application"]["申报信息"]["申报类型"]
    old_title = talent0["payload"]["申报人基本信息"]["拟任职职务"]
    new_title = "流程测试职务-v1"

    task = {
        "id": "flow-8091-v1",
        "dir": str(ROOT / "talent_outputs" / "_flow_8091"),
        "app": {
            "name": "8091申报书.xlsx",
            "no": "8091",
            "attachId": AID,
            "personName": talent0["name"],
            "mode": talent0["mode"],
        },
    }
    ident = identity_of(task)
    assert ident["attachId"] == AID, ident
    work = Path(task["dir"]) / "work" / "output"
    if Path(task["dir"]).exists():
        shutil.rmtree(task["dir"], ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    edits = [{"find": old_title, "replace": new_title}]
    applied = [{"status": "hit"}]
    export1 = build_database_export(
        snap, edits, applied,
        attach_id=AID,
        name=str(task["app"]["personName"]),
        mode=str(task["app"]["mode"]),
    )
    (work / (AID + "-人才库.json")).write_text(
        json.dumps(export1, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    row1 = save_from_task(task, export1)
    assert row1, "v1 未写入数据库"
    assert row1["version"] == "v1", row1
    assert row1["attach_id"] == AID
    packed1 = load_export(row1)
    f1 = _write_like_runner(work, packed1, row1)
    assert packed1["version"] == "v1"
    assert packed1["version_n"] == 1
    assert packed1["attach_id"] == AID
    assert packed1["talent"]["attach_id"] == AID
    assert packed1["talent"]["name"] == talent0["name"]
    assert packed1["talent"]["mode"] == "HJ"
    assert packed1["talent"]["source_year"] == "26年"
    assert packed1["talent"]["payload"]["申报人基本信息"]["拟任职职务"] == new_title
    assert packed1["talent"]["last_application"]["申报信息"]["申报类型"] == last_type
    assert packed1["talent"]["payload"]["last_application"]["申报信息"]["申报类型"] == last_type
    assert f1.name == AID + "-人才库-v1.json"
    assert not (work / (AID + "-人才库.json")).exists()
    lines.append("v1 入库 ok  id=%s file=%s title=%s" % (row1["id"], f1.name, new_title))

    export2 = json.loads(json.dumps(packed1, ensure_ascii=False))
    export2["talent"]["payload"]["申报人基本信息"]["拟任职职务"] = "流程测试职务-v2"
    export2.pop("version", None)
    export2.pop("version_n", None)
    task["id"] = "flow-8091-v2"
    row2 = save_from_task(task, export2)
    assert row2["version"] == "v2", row2
    packed2 = load_export(row2)
    f2 = _write_like_runner(work, packed2, row2)
    assert packed2["version"] == "v2"
    assert packed2["talent"]["payload"]["申报人基本信息"]["拟任职职务"] == "流程测试职务-v2"
    assert load_export(row1)["talent"]["payload"]["申报人基本信息"]["拟任职职务"] == new_title
    assert f2.name == AID + "-人才库-v2.json"
    assert not f1.exists()
    items = list_by_attach(AID)
    assert [x["version"] for x in items] == ["v2", "v1"], items
    lines.append("v2 入库 ok  id=%s file=%s" % (row2["id"], f2.name))
    lines.append("版本列表 " + json.dumps(
        [{"version": x["version"], "taskId": x["task_id"], "name": x["orig_name"]} for x in items],
        ensure_ascii=False,
    ))

    ports = [p for p in (3777, 3778) if _port_open(p)]
    lines.append("server_ports=" + json.dumps(ports))

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    _cleanup()
    shutil.rmtree(task["dir"], ignore_errors=True)
    print("ALL OK")


if __name__ == "__main__":
    main()
