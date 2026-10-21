# -*- coding: utf-8 -*-
"""Jev 评分、组长审核字段。不调网络。"""
import json
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
    drop_match_reason,
    drop_text_keys,
    example_stats,
    fallback_candidates,
    filtered_message_ids,
    get_hit,
    get_run,
    hit_shown,
    hit_reviewed,
    list_examples,
    list_hits,
    list_known_intents,
    list_runs,
    load_examples,
    rebuild_examples_from_reviews,
    save_hits,
    save_run,
    set_example,
    sort_hits,
    upsert_review,
)
from app.party import classify, clean_sender, normalize_party, party_of
from app.wecom_intent import answers_to_intent, rank_questions, ranked_intents, rule_intent, rule_intent_for_score, score_of


def test_party_rules():
    assert party_of("Reaching") == "甲方"
    assert party_of("郑建雄") == "乙方"
    assert party_of("宝珠") == "甲方"
    assert classify("张三", "wechat")["party"] == "乙方"
    assert classify("reaching", "wechat")["party"] == "甲方"
    assert clean_sender("null") == ""
    assert clean_sender(None) == ""
    assert clean_sender("刘晶") == "刘晶"
    assert classify("null", "wecom")["party"] == "甲方"


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
    named = answers_to_intent({
        "intent": {"choice": "形式审核缺项"},
        "score": {"choice": "80"},
    })
    assert named["intent"] == "形式审核缺项"
    assert named["intentLabel"] == "形式审核缺项"


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


def test_rule_intent_for_score_leaves_business_to_jev():
    assert rule_intent_for_score({"text": "请补学位证扫描件"}) is None
    assert rule_intent_for_score({"text": "请改护照号"}) is None
    assert rule_intent_for_score({"text": "是否缴纳社保-否"}) is None
    assert rule_intent_for_score({"text": "nello再确认一下，薪资是与申报书一致吗"}) is None
    assert rule_intent_for_score({"text": "这周匹配进度如何"}) is None
    assert rule_intent_for_score({"text": "形式审核部分不填"}) is None
    ack = rule_intent_for_score({"text": "然后OK"})
    assert ack and ack["intent"] == "confirm"
    send = rule_intent_for_score({"text": "申报书发给你了"})
    assert send and send["intent"] == "send_form"


def test_rank_questions_has_specific_labels():
    blank = rank_questions()
    criteria = blank["intent"]["criteria"]
    assert "supply_material" in criteria
    assert "形式审核缺项" in criteria
    assert "询问进度" in criteria
    assert "核对薪资" in criteria
    assert "要求补学历" in criteria
    assert list(criteria)[-1] == "other"
    ins = blank["intent"]["instructions"]
    assert "most specific" in ins
    extra = rank_questions(extra_intents=[{"id": "催办审批", "name": "催办审批"}])
    assert "催办审批" in extra["intent"]["criteria"]


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
    by_prob = ranked_intents({
        "intent": {
            "choice": "request_revision",
            "probabilities": {
                "request_revision": 0.72,
                "ask": 0.18,
                "chat": 0.02,
                "send_form": 0.05,
            },
        },
        "score": {"choice": "85"},
    }, 5)
    assert [x["intent"] for x in by_prob][0] == "request_revision"
    assert by_prob[0]["score"] == 85
    assert "ask" in [x["intent"] for x in by_prob]
    assert "chat" not in [x["intent"] for x in by_prob]
    only_ack = ranked_intents({
        "confirm": {"choice": "100"},
        "chat": {"choice": "99"},
        "supply_material": {"choice": "95"},
        "request_revision": {"choice": "84"},
        "send_form": {"choice": "74"},
    })
    assert [x["intent"] for x in only_ack] == ["confirm"]


def test_party_caps_and_unsafe_pattern():
    got = normalize_party({
        "staff": [{"name": "人%d" % i} for i in range(600)],
        "reachingAliases": ["a%d" % i for i in range(120)],
        "titleStaffPattern": "(a+)+",
    })
    assert len(got["staff"]) == 500
    assert len(got["reachingAliases"]) == 80
    assert got["titleStaffPattern"] == ""
    ok = normalize_party({"staff": [], "reachingAliases": ["reaching"], "titleStaffPattern": "申报.?组"})
    assert ok["titleStaffPattern"] == "申报.?组"
    row = classify("aaaaaaaaaaaaaaaa", "wecom", {
        "staff": [],
        "reachingAliases": ["reaching"],
        "titleStaffPattern": "(a+)+$",
    })
    assert row["party"] in ("甲方", "乙方")


def test_fallback_candidates():
    rows = fallback_candidates()
    assert rows == []
    dropped = candidates_of({"gemini_useful": False, "intent": "", "skip_jev": False})
    assert dropped == []
    one = candidates_of({"intent": "形式审核缺项", "intent_label": "形式审核缺项", "score": 80})
    assert len(one) == 1
    assert one[0]["intent"] == "形式审核缺项"


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


def test_review_accepts_custom_intent():
    sid = "_test_ar_custom_intent"
    mid = "custom1"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": mid,
        "sender": "宝珠",
        "time_text": "2026-10-16 09:00",
        "text": "形式审核部分不填",
        "party": "甲方",
        "gemini_useful": True,
        "intent": "request_revision",
        "score": 20,
    }])
    row = upsert_review({
        "session_id": sid,
        "message_id": mid,
        "selected_intents": [{"intent": "形式审核缺项", "label": "形式审核缺项"}],
        "reviewer": "测试",
    })
    codes = [x.get("intent") for x in (row.get("selected_intents") or [])]
    assert codes == ["形式审核缺项"]
    names = {x["id"]: x["name"] for x in list_known_intents()}
    assert names.get("形式审核缺项") == "形式审核缺项"
    qs = rank_questions(load_examples("intent", 16))
    assert "形式审核缺项" in qs["intent"]["criteria"]
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()


def test_fallback_not_written_to_db():
    sid = "_test_ar_fb_persist"
    mid = "fb1"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()
    dumped = fallback_candidates()
    assert dumped == []
    save_hits(sid, "*", [{
        "message_id": mid,
        "sender": "宝珠",
        "time_text": "2026-10-16 09:10",
        "text": "这句话没有意图",
        "party": "甲方",
        "gemini_useful": False,
        "intent": "",
        "score": 0,
        "candidates": [{"intent": "request_revision", "intentLabel": "要求修改申报书", "fallback": True}],
    }])
    with connect() as con:
        raw = con.execute(
            "SELECT candidates FROM pipeline_hits WHERE session_id=? AND message_id=?",
            (sid, mid),
        ).fetchone()["candidates"]
    stored = json.loads(raw or "[]")
    assert stored == []
    hit = get_hit(sid, mid)
    assert not any(c.get("fallback") for c in (hit.get("candidates") or []))
    upsert_review({
        "session_id": sid,
        "message_id": mid,
        "should_filter": True,
        "text": "这句话没有意图",
        "reviewer": "测试",
    })
    with connect() as con:
        raw2 = con.execute(
            "SELECT candidates FROM pipeline_hits WHERE session_id=? AND message_id=?",
            (sid, mid),
        ).fetchone()["candidates"]
    stored2 = json.loads(raw2 or "[]")
    assert len(stored2) <= 1
    assert not any(isinstance(c, dict) and c.get("fallback") for c in stored2)
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


def test_review_should_filter():
    sid = "_test_ar_should_filter"
    mid = "9101"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": mid,
        "sender": "宝珠",
        "time_text": "2026-10-14 10:00",
        "text": "这个不用处理，群公告而已",
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
        "should_filter": True,
        "text": "这个不用处理，群公告而已",
        "reviewer": "测试",
    })
    assert row["should_filter"] is True
    assert row["correct_useful"] is False
    assert row["process_ok"] is False
    assert row["selected_label"] == "应过滤"
    hit = get_hit(sid, mid)
    assert hit["shown"] is False
    assert hit["reviewed"] is True
    assert hit["gemini_useful"] is False
    assert hit.get("intent_label") == "应过滤"
    assert mid in filtered_message_ids(sid)
    keys = drop_text_keys()
    assert "这个不用处理，群公告而已" in keys
    examples = load_examples("filter", 40)
    assert any(e.get("label") == "drop" and "群公告" in str(e.get("text") or "") for e in examples)
    assert drop_match_reason(
        message_id="9102",
        text="这个不用处理，群公告而已",
        filtered_ids=filtered_message_ids(sid),
        drop_keys=keys,
    ) == "命中应过滤样本"
    assert drop_match_reason(
        message_id=mid,
        text="别的内容",
        filtered_ids=filtered_message_ids(sid),
        drop_keys=set(),
    ) == "组长标记应过滤"
    got = place_messages(
        [
            {"message_id": mid, "sender": "宝珠", "text": "这个不用处理，群公告而已", "time_text": "2026-10-14 10:00"},
            {"message_id": "9102", "sender": "宝珠", "text": "这个不用处理，群公告而已", "time_text": "2026-10-14 10:01"},
            {"message_id": "9103", "sender": "宝珠", "text": "请改护照号", "time_text": "2026-10-14 10:02"},
        ],
        done=set(),
        session={"username": "R_group", "display_name": "群"},
        filtered_ids=filtered_message_ids(sid),
        drop_keys=drop_text_keys(),
    )
    assert len(got["drop_buf"]) == 2
    assert len(got["pending"]) == 1
    assert str(got["pending"][0][0]["message_id"]) == "9103"
    row2 = upsert_review({
        "session_id": sid,
        "message_id": mid,
        "gemini_useful": True,
        "should_filter": False,
        "selected_intent": "request_revision",
        "selected_label": "要求修改申报书",
        "reviewer": "测试",
    })
    assert row2["should_filter"] is False
    hit2 = get_hit(sid, mid)
    assert hit2["shown"] is True
    assert hit2["reviewed"] is True
    assert hit2["gemini_useful"] is True
    assert mid not in filtered_message_ids(sid)
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()


def test_reviewed_hit_leaves_queue():
    sid = "_test_ar_reviewed_mark"
    mid = "9201"
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()
    save_hits(sid, "*", [{
        "message_id": mid,
        "sender": "宝珠",
        "time_text": "2026-10-15 10:00",
        "text": "请改护照号",
        "party": "甲方",
        "gemini_useful": True,
        "intent": "request_revision",
        "intent_label": "要求修改申报书",
        "score": 88,
    }])
    before = list_hits(sid, view="queue")
    assert before["stats"]["queue"] == 1
    assert before["data"] and str(before["data"][0]["message_id"]) == mid
    assert before["data"][0]["reviewed"] is False
    upsert_review({
        "session_id": sid,
        "message_id": mid,
        "gemini_useful": True,
        "selected_intent": "request_revision",
        "selected_label": "要求修改申报书",
        "reviewer": "测试",
    })
    hit = get_hit(sid, mid)
    assert hit["reviewed"] is True
    assert hit_reviewed(hit.get("review")) is True
    queued = list_hits(sid, view="queue")
    assert queued["stats"]["queue"] == 0
    assert queued["data"] == []
    listed = list_hits(sid, view="all")
    assert listed["stats"]["queue"] == 0
    assert listed["stats"]["reviewed"] == 1
    assert listed["data"][0]["reviewed"] is True
    runs = list_runs()
    assert int((runs.get(sid) or {}).get("queue") or 0) == 0
    assert int((runs.get(sid) or {}).get("reviewed") or 0) == 1
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (sid,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (sid,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (sid,))
        con.commit()


def test_rank_questions_includes_examples():
    qs = rank_questions([
        {"intent": "request_revision", "text": "请改护照号"},
        {"label": "send_form", "filename": "申报书.docx", "text": "发给你了"},
        {"label": "形式审核缺项", "text": "形式审核部分不填"},
    ])
    assert set(qs) == {"intent", "score"}
    criteria = qs["intent"]["criteria"]
    assert "Leader examples" in criteria["request_revision"]
    assert "请改护照号" in criteria["request_revision"]
    assert "申报书.docx" in criteria["send_form"]
    assert "请改护照号" not in criteria["chat"]
    assert "形式审核缺项" in criteria
    assert "形式审核部分不填" in criteria["形式审核缺项"]
    blank = rank_questions()
    assert "Leader examples" not in blank["intent"]["criteria"]["request_revision"]
    assert "形式审核缺项" in blank["intent"]["criteria"]
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
        edited = set_example(send["id"], text="申报书已发到群里，请查收", label="send_form", score=92)
        assert edited["text"].startswith("申报书已发")
        assert edited["pinned"] is True
        assert edited["score"] == 92
        assert edited["intentLabel"] == "发送申报书"
        named = set_example(send["id"], label="形式审核缺项")
        assert named["label"] == "形式审核缺项"
        try:
            set_example(send["id"], label="  ")
            raise AssertionError("empty intent should fail")
        except ValueError:
            pass
        delete_example(chat["id"])
        assert all(r.get("id") != chat["id"] for r in list_examples("intent"))
    finally:
        ar.DB_PATH = old
        try:
            os.remove(path)
        except OSError:
            pass


def test_reviewer_permission():
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    from app import auth
    from app.routes import agent_review as ar_routes

    def fake(token: str):
        mapping = {
            "t-user": {"id": 2, "role": "user", "username": "bob", "status": "active", "realName": "bob"},
            "t-leader": {"id": 3, "role": "leader", "username": "ld", "status": "active", "realName": "ld"},
            "t-admin": {"id": 1, "role": "admin", "username": "admin", "status": "active", "realName": "ad"},
        }
        return mapping.get(token)

    with patch.object(auth, "user_by_token", side_effect=fake):
        c = TestClient(app, raise_server_exceptions=False)
        user = {"Authorization": "Bearer t-user"}
        leader = {"Authorization": "Bearer t-leader"}
        r = c.put("/api/agent-review/party", json={"staff": []}, headers=user)
        assert r.status_code == 403, r.text
        r = c.post("/api/agent-review/analyze", json={"session_id": "R:1"}, headers=user)
        assert r.status_code == 403, r.text
        r = c.post("/api/agent-review/reviews", json={
            "session_id": "x", "message_id": "1", "should_filter": True
        }, headers=user)
        assert r.status_code == 403, r.text
        r = c.get("/api/agent-review/party", headers=user)
        assert r.status_code == 200, r.text
        r = c.post("/api/agent-review/party/preview", json={
            "sender": "aaaaaaaaaaaaaaaa",
            "platform": "wecom",
            "party": {"titleStaffPattern": "(a+)+$", "staff": [], "reachingAliases": ["reaching"]},
        }, headers=user)
        assert r.status_code == 200, r.text
        r = c.post("/api/agent-review/analyze", json={}, headers=leader)
        assert r.status_code == 400, r.text
        ar_routes._IN_FLIGHT.add("R:busy")
        try:
            r = c.post("/api/agent-review/analyze", json={"session_id": "R:busy"}, headers=leader)
            assert r.status_code == 409, r.text
        finally:
            ar_routes._IN_FLIGHT.discard("R:busy")


if __name__ == "__main__":
    test_party_rules()
    test_normalize_and_preview_party()
    test_party_caps_and_unsafe_pattern()
    test_chatter_filter()
    test_parse_filter_json()
    test_jev_score()
    test_queue_threshold()
    test_incremental_cursor()
    test_rule_intent_form_field_and_ack()
    test_rule_intent_for_score_leaves_business_to_jev()
    test_rank_questions_has_specific_labels()
    test_ranked_intents_top5()
    test_fallback_candidates()
    test_place_messages_yi_not_in_jev()
    test_hits_keep_original_order()
    test_sort_hits_without_seq_uses_message_id()
    test_resume_skips_done_jiafang()
    test_incomplete_checkpoint()
    test_review_learns_dropped_keep()
    test_review_multi_intents()
    test_review_accepts_custom_intent()
    test_fallback_not_written_to_db()
    test_review_other_requires_note()
    test_review_should_filter()
    test_reviewed_hit_leaves_queue()
    test_rank_questions_includes_examples()
    test_examples_pin_disable_rebuild_for_jev()
    test_reviewer_permission()
    print("ok")
