"""AI 审核流水线：甲方消息一律走 Jev 意图评分 → 70 分以上进组长队列。"""
from __future__ import annotations

import asyncio
import re

from .agent_extract import filename_of, is_delivery_name, is_system, text_of
from .agent_review import (
    SCORE_MIN,
    analyzed_ids,
    clear_hits,
    drop_match_reason,
    drop_text_keys,
    filtered_message_ids,
    get_run,
    list_hits,
    list_known_intents,
    load_examples,
    max_seq,
    save_hits,
    save_run,
    skip_jev_count,
    update_hit_seqs,
)
from .llm import extract_json_lenient
from .party import classify, clean_sender, infer_platform
from .wecom_intent import is_skip_intent, judge_message_scored

CHATTER_RE = re.compile(
    r"^((收到|好的|好滴|好哒|嗯+|哦+|噢+|喔+|额+|啊+|哈+|谢谢|辛苦[啦了哈]?|ok|okay|1|对|是|好)"
    r"[。.!！~，,、\s👍👌🙏😊😁]*?)+$",
    re.I,
)

def is_chatter_text(text: str) -> bool:
    t = re.sub(r"[\s\[\]\"“”]+", "", text or "")
    if not t:
        return True
    return bool(CHATTER_RE.match(t))


def parse_filter_json(text: str, ids: list[int]) -> dict[int, dict]:
    data = extract_json_lenient(text)
    rows = None
    if isinstance(data, dict):
        for key in ("items", "results", "messages", "data"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
        if rows is None and "useful" in data:
            rows = [data]
    elif isinstance(data, list):
        rows = data
    if not isinstance(rows, list):
        raise ValueError("过滤结果不是数组")
    by_id: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            nid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        by_id[nid] = {
            "useful": bool(row.get("useful")),
            "reason": str(row.get("reason") or "")[:120],
        }
    out = {}
    for nid in ids:
        hit = by_id.get(nid)
        if hit:
            out[nid] = hit
    return out


def fallback_filter(message: dict) -> dict:
    fname = filename_of(message)
    text = text_of(message)
    if fname:
        return {"useful": True, "reason": "有附件，不按回复过滤"}
    if not text:
        return {"useful": False, "reason": "无正文"}
    if is_chatter_text(text):
        return {"useful": False, "reason": "短回复/确认"}
    return {"useful": True, "reason": "规则保留"}


def needs_gemini(message: dict) -> bool:
    """兼容旧测试：短回复、空正文、纯附件不必再打模型。分析已改为只走 Jev。"""
    if filename_of(message):
        return False
    text = text_of(message)
    if not text or is_chatter_text(text):
        return False
    return True


def _pack_hit(message: dict, party: dict, filt: dict, judged: dict | None, *, skip_jev: bool = False, seq: int | None = None) -> dict:
    judged = judged or {}
    text = text_of(message)
    fname = filename_of(message)
    return {
        "message_id": str(message.get("message_id") or ""),
        "sender": clean_sender(message.get("sender")) or "未知",
        "time_text": str(message.get("time_text") or ""),
        "text": text[:2000],
        "filename": fname,
        "party": party.get("party") or "",
        "gemini_useful": bool(filt.get("useful")) and not skip_jev,
        "gemini_reason": str(filt.get("reason") or ""),
        "intent": judged.get("intent") or "",
        "intent_label": judged.get("intentLabel") or "",
        "need": judged.get("need") or "",
        "action": judged.get("action") or "",
        "score": int(judged.get("score") or 0),
        "error": judged.get("error") or "",
        "candidates": judged.get("candidates") if isinstance(judged.get("candidates"), list) else [],
        "skip_jev": bool(skip_jev),
        "seq": seq,
    }


def place_messages(
    messages: list[dict],
    *,
    done: set[str] | None = None,
    seq_base: int = 0,
    session: dict | None = None,
    filtered_ids: set[str] | None = None,
    drop_keys: set[str] | None = None,
) -> dict:
    """按原顺序给每条消息编号。乙方只登记原位，不进 Jev。应过滤的甲方直接进过滤明细。"""
    pending: list[tuple] = []
    pass_buf: list[dict] = []
    drop_buf: list[dict] = []
    seq_updates: list[tuple[str, int]] = []
    skipped = 0
    party_stats = {"甲方": 0, "乙方": 0}
    seen = done or set()
    sess = session or {}
    ids = filtered_ids if filtered_ids is not None else set()
    keys = drop_keys if drop_keys is not None else set()
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            skipped += 1
            continue
        seq = int(seq_base) + i
        platform = infer_platform(msg, sess)
        party = classify(msg.get("sender"), platform)
        party_stats[party["party"]] = party_stats.get(party["party"], 0) + 1
        if is_system(msg) or (not text_of(msg) and not filename_of(msg)):
            skipped += 1
            continue
        mid = str(msg.get("message_id") or "").strip()
        if not mid:
            skipped += 1
            continue
        if party["party"] != "甲方":
            if mid in seen:
                seq_updates.append((mid, seq))
            else:
                packed = _pack_hit(
                    msg, party,
                    {"useful": False, "reason": "乙方不进入Jev"},
                    None,
                    skip_jev=True,
                    seq=seq,
                )
                pass_buf.append(packed)
            continue
        if mid in seen:
            seq_updates.append((mid, seq))
            continue
        reason = drop_match_reason(message_id=mid, text=text_of(msg), filtered_ids=ids, drop_keys=keys)
        if reason:
            drop_buf.append(_pack_hit(
                msg, party,
                {"useful": False, "reason": reason},
                {"intent": "chat", "intentLabel": "应过滤", "score": 0, "action": reason},
                seq=seq,
            ))
            continue
        pending.append((msg, party, seq))
    return {
        "pending": pending,
        "pass_buf": pass_buf,
        "drop_buf": drop_buf,
        "seq_updates": seq_updates,
        "skipped": skipped,
        "party_stats": party_stats,
    }


async def _emit(progress, **ev) -> None:
    if not progress:
        return
    total = int(ev.get("total") or 0)
    done = int(ev.get("done") or 0)
    if total > 0 and "percent" not in ev:
        ev["percent"] = min(99, max(0, int(round(100 * done / total))))
    got = progress(ev)
    if asyncio.iscoroutine(got):
        await got


def _id_rank(message_id: str) -> tuple[int, str]:
    s = str(message_id or "")
    try:
        return (int(s), s)
    except ValueError:
        return (0, s)


def _msg_rank(message: dict) -> tuple:
    return (str(message.get("time_text") or ""),) + _id_rank(message.get("message_id"))


def _after_cursor(message: dict, last_time: str, last_id: str) -> bool:
    if not last_time and not last_id:
        return True
    return _msg_rank(message) > (str(last_time or ""),) + _id_rank(last_id)


async def analyze_session(
    session_id: str,
    source_id: str = "",
    name_hint: str = "",
    *,
    force: bool = False,
    only_jiafang: bool = False,
    batch_size: int = 32,
    progress=None,
) -> dict:
    from . import wecom_client as W

    sid = str(session_id or "").strip()
    if not sid:
        raise KeyError("未找到会话")
    run = {} if force else get_run(sid)
    last_time = str(run.get("last_time") or "")
    last_id = str(run.get("last_message_id") or "")
    done = set() if force else analyzed_ids(sid)
    full_done = bool(int(run.get("full_done") or 0))
    resume = (not force) and bool(done) and not full_done
    incremental = (not force) and full_done
    need_place = bool((incremental or resume) and done and skip_jev_count(sid) == 0)
    start_date = last_time[:10] if incremental and not need_place and not resume and re.match(r"\d{4}-\d{2}-\d{2}", last_time) else ""

    if force:
        clear_hits(sid)
        done = set()

    await _emit(
        progress,
        phase="load",
        percent=3,
        label=(
            "正在读取会话，从断点继续…" if resume
            else "正在读取会话并回填乙方原位…" if need_place
            else "正在读取会话（增量）…" if incremental
            else "正在全量读取会话…"
        ),
    )
    data = await W.list_merged_messages(
        sid,
        source_id=source_id,
        start_date=start_date,
        full=True,
    )
    all_messages = [m for m in (data.get("items") or []) if isinstance(m, dict)]
    name = str(data.get("display_name") or name_hint or sid)
    session = {
        "session_id": sid,
        "username": sid,
        "display_name": name,
        "source_id": source_id or "*",
    }
    if incremental and not need_place and not resume:
        work = [m for m in all_messages if _after_cursor(m, last_time, last_id)]
        seq_base = max_seq(sid) + 1
    else:
        work = all_messages
        seq_base = 0

    filtered_ids = filtered_message_ids(sid)
    drop_keys = drop_text_keys()
    placed = place_messages(
        work,
        done=done,
        seq_base=seq_base,
        session=session,
        filtered_ids=filtered_ids,
        drop_keys=drop_keys,
    )
    pending = placed["pending"]
    pass_buf = placed["pass_buf"]
    drop_buf = placed.get("drop_buf") or []
    party_stats = placed["party_stats"]
    skipped = placed["skipped"]
    if placed["seq_updates"]:
        update_hit_seqs(sid, placed["seq_updates"])
    if drop_buf:
        save_hits(sid, source_id or "*", drop_buf)
    if pass_buf:
        save_hits(sid, source_id or "*", pass_buf)
        await _emit(
            progress,
            phase="place",
            percent=6,
            yi=len(pass_buf),
            label="已登记乙方 " + str(len(pass_buf)) + " 条（原位展示，不进入 Jev）",
        )

    total = len(pending)
    prior = len(done)
    work_total = max(1, prior + total)
    await _emit(
        progress,
        phase="ready",
        percent=8 if not resume else min(90, int(round(100 * prior / work_total))),
        done=prior,
        total=prior + total,
        msgCount=len(work),
        incremental=incremental,
        resume=resume,
        placedYi=len(pass_buf),
        label=(
            ("从断点继续，已完成 " + str(prior) + " 条，剩余甲方 " + str(total) + " 条")
            if resume
            else ("回填乙方原位 " + str(len(pass_buf)) + " 条，待处理甲方 " + str(total) + " 条")
            if need_place
            else (
                ("增量分析，新消息 " + str(len(work)) + " 条，待处理 " + str(total) + " 条")
                if incremental
                else ("全量分析，待处理 " + str(total) + " 条")
            )
        ) + ("，开始 Jev 评分" if total else "，没有需要处理的甲方消息"),
    )
    save_run(
        sid,
        source_id=source_id or "*",
        name=name,
        last_time=last_time,
        last_message_id=last_id,
        msg_count=int(run.get("msg_count") or len(all_messages) or 0),
        full_done=False,
        progress_done=prior,
        progress_total=prior + total,
    )
    jev_n = 0
    processed = 0
    filtered_n = len(drop_buf)
    jev_sem = asyncio.Semaphore(8)
    intent_examples = load_examples("intent", 24)
    known_intents = list_known_intents()
    slim_examples = [
        {
            "text": ((r.get("filename") or "") + " " + (r.get("text") or "")).strip(),
            "intent": r.get("label"),
            "intentLabel": r.get("intentLabel") or "",
            "score": r.get("score"),
        }
        for r in intent_examples
    ]

    hits_buf: list[dict] = []
    keep_filt = {"useful": True, "reason": ""}

    async def score_one(msg: dict, party: dict, seq: int | None) -> dict:
        nonlocal jev_n
        jev_n += 1
        async with jev_sem:
            judged = await judge_message_scored(
                msg, examples=slim_examples, extra_intents=known_intents
            )
        return _pack_hit(msg, party, keep_filt, judged, seq=seq)

    size = max(8, min(int(batch_size or 32), 48))
    chunks = [pending[i: i + size] for i in range(0, len(pending), size)]

    for chunk in chunks:
        await _emit(
            progress,
            phase="score",
            percent=8 + int(82 * (prior + processed) / work_total),
            done=prior + processed,
            total=prior + total,
            jevDone=jev_n,
            dropped=filtered_n,
            resume=resume,
            label=("续跑 " if resume else "") + "Jev 评分本批 " + str(len(chunk)) + " 条（已完成 " + str(prior + processed) + "/" + str(prior + total) + "）",
        )
        packed = await asyncio.gather(*[
            score_one(item[0], item[1], item[2]) for item in chunk
        ])
        filtered_n += sum(1 for h in packed if is_skip_intent(h.get("intent"), h.get("intent_label")))
        hits_buf.extend(packed)
        processed += len(chunk)
        save_hits(sid, source_id or "*", hits_buf)
        newest_hit = max(hits_buf, key=lambda r: (str(r.get("time_text") or ""),) + _id_rank(r.get("message_id")))
        save_run(
            sid,
            source_id=source_id or "*",
            name=name,
            last_time=str(newest_hit.get("time_text") or last_time),
            last_message_id=str(newest_hit.get("message_id") or last_id),
            msg_count=int(run.get("msg_count") or len(all_messages) or 0),
            full_done=False,
            progress_done=prior + processed,
            progress_total=prior + total,
        )
        hits_buf = []
        await _emit(
            progress,
            phase="score",
            percent=8 + int(82 * (prior + processed) / work_total),
            done=prior + processed,
            total=prior + total,
            jevDone=jev_n,
            dropped=filtered_n,
            resume=resume,
            label=("续跑 " if resume else "") + "已完成 " + str(prior + processed) + "/" + str(prior + total) + " · Jev 评分 " + str(jev_n) + " · 闲聊/确认 " + str(filtered_n),
        )
    if hits_buf:
        await _emit(progress, phase="save", percent=96, done=prior + processed, total=prior + total, label="正在写入结果…")
        save_hits(sid, source_id or "*", hits_buf)

    listed = list_hits(sid, view="queue", offset=0, limit=20)
    stats = listed.get("stats") or {}
    cursor_time, cursor_id = last_time, last_id
    if all_messages:
        newest = max(all_messages, key=_msg_rank)
        if _msg_rank(newest) > (str(cursor_time or ""),) + _id_rank(cursor_id):
            cursor_time = str(newest.get("time_text") or "")
            cursor_id = str(newest.get("message_id") or "")
    stored_count = int(run.get("msg_count") or 0)
    if incremental and not need_place and not resume:
        stored_count += len(work)
    else:
        stored_count = len(all_messages)
    save_run(
        sid,
        source_id=source_id or "*",
        name=name,
        last_time=cursor_time,
        last_message_id=cursor_id,
        msg_count=stored_count,
        full_done=True,
        progress_done=prior + processed,
        progress_total=prior + total,
    )
    mode = "resume" if resume else ("incremental" if incremental else "full")
    return {
        "ok": True,
        "source_id": source_id or "*",
        "session_id": sid,
        "name": name,
        "is_delivery": is_delivery_name(name),
        "mode": mode,
        "incremental": incremental,
        "resume": resume,
        "msg_count": stored_count,
        "new_count": len(work) if incremental else stored_count,
        "placedYi": len(pass_buf),
        "pending": len(pending),
        "skipped": skipped,
        "geminiCalls": 0,
        "jevCalls": jev_n,
        "party_stats": party_stats,
        "scoreMin": SCORE_MIN,
        "stats": stats,
        "data": listed.get("data") or [],
    }
