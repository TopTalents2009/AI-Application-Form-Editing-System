# -*- coding: utf-8 -*-
"""告警过滤、去重、正文格式。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import alert as AL
from app.issue_watch import match_keywords, scan_messages, expand_keyword


def test_format_and_level():
    text = AL._format("P0", "任务失败 51104", ["提交人：test1", "原因：写入失败"], "/t/abc")
    assert "【P0】任务失败 51104" in text
    assert "提交人：test1" in text
    assert "/t/abc" in text
    assert AL._LEVEL_RANK["P0"] < AL._LEVEL_RANK["P1"]
    print("format ok")


def test_mentioned():
    # 无配置时不 @ 人
    assert AL._mentioned("申报部门") == []
    print("mentioned empty ok")


def test_leaders_from_messages():
    msgs = [
        {"sender": "申报一组唐文秀", "text": "收到"},
        {"sender": "申报一组组长-小唐18198156910", "text": "看一下"},
        {"sender": "申报书三组组员-刘湘泉", "text": "已发"},
        {"sender": "申报二组组长-小胡（小胡 18164804910）", "text": "好"},
        {"sender": "申报一组组长-小唐18198156910", "text": "再催"},
        {"sender": "申报三组组长-任涛", "text": "尽快"},
    ]
    leaders = AL.leaders_from_messages(msgs)
    assert leaders == [
        "申报一组组长-小唐18198156910",
        "申报二组组长-小胡（小胡 18164804910）",
        "申报三组组长-任涛",
    ]
    mobiles = AL.mobiles_from_leader_names(leaders)
    assert mobiles == ["18198156910", "18164804910"]
    assert AL.mobiles_from_leader_names(["申报三组组长-任涛"]) == []
    print("leaders/mobile ok")


def test_mask_webhook():
    assert AL._mask_webhook("") == ""
    assert AL._mask_webhook("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=4535b383-128e-4fcb-8ece-7f7aa1274bfe") == "…4bfe"
    st = AL.public_status()
    assert "webhook" not in st
    if st.get("configured"):
        assert st.get("webhookHint")
        assert "4535b383" not in str(st.get("webhookHint") or "")
    db = st.get("dueBroadcast") or {}
    assert "webhook" not in db
    assert "times" in db
    assert db.get("webhookReady") == bool(st.get("configured"))
    iw = st.get("issueWatch") or {}
    assert "低级错误" in (iw.get("keywords") or iw.get("defaults") or [])
    print("mask ok")


def test_issue_keywords():
    assert "低级错误" in match_keywords("这是低级错误，马上改")
    assert "怎么回事" in match_keywords("这是怎么回事")
    assert "什么时候" in match_keywords("材料什么时候给")
    assert "到底" in match_keywords("到底好了没有")
    assert "尽快" in match_keywords("请尽快处理")
    assert "急" in match_keywords("这个很急")
    assert "急" not in match_keywords("去急诊看看")
    assert "人呢？" in match_keywords("人呢？材料呢")
    assert "今天能交/给我吗" in match_keywords("今天能交吗")
    assert "今天能交/给我吗" in match_keywords("今天给我吗")
    assert "涉嫌造假" in match_keywords("这个涉嫌造假")
    assert "重复" in match_keywords("又重复了一遍")
    assert "效率差" in match_keywords("效率差得不行")
    assert "？" in match_keywords("？？？")
    assert "？" not in match_keywords("材料什么时候给？")
    hits = scan_messages([{"text": "人呢？赶紧", "sender": "客户", "message_id": 1}])
    assert hits and "人呢？" in hits[0]["keywords"]
    assert "今天给我吗" in expand_keyword("今天能交/给我吗")
    print("keywords ok")


if __name__ == "__main__":
    test_format_and_level()
    test_mentioned()
    test_leaders_from_messages()
    test_mask_webhook()
    test_issue_keywords()
    print("ALL OK")
