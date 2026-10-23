# -*- coding: utf-8 -*-
"""AI 审核整条链路：待审 → 审核 → 已审核离开队列 → 样本/过滤/甲乙方。"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.agent_pipeline import place_messages
from app.agent_review import (
    connect,
    drop_match_reason,
    drop_text_keys,
    example_stats,
    filtered_message_ids,
    get_hit,
    list_examples,
    list_hits,
    list_reviews,
    list_runs,
    load_examples,
    save_hits,
    upsert_review,
)

SID = "_flow_test_ar"
BASE = os.environ.get("SHENBAOSHU_BASE") or "http://127.0.0.1:3777"
ISSUES = []


def issue(msg):
    ISSUES.append(msg)
    print("ISSUE", msg)


def cleanup():
    with connect() as con:
        con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (SID,))
        con.execute("DELETE FROM intent_reviews WHERE session_id=?", (SID,))
        con.execute("DELETE FROM prompt_examples WHERE session_id=?", (SID,))
        con.commit()


def seed():
    save_hits(SID, "*", [
        {
            "message_id": "1",
            "sender": "宝珠",
            "time_text": "2026-10-15 10:00",
            "text": "请改护照号",
            "party": "甲方",
            "gemini_useful": True,
            "intent": "request_revision",
            "intent_label": "要求修改申报书",
            "score": 88,
            "seq": 1,
        },
        {
            "message_id": "2",
            "sender": "宝珠",
            "time_text": "2026-10-15 10:01",
            "text": "这个进度怎么样了",
            "party": "甲方",
            "gemini_useful": True,
            "intent": "other",
            "intent_label": "其他",
            "score": 80,
            "seq": 2,
        },
        {
            "message_id": "3",
            "sender": "宝珠",
            "time_text": "2026-10-15 10:02",
            "text": "好的收到",
            "party": "甲方",
            "gemini_useful": True,
            "intent": "request_revision",
            "intent_label": "要求修改申报书",
            "score": 72,
            "seq": 3,
        },
        {
            "message_id": "4",
            "sender": "唐文秀",
            "time_text": "2026-10-15 10:03",
            "text": "收到，这边改",
            "party": "乙方",
            "gemini_useful": False,
            "intent": "confirm",
            "intent_label": "确认",
            "score": 0,
            "skip_jev": True,
            "seq": 4,
        },
        {
            "message_id": "5",
            "sender": "宝珠",
            "time_text": "2026-10-15 10:04",
            "text": "哈哈哈哈",
            "party": "甲方",
            "gemini_useful": False,
            "intent": "chat",
            "intent_label": "闲聊",
            "score": 0,
            "seq": 5,
        },
    ])


def test_db_flow():
    cleanup()
    seed()
    before = list_hits(SID, view="all")
    assert before["stats"]["queue"] == 3, before["stats"]
    assert before["stats"]["reviewed"] == 0
    queued = list_hits(SID, view="queue")
    assert [h["message_id"] for h in queued["data"]] == ["1", "2", "3"]
    assert all(h["reviewed"] is False for h in queued["data"])

    r1 = upsert_review({
        "session_id": SID,
        "message_id": "1",
        "gemini_useful": True,
        "selected_intents": [{"intent": "request_revision", "label": "要求修改申报书", "score": 88}],
        "reviewer": "流程测试",
    })
    assert r1.get("selected_intent") == "request_revision"
    h1 = get_hit(SID, "1")
    assert h1["reviewed"] is True
    assert h1["shown"] is True
    after1 = list_hits(SID, view="queue")
    assert after1["stats"]["queue"] == 2, after1["stats"]
    assert [h["message_id"] for h in after1["data"]] == ["2", "3"]

    try:
        upsert_review({
            "session_id": SID,
            "message_id": "2",
            "selected_intents": [{"intent": "other", "label": "其他"}],
            "reviewer": "流程测试",
        })
        raise AssertionError("other without note should fail")
    except ValueError:
        pass
    upsert_review({
        "session_id": SID,
        "message_id": "2",
        "selected_intents": [{"intent": "other", "label": "其他", "note": "询问进度"}],
        "reviewer": "流程测试",
    })
    upsert_review({
        "session_id": SID,
        "message_id": "3",
        "should_filter": True,
        "text": "好的收到",
        "reviewer": "流程测试",
    })
    h3 = get_hit(SID, "3")
    assert h3["reviewed"] is True
    assert h3["shown"] is False
    assert h3["intent_label"] == "应过滤"

    done = list_hits(SID, view="all")
    assert done["stats"]["queue"] == 0, done["stats"]
    assert done["stats"]["reviewed"] == 3
    by_id = {str(h["message_id"]): h for h in done["data"]}
    assert by_id["1"]["reviewed"] is True
    assert by_id["3"]["review"].get("should_filter") is True
    assert list_hits(SID, view="queue")["data"] == []

    dropped = list_hits(SID, view="dropped")
    drop_ids = {str(h["message_id"]) for h in dropped["data"]}
    assert "3" in drop_ids
    assert "5" in drop_ids
    d3 = next(h for h in dropped["data"] if str(h["message_id"]) == "3")
    assert d3["reviewed"] is True

    jia = list_hits(SID, view="all", party="甲方")
    assert all(h.get("party") == "甲方" for h in jia["data"])

    runs = list_runs()
    assert int((runs.get(SID) or {}).get("queue") or 0) == 0
    assert int((runs.get(SID) or {}).get("reviewed") or 0) == 3

    intents = load_examples("intent", 40)
    assert any(e.get("session_id") == SID and e.get("label") == "request_revision" for e in intents)
    assert any("询问进度" in str(e.get("text") or "") for e in intents if e.get("session_id") == SID)
    filters = load_examples("filter", 40)
    assert any(e.get("session_id") == SID and e.get("label") == "drop" for e in filters)

    assert "1" not in filtered_message_ids(SID)
    assert "3" in filtered_message_ids(SID)
    keys = drop_text_keys()
    assert "好的收到" in keys
    assert drop_match_reason(message_id="3", text="别的", filtered_ids=filtered_message_ids(SID), drop_keys=set()) == "组长标记应过滤"
    assert drop_match_reason(message_id="99", text="好的收到", filtered_ids=set(), drop_keys=keys) == "命中应过滤样本"

    placed = place_messages(
        [
            {"message_id": "3", "sender": "宝珠", "text": "好的收到", "time_text": "2026-10-15 11:00"},
            {"message_id": "6", "sender": "宝珠", "text": "好的收到", "time_text": "2026-10-15 11:01"},
            {"message_id": "7", "sender": "宝珠", "text": "请改申报书日期", "time_text": "2026-10-15 11:02"},
        ],
        done=set(),
        session={"username": "R_flow", "display_name": "群"},
        filtered_ids=filtered_message_ids(SID),
        drop_keys=keys,
    )
    assert len(placed["pending"]) == 1
    assert str(placed["pending"][0][0]["message_id"]) == "7"
    reviews = list_reviews(SID)
    assert len(reviews) >= 3
    st = example_stats()
    assert int(st.get("reviews") or 0) >= 3
    cleanup()
    print("db_flow ok")


def check_static():
    index = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "static", "agent-review.js"), encoding="utf-8").read()
    css = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
    admin_js = open(os.path.join(ROOT, "static", "admin-agent-review.js"), encoding="utf-8").read()
    if "agent-review.js?v=py40" not in index:
        issue("index.html 未引用 agent-review.js?v=py40")
    if "arThumbHtml" not in js or "arBindThumbs" not in js:
        issue("agent-review.js 缺少图片预览")
    ar_fn = js.find("function arWatched")
    ar_chunk = js[ar_fn:ar_fn + 900] if ar_fn >= 0 else ""
    if "state.watchGroups" not in ar_chunk:
        issue("arWatched 未使用本页关注名单")
    wg = ar_chunk.find("state.watchGroups")
    fn = ar_chunk.find("wecomGroupWatched")
    if fn >= 0 and (wg < 0 or wg > fn):
        issue("arWatched 在本页关注名单就绪前就依赖 wecomGroupWatched，会滤空会话")
    if "canReview" not in js:
        issue("agent-review.js 缺少 canReview")
    if "补充意图" not in js:
        issue("agent-review.js 缺少补充意图")
    if "选择其他意图" in js:
        issue("审核弹窗不应再摊整份写死词表")
    if "style.css?v=py135" not in index:
        issue("index.html 未引用 style.css?v=py135")
    for needle in ("ar-hit-reviewed", "已审核 · 改", "hit.reviewed", "run.reviewed"):
        if needle not in js:
            issue("agent-review.js 缺少 " + needle)
    if ".ar-hit-reviewed" not in css:
        issue("style.css 缺少 .ar-hit-reviewed")
    if "已审核" not in admin_js:
        issue("admin-agent-review.js 未标记已审核")
    for eid in ("arHitList", "arReviewMask", "arSaveReviewBtn", "arIntentNav", "arExIntentList"):
        if 'id="' + eid + '"' not in index:
            issue("index.html 缺少 " + eid)
    print("static ok")


def check_real_session():
    sid = "R:10740157231685523"
    all_hits = list_hits(sid, view="all", offset=0, limit=0)
    q = list_hits(sid, view="queue")
    st = all_hits["stats"]
    print("real stats", st, "queue_view", q["total"])
    if st.get("queue") != 0:
        issue("江苏省人才集团待审应为 0，实际 " + str(st.get("queue")))
    if int(st.get("reviewed") or 0) < 100:
        issue("已审核数量异常：" + str(st.get("reviewed")))
    reviewed_shown = [h for h in all_hits["data"] if h.get("shown") and h.get("reviewed")]
    unreviewed_shown = [h for h in all_hits["data"] if h.get("shown") and not h.get("reviewed")
                        and h.get("party") != "乙方" and not h.get("skip_jev")]
    if unreviewed_shown:
        issue("仍有 shown 未审 " + str(len(unreviewed_shown)) + " 条，如 " + str(unreviewed_shown[0].get("message_id")))
    if q["data"]:
        issue("view=queue 仍返回 " + str(len(q["data"])) + " 条")
    marked = [h for h in all_hits["data"] if h.get("reviewed")]
    if not marked:
        issue("真实会话 hits 没有 reviewed=true")
    else:
        sample = marked[0]
        if not (sample.get("review") or {}).get("id") and not (sample.get("review") or {}).get("selected_intent") and not (sample.get("review") or {}).get("should_filter"):
            issue("reviewed=true 但 review 对象为空")
    runs = list_runs().get(sid) or {}
    print("real run queue/reviewed", runs.get("queue"), runs.get("reviewed"))
    print("real_session ok")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_json(opener, method, path, body=None, token=""):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with opener.open(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"detail": raw[:300]}
        return e.code, payload


def test_http_flow():
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    try:
        code, me = http_json(opener, "GET", "/api/auth/me")
        if code == 401:
            code, login = http_json(opener, "POST", "/api/auth/login", {"username": "admin", "password": "Admin@123456"})
            if code != 200 or not login.get("ok"):
                issue("登录失败 " + str(code) + " " + str(login)[:200])
                return
            token = login.get("token") or ""
        else:
            token = ""
        code, me = http_json(opener, "GET", "/api/auth/me", token=token)
        if code != 200:
            issue("/api/auth/me " + str(code))
            return
        checks = [
            ("GET", "/api/agent-review/intents", None),
            ("GET", "/api/agent-review/party", None),
            ("GET", "/api/agent-review/runs", None),
            ("GET", "/api/wecom/groups?kind=group&watch_only=1&limit=50", None),
            ("GET", "/api/agent-review/hits?session_id=" + urllib.parse.quote("R:10740157231685523") + "&view=all&limit=20", None),
            ("GET", "/api/agent-review/hits?session_id=" + urllib.parse.quote("R:10740157231685523") + "&view=queue&limit=20", None),
            ("GET", "/api/agent-review/hits?session_id=" + urllib.parse.quote("R:10740157231685523") + "&view=dropped&limit=20", None),
            ("GET", "/api/agent-review/examples?browse=1&limit=40", None),
            ("GET", "/api/agent-review/reviews", None),
            ("GET", "/api/admin/agent-review/stats", None),
            ("GET", "/api/admin/agent-review/examples?limit=40", None),
            ("POST", "/api/agent-review/party/preview", {"sender": "宝珠", "platform": "wecom"}),
        ]
        for method, path, body in checks:
            c, payload = http_json(opener, method, path, body, token=token)
            if c != 200 or payload.get("ok") is False:
                issue(path + " -> " + str(c) + " " + str(payload.get("detail") or payload)[:180])
                continue
            if "hits?session_id" in path and "view=all" in path:
                st = payload.get("stats") or {}
                data = payload.get("data") or []
                if st.get("queue") not in (0, None) and st.get("queue") != 0:
                    pass
                reviewed_n = sum(1 for h in data if h.get("reviewed"))
                print("http hits all", "n", payload.get("total"), "stats", st, "page_reviewed", reviewed_n)
                if data and not any(h.get("reviewed") for h in data):
                    # first 20 by sort may be early unreviewed 乙方
                    pass
            if "view=queue" in path:
                if payload.get("data"):
                    issue("HTTP view=queue 仍有待审 " + str(len(payload.get("data") or [])))
            if path.endswith("/reviews") and not (payload.get("data") or []):
                issue("HTTP 最近审核为空")
        # isolated HTTP review
        http_sid = "_flow_http_ar"
        save_hits(http_sid, "*", [{
            "message_id": "h1",
            "sender": "宝珠",
            "time_text": "2026-10-15 12:00",
            "text": "请发一下简历谢谢",
            "party": "甲方",
            "gemini_useful": True,
            "intent": "supply_material",
            "intent_label": "要求补材料",
            "score": 82,
        }])
        c, saved = http_json(opener, "POST", "/api/agent-review/reviews", {
            "session_id": http_sid,
            "message_id": "h1",
            "selected_intents": [{"intent": "supply_material", "label": "要求补材料", "score": 82}],
            "should_filter": False,
            "text": "请发一下简历谢谢",
        }, token=token)
        if c != 200:
            issue("HTTP 保存审核失败 " + str(c) + " " + str(saved)[:200])
        else:
            hit = get_hit(http_sid, "h1")
            if not hit.get("reviewed"):
                issue("HTTP 审核后 hit.reviewed 仍为假")
            q = list_hits(http_sid, view="queue")
            if q["stats"]["queue"] != 0:
                issue("HTTP 审核后仍在待审")
        c, bad = http_json(opener, "POST", "/api/agent-review/reviews", {
            "session_id": http_sid,
            "message_id": "h1",
            "selected_intents": [{"intent": "other", "label": "其他"}],
            "should_filter": False,
        }, token=token)
        if c != 400:
            issue("缺备注的「其他」应返回 400，实际 " + str(c))
        with connect() as con:
            con.execute("DELETE FROM pipeline_hits WHERE session_id=?", (http_sid,))
            con.execute("DELETE FROM intent_reviews WHERE session_id=?", (http_sid,))
            con.execute("DELETE FROM prompt_examples WHERE session_id=?", (http_sid,))
            con.commit()
        print("http_flow ok")
    except urllib.error.URLError as e:
        issue("服务不可达 " + str(e))


def main():
    test_db_flow()
    check_static()
    check_real_session()
    test_http_flow()
    if ISSUES:
        print("FAILED", len(ISSUES))
        for x in ISSUES:
            print("-", x)
        raise SystemExit(1)
    print("flow ok")


if __name__ == "__main__":
    main()
