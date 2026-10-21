# -*- coding: utf-8 -*-
"""文件分类：用前后聊天上下文纠偏，不调用模型。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.wecom_cases import cluster_messages
from app.wecom_file_class import (
    after_cursor,
    build_file_groups,
    can_be_app_file,
    classify_name_with_context,
    counts_from_groups,
    cursor_of,
    gather_context,
    get_summary_cache,
    image_media_type,
    incremental_work_window,
    is_image_message,
    is_screenshot_name,
    list_file_entries,
    pick_cached_copy,
    load_summary_state,
    make_cluster_kind_of,
    merge_file_groups,
    refresh_group_image_flags,
    resolve_wecom_cached_file,
    resolve_wecom_temp_image,
    screenshot_name_variants,
    save_summary_state,
)


def _m(mid, name="", text="", sender="陈侃", time="2026-09-01 10:00:00"):
    row = {
        "message_id": mid,
        "text": text,
        "sender": sender,
        "time_text": time,
    }
    if name:
        row["attachment_name"] = name
        row["has_attachment"] = True
    return row


def test_person_pdf_passport_from_prev_chat():
    msgs = [
        _m(1, text="这是护照扫描件，请归档"),
        _m(2, "张三.pdf"),
    ]
    items = list_file_entries(msgs)
    assert len(items) == 1
    assert items[0]["label"] == "护照", items[0]
    assert items[0]["source"] == "context"


def test_explicit_app_not_stolen_by_passport_chat():
    msgs = [
        _m(1, text="护照也发一下"),
        _m(2, "51104申报书.docx"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "申报书", items[0]


def test_same_sender_next_line_caption():
    msgs = [
        _m(1, "杜垚.pdf"),
        _m(2, text="这是护照扫描件"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "护照", items[0]


def test_meeting_minutes_not_opinion():
    msgs = [_m(1, "评审会议纪要.docx")]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "其他", items[0]


def test_opinion_from_following_chat():
    msgs = [
        _m(1, "辅导.docx"),
        _m(2, text="这是修改意见，请按意见改申报书"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "修改意见", items[0]


def test_project_plan_not_project_proof():
    msgs = [_m(1, "XX项目计划.xlsx")]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "其他", items[0]


def test_work_proof_from_filename():
    msgs = [_m(1, "海外工作证明.pdf")]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "工作证明", items[0]


def test_soft_chat_passport_on_person_pdf():
    msgs = [
        _m(1, text="把护照发来"),
        _m(2, "张三.pdf"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "护照", items[0]


def test_soft_education_before_scan():
    msgs = [
        _m(1, text="先发学历"),
        _m(2, "scan.jpg"),
    ]
    items = list_file_entries(msgs)
    assert items[0]["label"] == "学历证明", items[0]


def test_gather_includes_neighbors():
    msgs = [
        _m(1, text="先发学历"),
        _m(2, "scan.jpg"),
        _m(3, text="毕业证复印件"),
    ]
    ctx = gather_context(msgs, 1)
    blob = ctx["blob"]
    assert "学历" in blob
    assert "毕业证" in blob
    hit = classify_name_with_context("scan.jpg", ctx)
    assert hit["label"] == "学历证明", hit


def test_watch_kind_passport_not_app():
    msgs = [
        _m(1, text="这是护照扫描件，请归档"),
        _m(2, "张三.pdf"),
    ]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[1]) == "ignore"
    cases = cluster_messages(msgs, session_id="R:w", session_name="值班", kind_of=kind_of)
    assert cases == []


def test_watch_kind_chat_opinion():
    msgs = [
        _m(1, "辅导.docx"),
        _m(2, text="这是修改意见，请按意见改申报书"),
        _m(3, "51104申报书.docx"),
    ]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[0]) == "opinion"
    assert kind_of(msgs[2]) == "app"
    cases = cluster_messages(msgs, session_id="R:w", session_name="值班", kind_of=kind_of)
    assert len(cases) == 1
    ops = [o.get("filename") for o in (cases[0].get("opinions") or [])]
    assert "辅导.docx" in ops


def test_watch_kind_meeting_not_opinion():
    msgs = [_m(1, "评审会议纪要.docx")]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[0]) == "ignore"


def test_wecom_screenshot_not_app_or_version():
    shot = r"C:\Users\1\AppData\Local\Temp\企业微信截图_17920410439117.png"
    assert is_screenshot_name(shot)
    assert not can_be_app_file(shot)
    msgs = [
        _m(1, text="这是新申报书，请按这版改"),
        _m(2, shot),
        _m(3, "51104申报书.docx"),
    ]
    items = list_file_entries(msgs)
    by_name = {it["filename"]: it for it in items}
    assert by_name[shot]["label"] != "申报书", by_name[shot]
    assert by_name["51104申报书.docx"]["label"] == "申报书"
    kind_of = make_cluster_kind_of(items)
    assert kind_of(msgs[1]) != "app"
    assert kind_of(msgs[2]) == "app"
    from app.wecom_ai_bind import extract_catalog
    cat = extract_catalog(msgs, kind_of=kind_of)
    names = [a["filename"] for a in cat["apps"]]
    assert shot not in names
    assert "51104申报书.docx" in names


def test_nearby_申报书_chat_does_not_promote_xlsx_or_screenshot():
    shot = r"C:\Users\1\AppData\Local\Temp\企业微信截图_17920417177689.png"
    msgs = [
        _m(1, "大部分有Nature(1).xlsx"),
        _m(2, text="据悉，今年的申报书有比较大的改版"),
        _m(3, shot),
    ]
    items = list_file_entries(msgs)
    by_name = {it["filename"]: it for it in items}
    assert by_name["大部分有Nature(1).xlsx"]["label"] != "申报书", by_name["大部分有Nature(1).xlsx"]
    assert by_name[shot]["label"] != "申报书", by_name[shot]
    kind_of = make_cluster_kind_of(items)
    assert kind_of(msgs[0]) != "app"
    assert kind_of(msgs[2]) != "app"
    cached = {
        "app": {
            "type": "app",
            "label": "申报书",
            "reason": "旧缓存",
            "source": "gemini",
        }
    }
    from app.wecom_file_class import sanitize_classified_entry
    fake = {"filename": shot, "type": "app", "label": "申报书", "isImage": True}
    sanitize_classified_entry(fake)
    assert fake["type"] != "app"
    assert fake["label"] != "申报书"


def test_watch_kind_person_pdf_stays_app_without_chat():
    msgs = [_m(1, "张三.pdf")]
    kind_of = make_cluster_kind_of(list_file_entries(msgs))
    assert kind_of(msgs[0]) == "app"


def test_image_message_and_mime():
    assert is_image_message({"media_url": "https://wework.qpic.cn/x"})
    assert is_image_message({"msg_type": 4, "attachment_name": "图片"})
    assert is_image_message({"msg_type": 4, "has_attachment": True, "attachment_name": "图片"})
    assert is_image_message({
        "has_attachment": True,
        "attachment_name": "图片",
        "media_url": "https://wework.qpic.cn/x",
    })
    assert is_image_message({
        "has_attachment": True,
        "attachment_name": "图片",
        "msg_type_label": "图片",
    })
    assert is_image_message(filename="截图.png")
    assert is_image_message(filename=r"C:\Users\1\AppData\Local\Temp\企业微信截图_17920417177689.png")
    assert not is_image_message(filename="51104申报书.docx")
    assert not is_image_message(filename="申报书.wps", media_url="https://wework.qpic.cn/cover")
    assert not is_image_message({"has_attachment": True, "media_url": "https://wework.qpic.cn/cover", "attachment_name": "材料.wps"})
    assert not is_image_message(filename="说明.pdf", media_url="https://wework.qpic.cn/cover")
    assert image_media_type("a.png") == "image/png"
    assert image_media_type(r"C:\Users\1\AppData\Local\Temp\企业微信截图_17920417177689.png") == "image/png"
    assert image_media_type("a.bin", content=b"\x89PNG\r\n\x1a\nxxxx") == "image/png"
    assert image_media_type("a.bin", content=b"%PDF-1.4") == ""
    assert image_media_type("a.bin", content=b"II*\x00xxxx") == "image/tiff"


def test_resolve_wecom_temp_image():
    import tempfile
    tmp = Path(tempfile.gettempdir())
    p = tmp / "企业微信截图_17921122095358.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    try:
        got = resolve_wecom_temp_image(str(p))
        assert got is not None and got.name == p.name
        got2 = resolve_wecom_temp_image(p.name)
        assert got2 is not None and got2.name == p.name
        got3 = resolve_wecom_temp_image(r"C:\Users\1\AppData\Local\Temp\\" + p.name)
        if Path(r"C:\Users\1\AppData\Local\Temp").exists():
            assert got3 is None or got3.name == p.name
        assert resolve_wecom_temp_image("secret.txt") is None
        assert resolve_wecom_temp_image("申报书.docx") is None
        assert resolve_wecom_temp_image(r"C:\Windows\System32\config\SAM") is None
        assert resolve_wecom_temp_image("企业微信截图_*.png") is None
        assert resolve_wecom_temp_image("../企业微信截图_1.png") is None or resolve_wecom_temp_image("../企业微信截图_1.png").name == p.name
    finally:
        try:
            p.unlink()
        except OSError:
            pass


def test_screenshot_copy_suffix_and_cached_file():
    import tempfile
    tmp = Path(tempfile.gettempdir())
    p = tmp / "企业微信截图_1778848212580.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    try:
        assert screenshot_name_variants("企业微信截图_1778848212580(1).png")[1] == "企业微信截图_1778848212580.png"
        got = resolve_wecom_temp_image("企业微信截图_1778848212580(1).png")
        assert got is not None and got.name == p.name
        cached = resolve_wecom_cached_file(
            "machefu003-1688858081684753",
            68439,
            "企业微信截图_1778848212580(1).png",
        )
        if cached is not None:
            assert "1778848212580" in cached.name
    finally:
        try:
            p.unlink()
        except OSError:
            pass


def test_pick_cached_copy_prefers_real_file():
    name = "企业微信截图_17784878556121.png"
    copies = [
        {"source_id": "SKY-20260607ENA-1688855565376327", "message_id": 21428},
        {"source_id": "machefu003-1688858081684753", "message_id": 62252},
    ]
    sid, mid = pick_cached_copy(name, copies, 21428)
    if not sid and not mid:
        return
    assert mid == 62252, (sid, mid)
    assert "machefu003" in sid, sid


def test_list_file_entries_keeps_screenshot_preview():
    named = _m(3, "图片")
    named["media_url"] = "https://wework.qpic.cn/orig"
    msgs = [
        _m(1, "截图.png"),
        {"message_id": 2, "text": "", "sender": "陈侃", "time_text": "2026-09-01 10:00:00", "media_url": "https://wework.qpic.cn/p"},
        named,
    ]
    items = list_file_entries(msgs)
    by_mid = {str(it["messageId"]): it for it in items}
    assert by_mid["1"]["isImage"] is True
    assert by_mid["2"]["isImage"] is True
    assert by_mid["2"]["mediaUrl"].startswith("https://wework.qpic.cn")
    assert by_mid["3"]["isImage"] is True
    assert by_mid["3"]["filename"] == "图片"
    groups, n = build_file_groups(items, msgs, session_id="R:img", session_name="图")
    files = [f for g in groups for f in g["files"]]
    by_file = {str(f["messageId"]): f for f in files}
    assert by_file["3"]["isImage"] is True
    assert by_file["3"]["mediaUrl"].startswith("https://wework.qpic.cn")
    stale = [{"id": "other", "label": "其他", "files": [{
        "filename": "图片",
        "messageId": 9,
        "has_attachment": True,
        "media_url": "https://wework.qpic.cn/old",
        "isImage": False,
        "copies": [],
    }]}]
    fixed = refresh_group_image_flags(stale)
    assert fixed[0]["files"][0]["isImage"] is True
    assert fixed[0]["files"][0]["mediaUrl"].startswith("https://wework.qpic.cn")


def test_later_app_filename_does_not_reclassify_passport():
    msgs = [
        _m(1, text="这是护照扫描件，请归档"),
        _m(2, "张三.pdf"),
        _m(3, "51104申报书.docx"),
        _m(4, "51104_修改意见.txt"),
    ]
    items = list_file_entries(msgs)
    by_name = {it["filename"]: it for it in items}
    assert by_name["张三.pdf"]["label"] == "护照", by_name["张三.pdf"]
    assert by_name["51104申报书.docx"]["label"] == "申报书"
    kind_of = make_cluster_kind_of(items)
    assert kind_of(msgs[1]) == "ignore"
    assert kind_of(msgs[2]) == "app"


def test_after_cursor_and_window():
    msgs = [
        _m(1, "旧护照.pdf", time="2026-09-01 10:00:00"),
        _m(2, text="说明", time="2026-09-01 10:01:00"),
        _m(3, "新学历.pdf", time="2026-09-01 11:00:00"),
    ]
    assert after_cursor(msgs[0], "", "")
    assert not after_cursor(msgs[0], "2026-09-01 10:01:00", "2")
    assert not after_cursor(msgs[1], "2026-09-01 10:01:00", "2")
    assert after_cursor(msgs[2], "2026-09-01 10:01:00", "2")
    first, work = incremental_work_window(msgs, "2026-09-01 10:01:00", "2")
    assert first == 2
    assert work[0]["message_id"] == 1
    t, i = cursor_of(msgs)
    assert t == "2026-09-01 11:00:00"
    assert str(i) == "3"


def test_merge_file_groups_keeps_old_and_adds_new():
    old = [{"id": "passport", "label": "护照", "files": [
        {"filename": "旧护照.pdf", "time": "2026-09-01 10:00:00", "sender": "陈侃", "messageId": 1},
    ]}]
    extra = [
        {"id": "passport", "label": "护照", "files": [
            {"filename": "旧护照.pdf", "time": "2026-09-01 10:00:00", "sender": "陈侃", "messageId": 1},
        ]},
        {"id": "education", "label": "学历证明", "files": [
            {"filename": "新学历.pdf", "time": "2026-09-01 11:00:00", "sender": "陈侃", "messageId": 3},
        ]},
    ]
    got = merge_file_groups(old, extra)
    by = {g["id"]: g for g in got}
    assert len(by["passport"]["files"]) == 1
    assert len(by["education"]["files"]) == 1
    counts = counts_from_groups(got)
    assert counts["护照"] == 1
    assert counts["学历证明"] == 1


def test_cached_file_rejects_glob_and_escape():
    import tempfile
    import app.wecom_local as L
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name) / "data"
    files = root / "pc1" / "files"
    files.mkdir(parents=True)
    good = files / "12_企业微信截图_199.png"
    good.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    other = files / "99_secret.png"
    other.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 40)
    escape = Path(tmp.name) / "files"
    escape.mkdir()
    evil = escape / "77_企业微信截图_escape.png"
    evil.write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 40)
    old = L.data_root
    try:
        L.data_root = lambda: root
        hit = resolve_wecom_cached_file("pc1", 12, "企业微信截图_199.png")
        assert hit is not None and hit.name == good.name
        assert resolve_wecom_cached_file("pc1", 0, "*") is None
        assert resolve_wecom_cached_file("pc1", 0, "企业微信截图_*.png") is None
        assert resolve_wecom_cached_file("pc1", 0, "../no-such.png") is None
        assert resolve_wecom_cached_file("..", 77, "企业微信截图_escape.png") is None
        assert resolve_wecom_cached_file("../files", 77, "企业微信截图_escape.png") is None
    finally:
        L.data_root = old
        tmp.cleanup()


def test_summary_state_persists_cursor_across_stamp_change():
    import tempfile
    import app.wecom_file_class as F
    old_dir = F._SUMMARY_DIR
    old_mem = dict(F._SUMMARY_CACHE)
    tmp = tempfile.TemporaryDirectory()
    F._SUMMARY_DIR = Path(tmp.name)
    F._SUMMARY_CACHE.clear()
    try:
        msgs = [_m(1, "旧护照.pdf", time="2026-09-01 10:00:00")]
        groups, n = build_file_groups(list_file_entries(msgs), msgs, session_id="R:t", session_name="测")
        assert n == 1
        save_summary_state(
            "R:t",
            stamp="v1",
            result={"groups": groups, "messageCount": 1, "session_name": "测"},
            messages=msgs,
            full_done=True,
        )
        hit = get_summary_cache("R:t", "v1")
        assert hit and hit["groups"]
        assert get_summary_cache("R:t", "v2") is None
        state = load_summary_state("R:t")
        assert state["full_done"] is True
        assert state["last_time"] == "2026-09-01 10:00:00"
        assert str(state["last_message_id"]) == "1"
        F._SUMMARY_CACHE.clear()
        state2 = load_summary_state("R:t")
        assert state2["last_time"] == "2026-09-01 10:00:00"
        assert str(state2["last_message_id"]) == "1"
    finally:
        F._SUMMARY_CACHE.clear()
        F._SUMMARY_CACHE.update(old_mem)
        F._SUMMARY_DIR = old_dir
        tmp.cleanup()


if __name__ == "__main__":
    test_person_pdf_passport_from_prev_chat()
    test_explicit_app_not_stolen_by_passport_chat()
    test_same_sender_next_line_caption()
    test_meeting_minutes_not_opinion()
    test_opinion_from_following_chat()
    test_project_plan_not_project_proof()
    test_work_proof_from_filename()
    test_soft_chat_passport_on_person_pdf()
    test_soft_education_before_scan()
    test_gather_includes_neighbors()
    test_watch_kind_passport_not_app()
    test_watch_kind_chat_opinion()
    test_watch_kind_meeting_not_opinion()
    test_wecom_screenshot_not_app_or_version()
    test_nearby_申报书_chat_does_not_promote_xlsx_or_screenshot()
    test_watch_kind_person_pdf_stays_app_without_chat()
    test_image_message_and_mime()
    test_resolve_wecom_temp_image()
    test_screenshot_copy_suffix_and_cached_file()
    test_pick_cached_copy_prefers_real_file()
    test_cached_file_rejects_glob_and_escape()
    test_list_file_entries_keeps_screenshot_preview()
    test_later_app_filename_does_not_reclassify_passport()
    test_after_cursor_and_window()
    test_merge_file_groups_keeps_old_and_adds_new()
    test_summary_state_persists_cursor_across_stamp_change()
    print("ALL OK")
