# -*- coding: utf-8 -*-
"""Jev 评分、组长审核字段。不调网络。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.agent_pipeline import _after_cursor, fallback_filter, is_chatter_text, needs_gemini, parse_filter_json, place_messages
from app.agent_review import (
    SCORE_MIN,
    add_example,
    candidates_of,
    connect,
    delete_example,
    example_stats,
    fallback_candidates,
    get_run,
    hit_shown,
    list_examples,
    list_hits,
    list_runs,
    load_examples,
    rebuild_examples_from_reviews,
    save_hits,
    save_run,
    set_example,
    sort_hits,
    upsert_review,
)
from app.party import classify, normalize_party, party_of
from app.wecom_intent import answers_to_intent, rank_questions, ranked_intents, rule_intent, score_of


def test_party_rules():
    assert party_of("Reaching") == "甲方"
    assert party_of("郑建雄") == "乙方"
    assert party_of("宝珠") == "甲方"
    assert classify("张三", "wechat")["party"] == "乙方"
    assert classify("reaching", "wechat")["party"] == "甲方"


def test_normalize_and_preview_party():
    got = normalize_party({
        "staff": [
            {"name": " 郑建雄 ", "aliases": ["小郑", "小郑", ""]},
            {"name": "", "aliases": ["x"]},
            {"name": "郑建雄", "aliases": ["阿郑"]},
            "只写名字",
        ],
        "reachingAliases": ["reaching", "reaching", ""],
        "titleStaffPattern": "申报.?组",
    })
    names = [r["name"] for r in got["staff"]]
    assert names == ["只写名字", "郑建雄"]
    assert got["staff"][1]["aliases"] == ["小郑", "阿郑"]
    assert got["reachingAliases"] == ["reaching"]
    data = {
        "staff": [{"name": "测试员", "aliases": ["小测"]}],
        "reachingAliases": ["boss"],
        "titleStaffPattern": "",
    }
    assert classify("小测", "wecom", data)["party"] == "乙方"
    assert classify("boss-微信", "wechat", data)["party"] == "甲方"
    assert classify("客户甲", "wecom", data)["party"] == "甲方"
    assert classify("申报一组-临时", "wecom", data)["party"] == "乙方"


def test_chatter_filter():
    assert is_chatter_text("收到")
    assert is_chatter_text("嗯")
    assert is_chatter_text("哦")
    assert is_chatter_text("好的。")
    assert is_chatter_text("谢谢")
    assert is_chatter_text("ok")
    assert not is_chatter_text("谢博士的材料预计啥时候能改好？")
    drop = fallback_filter({"text": "收到", "sender": "宝珠"})
    assert drop["useful"] is False
    keep = fallback_filter({"text": "请改护照号", "sender": "宝珠"})
    assert keep["useful"] is True
    file_keep = fallback_filter({"text": "请查收", "attachment_name": "申报书.docx", "has_attachment": True})
    assert file_keep["useful"] is True
    assert needs_gemini({"text": "请改护照号"}) is True
    assert needs_gemini({"text": "收到"}) is False
    assert needs_gemini({"text": "好的。"}) is False
    assert needs_gemini({"text": "", "attachment_name": "申报书.docx"}) is False


def test_parse_filter_json():
    raw = '{"items":[{"id":0,"useful":false,"reason":"纯确认"},{"id":1,"useful":true,"reason":"要改稿"}]}'
    got = parse_filter_json(raw, [0, 1])
    assert got[0]["useful"] is False
    assert got[1]["useful"] is True


def test_jev_score():
    assert score_of({"choice": "85"}) == 85
    assert score_of({"confidence": 0.9}) == 90
    assert score_of({"score": 75}) == 75
    got = answers_to_intent({
        "intent": {"choice": "request_revision", "confidence": 0.8},
        "score": {"choice": "85"},
    })
    assert got["intent"] == "request_revision"
    assert got["intentLabel"] == "要求修改申报书"
    assert got["score"] == 85


def test_queue_threshold():
    assert SCORE_MIN == 70
    assert hit_shown({"gemini_useful": True, "intent": "request_revision", "score": 75}) is True
    assert hit_shown({"gemini_useful": True, "intent": "request_revision", "score": 60}) is False
    assert hit_shown({"gemini_useful": True, "intent": "confirm", "score": 95}) is False
    assert hit_shown({"gemini_useful": False, "intent": "other", "score": 90}) is False
    assert hit_shown({"gemini_useful": True, "intent": "request_revision", "score": 88, "skip_jev": True, "party": "乙方"}) is False
    assert hit_shown({"gemini_useful": True, "intent": "request_revision", "score": 88, "party": "乙方"}) is False


def test_incremental_cursor():
    old = {"time_text": "2026-04-10 11:57", "message_id": "10"}
    assert _after_cursor({"time_text": "2026-04-10 11:57", "message_id": "10"}, "2026-04-10 11:57", "10") is False
    assert _after_cursor({"time_text": "2026-04-10 11:58", "message_id": "11"}, "2026-04-10 11:57", "10") is True
    assert _after_cursor({"time_text": "2026-04-10 11:57", "message_id": "9"}, "2026-04-10 11:57", "10") is False
    assert _after_cursor(old, "", "") is True


def test_rule_intent_form_field_and_ack():
    field = rule_intent({"text": "是否缴纳社保-否"})
    assert field and field["intent"] == "request_revision"
    assert field["candidates"][0]["intent"] == "request_revision"
    ack = rule_intent({"text": "然后OK"})
    assert ack and ack["intent"] == "confirm"
    ask = rule_intent({"text": "nello再确认一下，薪资是与申报书一致吗"})
    assert ask and ask["intent"] == "ask"
    send = rule_intent({"text": "申报书发给你了"})
    assert send and send["intent"] == "send_form"
    material = rule_intent({"text": "请补学位证扫描件"})
    assert material and material["intent"] == "supply_material"
    revise = rule_intent({"text": "请改护照号"})
    assert revise and revise["intent"] == "request_revision"
    assert rule_intent({"text": "这周匹配进度如何"}) is None


def test_ranked_intents_top5():
    ranked = ranked_intents({
        "send_form": {"choice": "85"},
        "request_revision": {"choice": "75"},
        "supply_material": {"choice": "60"},
        "confirm": {"choice": "0"},
        "chat": {"choice": "40"},
        "other": {"choice": "95"},
        "ask": {"choice": "70"},
    }, 5)
    codes = [x["intent"] for x in ranked]
    assert codes[0] == "other"
    assert "send_form" in codes
    assert "chat" not in codes
    assert "confirm" not in codes
    assert "supply_material" not in codes
    assert all(x["score"] >= 83 for x in ranked)
    assert all("intentLabel" in x and "score" in x for x in ranked)
    cands = candidates_of({"intent": "match", "intent_label": "匹配确认", "score": 80, "candidates": ranked})
    assert 1 <= len(cands) <= 3
    one = candidates_of({"intent": "request_revision", "intent_label": "要求修改申报书", "score": 88})
    assert len(one) == 1
    assert one[0]["score"] == 88
    only_ack = ranked_intents({
        "confirm": {"choice": "100"},
        "chat": {"choice": "99"},
        "supply_material": {"choice": "95"},
        "request_revision": {"choice": "84"},
        "send_form": {"choice": "74"},
    })
    assert [x["intent"] for x in only_ack] == ["confirm"]


def test_fallback_candidates():
    rows = fallback_candidates()
    codes = [r["intent"] for r in rows]
    assert "request_revision" in codes
    assert "supply_material" in codes
    dropped = candidates_of({"gemini_useful": False, "intent": "", "skip_jev": False})
    assert dropped == []
    filled = candidates_of({"gemini_useful": False, "intent": "", "skip_jev": False}) or fallback_candidates()
    assert len(filled) >= 4
    assert all("intentLabel" in x for x in filled)


def test_place_messages_yi_not_in_jev():
    msgs = [
        {"message_id": "1", "sender": "宝珠", "text": "请改护照号", "time_text": "2026-10-14 08:50"},
        {"message_id": "2", "sender": "郑建雄", "text": "好的，我改", "time_text": "2026-10-14 08:50"},
        {"message_id": "3", "sender": "宝珠", "text": "今晚前发我", "time_text": "2026-10-14 08:50"},
    ]
    got = place_messages(msgs, done=set(), seq_base=0, session={"username": "R_group", "display_name": "交付群"})
    assert got["party_stats"]["甲方"] == 2
    assert got["party_stats"]["乙方"] == 1
    assert len(got["pending"]) == 2
    assert [p[2] for p in got["pending"]] == [0, 2]
    assert len(got["pass_buf"]) == 1
    yi = got["pass_buf"][0]
    assert yi["skip_jev"] is True
    assert yi["party"] == "乙方"
    assert yi["seq"] == 1
    assert yi["gemini_useful"] is False
    assert not yi.get("intent")


def test_hits_keep_original_order():
    sid = "_test_ar_order"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": "2", "sender": "郑建雄", "time_text": "2026-10-14 08:50",
        "text": "好的", "party": "乙方", "skip_jev": True, "seq": 1,
    }])
    save_hits(sid, "*", [{
        "message_id": "1", "sender": "宝珠", "time_text": "2026-10-14 08:50",
        "text": "请改", "party": "甲方", "gemini_useful": True, "intent": "request_revision",
        "score": 80, "seq": 0,
    }])
    save_hits(sid, "*", [{
        "message_id": "3", "sender": "宝珠", "time_text": "2026-10-14 08:50",
        "text": "今晚", "party": "甲方", "gemini_useful": True, "intent": "request_revision",
        "score": 75, "seq": 2,
    }])
    listed = list_hits(sid, view="all", offset=0, limit=20)
    ids = [str(r["message_id"]) for r in listed["data"]]
    assert ids == ["1", "2", "3"], ids
    assert listed["data"][1]["skip_jev"] is True
    all_hits = list_hits(sid, view="all", offset=0, limit=0)
    assert [str(r["message_id"]) for r in all_hits["data"]] == ["1", "2", "3"]
    assert all_hits["total"] == 3
    assert all_hits["limit"] == 3
    dropped = list_hits(sid, view="dropped", offset=0, limit=20)
    assert all(not r.get("skip_jev") for r in dropped["data"])
    newest = list_hits(sid, view="all", offset=0, limit=2, tail=True)
    assert [str(r["message_id"]) for r in newest["data"]] == ["2", "3"]
    assert newest["hasEarlier"] is True
    older = list_hits(sid, view="all", offset=2, limit=2, tail=True)
    assert [str(r["message_id"]) for r in older["data"]] == ["1"]
    assert older["hasEarlier"] is False
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.commit()


def test_sort_hits_without_seq_uses_message_id():
    rows = [
        {"message_id": "20", "time_text": "2026-10-14 08:50", "seq": None},
        {"message_id": "11", "time_text": "2026-10-14 08:50", "seq": None},
    ]
    sort_hits(rows)
    assert [r["message_id"] for r in rows] == ["11", "20"]


def test_resume_skips_done_jiafang():
    msgs = [
        {"message_id": "1", "sender": "宝珠", "text": "请改护照号", "time_text": "2026-10-14 08:50"},
        {"message_id": "2", "sender": "郑建雄", "text": "好的", "time_text": "2026-10-14 08:50"},
        {"message_id": "3", "sender": "宝珠", "text": "今晚前发我", "time_text": "2026-10-14 08:51"},
    ]
    got = place_messages(msgs, done={"1"}, seq_base=0, session={"username": "R_group", "display_name": "交付群"})
    assert [p[0]["message_id"] for p in got["pending"]] == ["3"]
    assert got["seq_updates"] == [("1", 0)]
    assert got["pass_buf"][0]["message_id"] == "2"


def test_incomplete_checkpoint():
    sid = "_test_ar_resume"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM pipeline_runs WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": "1", "sender": "宝珠", "time_text": "2026-10-14 08:50",
        "text": "请改", "party": "甲方", "gemini_useful": True, "seq": 0,
    }])
    run = get_run(sid)
    assert run.get("full_done") in (0, False, None)
    assert run.get("incomplete") is True
    save_run(sid, name="t", last_time="2026-10-14 08:50", last_message_id="1", msg_count=3, full_done=False, progress_done=1, progress_total=3)
    run = get_run(sid)
    assert run["full_done"] is False
    assert run["incomplete"] is True
    assert run["progress_done"] == 1
    assert run["progress_total"] == 3
    runs = list_runs()
    assert runs[sid]["incomplete"] is True
    save_run(sid, name="t", last_time="2026-10-14 08:51", last_message_id="3", msg_count=3, full_done=True, progress_done=3, progress_total=3)
    run = get_run(sid)
    assert run["full_done"] is True
    assert run["incomplete"] is False
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM pipeline_runs WHERE session_id=?", (sid,))
        con.commit()


def test_review_learns_dropped_keep():
    sid = "_test_ar_dropped_keep"
    mid = "9001"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": mid,
        "sender": "宝珠",
        "time_text": "2026-10-14 08:50",
        "text": "请改护照号",
        "party": "甲方",
        "gemini_useful": False,
        "gemini_reason": "短回复/确认",
        "intent": "",
        "score": 0,
    }])
    row = upsert_review({
        "session_id": sid,
        "message_id": mid,
        "gemini_useful": False,
        "selected_intent": "request_revision",
        "selected_label": "要求修改申报书",
        "reviewer": "测试",
    })
    assert row["selected_intent"] == "request_revision"
    assert row["filter_ok"] is False
    assert row["correct_useful"] is True
    assert row["process_ok"] is True
    examples = load_examples("filter", 20)
    assert any(e.get("label") == "keep" and "护照" in str(e.get("text") or "") for e in examples)
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()


def test_review_multi_intents():
    sid = "_test_ar_multi_intent"
    mid = "9002"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": mid,
        "sender": "宝珠",
        "time_text": "2026-10-14 09:10",
        "text": "请改护照号并补学位证",
        "party": "甲方",
        "gemini_useful": True,
        "intent": "request_revision",
        "intent_label": "要求修改申报书",
        "score": 88,
    }])
    row = upsert_review({
        "session_id": sid,
        "message_id": mid,
        "gemini_useful": True,
        "selected_intents": [
            {"intent": "request_revision", "label": "要求修改申报书", "score": 88},
            {"intent": "supply_material", "label": "要求补材料", "score": 80},
        ],
        "reviewer": "测试",
    })
    codes = [x.get("intent") for x in (row.get("selected_intents") or [])]
    assert codes == ["request_revision", "supply_material"]
    assert "要求修改申报书" in (row.get("selected_label") or "")
    assert "要求补材料" in (row.get("selected_label") or "")
    assert row["selected_score"] == 88
    examples = load_examples("intent", 20)
    labels = {e.get("label") for e in examples if e.get("session_id") == sid}
    assert "request_revision" in labels
    assert "supply_material" in labels
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()


def test_review_other_requires_note():
    sid = "_test_ar_other_note"
    mid = "9003"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": mid,
        "sender": "宝珠",
        "time_text": "2026-10-14 09:20",
        "text": "这个进度怎么样了",
        "party": "甲方",
        "gemini_useful": True,
        "intent": "other",
        "intent_label": "其他",
        "score": 80,
    }])
    try:
        upsert_review({
            "session_id": sid,
            "message_id": mid,
            "gemini_useful": True,
            "selected_intents": [{"intent": "other", "label": "其他"}],
            "reviewer": "测试",
        })
        raise AssertionError("other without note should fail")
    except ValueError as e:
        assert "其他" in str(e)
    row = upsert_review({
        "session_id": sid,
        "message_id": mid,
        "gemini_useful": True,
        "selected_intents": [{"intent": "other", "label": "其他", "note": "询问进度"}],
        "reviewer": "测试",
    })
    items = row.get("selected_intents") or []
    assert items and items[0].get("note") == "询问进度"
    assert "询问进度" in (row.get("selected_label") or "")
    assert row.get("intent_note") == "询问进度"
    examples = load_examples("intent", 20)
    blob = " ".join(str(e.get("text") or "") for e in examples if e.get("session_id") == sid)
    assert "询问进度" in blob
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()


def test_rank_questions_includes_examples():
    qs = rank_questions([
        {"intent": "request_revision", "text": "请改护照号"},
        {"label": "send_form", "filename": "申报书.docx", "text": "发给你了"},
    ])
    assert set(qs) == {"intent", "score"}
    criteria = qs["intent"]["criteria"]
    assert "Leader examples" in criteria["request_revision"]
    assert "请改护照号" in criteria["request_revision"]
    assert "申报书.docx" in criteria["send_form"]
    assert "请改护照号" not in criteria["chat"]
    blank = rank_questions()
    assert "Leader examples" not in blank["intent"]["criteria"]["request_revision"]
    one = ranked_intents({"intent": {"choice": "ask"}, "score": {"choice": "85"}})
    assert [x["intent"] for x in one] == ["ask"]
    assert one[0]["score"] == 85


def test_examples_pin_disable_rebuild_for_jev():
    import tempfile
    from pathlib import Path
    import app.agent_review as ar

    sid = "admin-ar-learn-test"
    old = ar.DB_PATH
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    ar.DB_PATH = Path(path)
    try:
        ar.init_db()
        add_example({"kind": "intent", "text": "请改护照号", "label": "request_revision", "session_id": sid, "message_id": "1", "reviewer": "测"})
        add_example({"kind": "intent", "text": "申报书发你了", "label": "send_form", "session_id": sid, "message_id": "2"})
        add_example({"kind": "intent", "text": "请补学位证", "label": "supply_material", "session_id": sid, "message_id": "3"})
        add_example({"kind": "intent", "text": "今晚吃饭吗", "label": "chat", "session_id": sid, "message_id": "4"})
        add_example({"kind": "intent", "text": "请改护照号和身份证", "label": "request_revision", "session_id": sid, "message_id": "1"})
        rows = [r for r in list_examples("intent") if r.get("session_id") == sid]
        assert len(rows) == 4
        one = [r for r in rows if r["message_id"] == "1"][0]
        assert "身份证" in (one.get("text") or "")
        chat = [r for r in rows if r["label"] == "chat"][0]
        set_example(chat["id"], enabled=False)
        loaded = load_examples("intent", 16)
        assert all(r.get("label") != "chat" for r in loaded)
        send = [r for r in rows if r["label"] == "send_form"][0]
        set_example(send["id"], pinned=True)
        loaded = load_examples("intent", 3)
        assert loaded and loaded[0]["id"] == send["id"]
        labels = {r.get("label") for r in loaded}
        assert "send_form" in labels
        st = example_stats()
        assert st["intent"] == 4
        assert st["intentOn"] == 3
        assert st["pinned"] == 1
        assert st["jevLimit"] == 16
        save_hits(sid, "*", [{
            "message_id": "1",
            "sender": "宝珠",
            "time_text": "2026-10-14 09:00",
            "text": "请改护照号和身份证",
            "party": "甲方",
            "gemini_useful": True,
            "intent": "request_revision",
            "intent_label": "要求修改申报书",
            "score": 88,
        }])
        upsert_review({
            "session_id": sid,
            "message_id": "1",
            "selected_intent": "request_revision",
            "selected_label": "要求修改申报书",
            "selected_score": 90,
            "reviewer": "测",
        })
        rebuilt = rebuild_examples_from_reviews()
        assert rebuilt["reviewsUsed"] == 1
        after = [r for r in list_examples("intent") if r.get("session_id") == sid]
        pinned = [r for r in after if r.get("message_id") == "2"]
        assert pinned and pinned[0].get("pinned") is True
        kept = [r for r in after if r.get("message_id") == "1"]
        assert kept and "护照" in str(kept[0].get("text") or "")
        qs = rank_questions(load_examples("intent", 16))
        assert "请改护照号" in qs["intent"]["criteria"]["request_revision"]
        delete_example(chat["id"])
        assert all(r.get("id") != chat["id"] for r in list_examples("intent"))
    finally:
        ar.DB_PATH = old
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    test_party_rules()
    test_normalize_and_preview_party()
    test_chatter_filter()
    test_parse_filter_json()
    test_jev_score()
    test_queue_threshold()
    test_incremental_cursor()
    test_rule_intent_form_field_and_ack()
    test_ranked_intents_top5()
    test_fallback_candidates()
    test_place_messages_yi_not_in_jev()
    test_hits_keep_original_order()
    test_sort_hits_without_seq_uses_message_id()
    test_resume_skips_done_jiafang()
    test_incomplete_checkpoint()
    test_review_learns_dropped_keep()
    test_review_multi_intents()
    test_review_other_requires_note()
    test_rank_questions_includes_examples()
    test_examples_pin_disable_rebuild_for_jev()
    print("ok")
