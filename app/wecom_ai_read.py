"""AI 解读：先扫当前会话全部文件，按规则分类，再按时间绑定申报书版本与修改意见。"""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta

from .config import LLM_TIMEOUT_CLASSIFY, resolve_gemini
from . import wecom_client as W
from .llm import LlmError, chat, extract_json_lenient
from .party import sender_of
from .wecom_ai_bind import PROMPT_TIME_WINDOW
from .wecom_cases import annotate_existing, cluster_messages, split_scan_stats
from .wecom_file_class import counts_of, list_file_entries, make_cluster_kind_of, sanitize_classified_entry, slim_items
from .wecom_groups import list_watch_groups

_SUMMARY_PROMPT = """你是申报书修改系统的聊天解读助手。已扫描会话全部文件、按规则分类，并按时间完成「申报书版本 ↔ 修改意见」绑定。

只输出一个 JSON 对象：
{
  "summary": "2-4 句概述各群最近在办什么、已配对多少份申报书与意见",
  "highlights": ["要点，每条一句"],
  "risks": ["待办或风险，如缺意见、已有任务、某群无消息"],
  "suggestedAction": "建议用户下一步做什么（拆解任务、手工勾选上传、或无需操作）"
}

要求：
- 只依据输入，禁止编造姓名、编号、文件名
- 没有可配对任务时 risks 可写「暂无待处理配对」
- highlights 最多 6 条，risks 最多 5 条

输入：
"""


async def _progress(cb, phase: str, percent: int, label: str, **extra) -> None:
    if not cb:
        return
    ev = {"phase": phase, "percent": max(0, min(100, int(percent))), "label": str(label or "")}
    ev.update(extra)
    out = cb(ev)
    if asyncio.iscoroutine(out):
        await out


def _default_dates(start: str, end: str) -> tuple[str, str]:
    end = str(end or "").strip()[:10]
    start = str(start or "").strip()[:10]
    if not start and not end:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    return start, end


def _slim_case(c: dict, session_name: str = "") -> dict:
    app = c.get("app") if isinstance(c.get("app"), dict) else {}
    ops = c.get("opinions") or []
    slim_app = {
        "filename": app.get("filename") or "",
        "time": app.get("time") or "",
        "sender": sender_of(app),
        "message_id": app.get("message_id") or 0,
        "kind": app.get("kind") or "file",
        "copies": list(app.get("copies") or [])[:8],
    }
    return {
        "id": c.get("id"),
        "session_name": session_name or str(c.get("session_name") or ""),
        "session_id": str(c.get("session_id") or c.get("sessionId") or ""),
        "filename": slim_app["filename"],
        "time": slim_app["time"],
        "sender": slim_app["sender"],
        "message_id": slim_app["message_id"],
        "app": slim_app,
        "names": list(c.get("names") or [])[:4],
        "nums": list(c.get("nums") or [])[:6],
        "ready": bool(c.get("ready")),
        "existing": c.get("existing") if isinstance(c.get("existing"), dict) else bool(c.get("existing")),
        "similar": c.get("similar") if isinstance(c.get("similar"), dict) else bool(c.get("similar")),
        "opinionCount": len(ops),
        "opinions": [
            {
                "kind": o.get("kind"),
                "filename": o.get("filename") or "",
                "time": o.get("time") or "",
                "message_id": o.get("message_id") or 0,
                "copies": list(o.get("copies") or [])[:8] if str(o.get("kind") or "") != "text" else [],
                "text": str(o.get("text") or "")[:400] if str(o.get("kind") or "") == "text" else "",
            }
            for o in ops[:6]
        ],
        "warnings": list(c.get("warnings") or [])[:3],
    }


def _cases_have_opinion(cases: list) -> bool:
    for c in cases or []:
        if c.get("opinions"):
            return True
        if c.get("ready") and not c.get("existing"):
            return True
    return False


async def _summarize(
    *,
    scope: str,
    groups: list,
    all_cases: list,
) -> dict:
    empty = {"summary": "", "highlights": [], "risks": [], "suggestedAction": ""}
    if not groups and not all_cases:
        return {
            **empty,
            "summary": "当前日期窗内没有可分析的消息。",
            "suggestedAction": "放宽日期范围或确认解析服务已同步关注群。",
        }
    try:
        prof = resolve_gemini()
    except ValueError as e:
        return {**empty, "summary": str(e)}
    payload = {
        "scope": scope,
        "groupCount": len(groups),
        "groups": [
            {
                "session_name": g.get("session_name") or "",
                "messageCount": g.get("messageCount") or 0,
                "scan": g.get("scan") or {},
                "geminiNote": g.get("geminiNote") or "",
                "readyCount": g.get("readyCount") or 0,
                "caseCount": len(g.get("cases") or []),
            }
            for g in groups[:12]
        ],
        "readyCount": sum(1 for c in all_cases if c.get("ready") and not c.get("existing")),
        "cases": [_slim_case(c, c.get("session_name") or "") for c in all_cases[:16]],
    }
    try:
        resp = await chat(
            [{"role": "user", "content": _SUMMARY_PROMPT + json.dumps(payload, ensure_ascii=False)}],
            json_mode=True,
            timeout_s=min(float(prof.get("timeoutSec") or 120), LLM_TIMEOUT_CLASSIFY * 2),
            profile=prof,
            retries=1,
            apply_profile_timeout=False,
        )
        data = extract_json_lenient(resp.get("content") or "")
        if not isinstance(data, dict):
            raise ValueError("摘要不是 JSON 对象")
        return {
            "summary": str(data.get("summary") or "").strip()[:800],
            "highlights": [str(x).strip() for x in (data.get("highlights") or []) if str(x).strip()][:6],
            "risks": [str(x).strip() for x in (data.get("risks") or []) if str(x).strip()][:5],
            "suggestedAction": str(data.get("suggestedAction") or data.get("suggested_action") or "").strip()[:300],
        }
    except (LlmError, ValueError, TypeError, json.JSONDecodeError):
        bits = []
        total_apps = sum((g.get("scan") or {}).get("appFileCount") or 0 for g in groups)
        if total_apps:
            bits.append(f"识别到 {total_apps} 份申报书相关文件")
        ready = sum(1 for c in all_cases if c.get("ready") and not c.get("existing"))
        if ready:
            bits.append(f"可提交配对 {ready} 份")
        elif _cases_have_opinion(all_cases):
            bits.append("存在修改意见待配对")
        return {
            **empty,
            "summary": "；".join(bits) or "已通读消息，摘要生成失败，请查看下方配对结果。",
            "suggestedAction": "可点「拆解任务」预览，或手工勾选消息后上传。",
        }


def scan_files_then_pair(
    messages: list,
    *,
    session_id: str = "",
    session_name: str = "",
    window_hours: int = 48,
    runner=None,
) -> dict:
    """先扫全部文件，用原有规则分类，再按分类结果拆案。"""
    entries = list_file_entries(messages)
    for it in entries:
        sanitize_classified_entry(it)
    kind_of = make_cluster_kind_of(entries)
    scan = split_scan_stats(messages, kind_of=kind_of)
    cluster_hours = int(window_hours or 48)
    if cluster_hours <= 0:
        cluster_hours = 48
    cases = cluster_messages(
        messages,
        session_id=session_id,
        session_name=session_name,
        window_hours=cluster_hours,
        kind_of=kind_of,
    )
    if runner is not None:
        annotate_existing(cases, runner)
    for c in cases:
        c["session_name"] = session_name
        c["session_id"] = session_id
    n_app = sum(1 for it in entries if it.get("type") == "app")
    n_op = sum(1 for it in entries if it.get("type") == "opinion")
    note = (
        "已扫描全部文件 " + str(len(entries))
        + " 个，按文件名与前后聊天规则分类（申报书 " + str(n_app)
        + "、修改意见 " + str(n_op)
        + "）；申报书版本与修改意见按时间绑定"
    )
    return {
        "entries": entries,
        "kind_of": kind_of,
        "scan": scan,
        "cases": cases,
        "geminiNote": note,
        "fileTypes": {
            "items": slim_items(entries),
            "counts": counts_of(entries),
            "fileCount": len(entries),
            "geminiNote": note,
            "usedGemini": False,
        },
    }


async def _analyze_one_group(
    *,
    session_id: str,
    session_name: str = "",
    source_id: str = "",
    start_date: str,
    end_date: str,
    window_hours: int,
    runner=None,
    catalog_max_files: int = 200,
    catalog_max_texts: int = 200,
    bind: bool = False,
    bind_prompts: list | None = None,
    user: dict | None = None,
    progress=None,
) -> dict:
    sid = str(session_id or "").strip()
    await _progress(progress, "load", 8, "正在读取聊天记录")
    data = await W.list_merged_messages(
        sid,
        source_id=str(source_id or ""),
        start_date=start_date,
        end_date=end_date,
        full=True,
    )
    messages = data.get("items") or []
    name = str(session_name or data.get("display_name") or sid)
    nmsg = len(messages)
    await _progress(progress, "load", 22, "已读取 " + str(nmsg) + " 条消息", messages=nmsg)
    paired = scan_files_then_pair(
        messages,
        session_id=sid,
        session_name=name,
        window_hours=window_hours,
        runner=runner,
    )
    nfiles = len(paired.get("entries") or [])
    await _progress(
        progress, "scan", 48,
        "规则扫描完成 · 文件 " + str(nfiles) + " 个",
        files=nfiles,
    )
    cases = paired.get("cases") or []
    ready = sum(1 for c in cases if c.get("ready") and not c.get("existing"))
    bind_out = {}
    if bind:
        from .wecom_ai_bind import run_version_bind
        await _progress(progress, "bind", 55, "正在按时间绑定申报书与修改意见")
        prompts = bind_prompts if isinstance(bind_prompts, list) and bind_prompts else [PROMPT_TIME_WINDOW]
        bind_out = await run_version_bind(
            messages,
            session_id=sid,
            session_name=name,
            window_hours=int(window_hours or 48),
            bind_prompts=prompts,
            user=user,
            kind_of=paired.get("kind_of"),
        )
        await _progress(progress, "bind", 78, "绑定完成")
    return {
        "session_id": sid,
        "session_name": name,
        "start_date": start_date,
        "end_date": end_date,
        "messageCount": data.get("total") or len(messages),
        "scan": paired.get("scan") or {},
        "cases": cases,
        "readyCount": ready,
        "geminiNote": paired.get("geminiNote") or "",
        "hasOpinion": _cases_have_opinion(cases),
        "bindPrompts": bind_out,
        "fileTypes": paired.get("fileTypes") or {},
    }


async def ai_read_watched(
    *,
    start_date: str = "",
    end_date: str = "",
    window_hours: int = 48,
    runner=None,
    on_group=None,
    bind: bool = False,
    bind_prompts: list | None = None,
    user: dict | None = None,
    progress=None,
) -> dict:
    start, end = _default_dates(start_date, end_date)
    allow = list_watch_groups()
    if not allow:
        raise ValueError("尚未关注任何群，请先在群列表点 ★ 关注")
    try:
        listed = await W.list_merged_groups(kind="group", limit=400, watch_only=True)
    except W.WecomError as e:
        raise ValueError(e.message) from e
    items = listed.get("items") or []
    if not items:
        raise ValueError("关注群在解析服务中未找到，请确认群名或 session_id 正确")

    groups_out = []
    all_cases = []
    errors = []
    bind_out: dict = {}
    total = len([g for g in items if isinstance(g, dict) and str(g.get("username") or g.get("session_id") or "").strip()])
    done = 0
    for g in items:
        if not isinstance(g, dict):
            continue
        sid = str(g.get("username") or g.get("session_id") or "").strip()
        name = str(g.get("display_name") or sid)
        if not sid:
            continue
        pct = 8 + int(70 * done / max(1, total))
        await _progress(
            progress, "group", pct,
            "正在解读 " + name + "（" + str(done + 1) + "/" + str(total) + "）",
        )
        try:
            one = await _analyze_one_group(
                session_id=sid,
                session_name=name,
                start_date=start,
                end_date=end,
                window_hours=window_hours,
                runner=runner,
                bind=bind,
                bind_prompts=bind_prompts,
                user=user,
            )
        except W.WecomError as e:
            errors.append(name + "：" + e.message)
            continue
        except Exception as e:
            errors.append(name + "：" + str(e)[:120])
            continue
        slim_cases = [_slim_case(c, name) for c in one.get("cases") or []]
        ft = one.get("fileTypes") or {}
        groups_out.append({
            "session_id": one["session_id"],
            "session_name": one["session_name"],
            "messageCount": one["messageCount"],
            "scan": one["scan"],
            "readyCount": one["readyCount"],
            "hasOpinion": one["hasOpinion"],
            "geminiNote": one["geminiNote"],
            "cases": slim_cases[:12],
            "fileTypes": {
                "items": (ft.get("items") or [])[:20],
                "counts": ft.get("counts") or {},
                "fileCount": ft.get("fileCount") or 0,
                "geminiNote": ft.get("geminiNote") or "",
            },
        })
        all_cases.extend(one.get("cases") or [])
        if bind:
            for pid, block in (one.get("bindPrompts") or {}).items():
                acc = bind_out.setdefault(pid, {"id": pid, "talents": [], "unboundOpinions": [], "conflicts": []})
                if isinstance(block, dict):
                    acc["talents"].extend(block.get("talents") or [])
                    acc["unboundOpinions"].extend(block.get("unboundOpinions") or [])
                    acc["conflicts"].extend(block.get("conflicts") or [])
        done += 1
        if on_group:
            try:
                on_group(done, total, one)
            except Exception:
                pass

    await _progress(progress, "summary", 85, "正在生成关注群摘要")
    summary = await _summarize(scope="watch", groups=groups_out, all_cases=all_cases)
    try:
        model = str(resolve_gemini().get("id") or "")
    except ValueError:
        model = ""
    ready_total = sum(1 for c in all_cases if c.get("ready") and not c.get("existing"))
    file_items = []
    file_counts = {}
    for g in groups_out:
        ft = g.get("fileTypes") or {}
        for it in ft.get("items") or []:
            if len(file_items) < 80:
                row = dict(it)
                row["session_name"] = g.get("session_name") or ""
                file_items.append(row)
        for lab, n in (ft.get("counts") or {}).items():
            file_counts[lab] = file_counts.get(lab, 0) + int(n or 0)
    return {
        "ok": True,
        "scope": "watch",
        "watchGroupCount": len(allow),
        "groupCount": len(groups_out),
        "start_date": start,
        "end_date": end,
        "messageCount": sum(g.get("messageCount") or 0 for g in groups_out),
        "groups": groups_out,
        "cases": [_slim_case(c, c.get("session_name") or "") for c in all_cases[:24]],
        "readyCount": ready_total,
        "hasOpinion": _cases_have_opinion(all_cases),
        "summary": summary,
        "bindPrompts": bind_out if bind else {},
        "fileTypes": {
            "items": file_items,
            "counts": file_counts,
            "fileCount": sum(int((g.get("fileTypes") or {}).get("fileCount") or 0) for g in groups_out),
            "geminiNote": "",
        },
        "errors": errors[:8],
        "model": model,
        "engine": "file-rules-time",
    }


async def ai_read_chat(
    *,
    session_id: str,
    session_name: str = "",
    source_id: str = "",
    start_date: str = "",
    end_date: str = "",
    window_hours: int = 48,
    runner=None,
    scope: str = "",
    bind: bool = True,
    bind_prompts: list | None = None,
    user: dict | None = None,
    progress=None,
) -> dict:
    """scope=watch 时扫描全部关注群；scope=session 时扫描当前会话全部文件后按规则分类、按时间绑定。"""
    if str(scope or "").strip().lower() == "watch":
        return await ai_read_watched(
            start_date=start_date,
            end_date=end_date,
            window_hours=window_hours,
            runner=runner,
            bind=bind,
            bind_prompts=bind_prompts,
            user=user,
            progress=progress,
        )
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("缺少 session_id")
    start = str(start_date or "").strip()[:10]
    end = str(end_date or "").strip()[:10]
    one = await _analyze_one_group(
        session_id=sid,
        session_name=session_name,
        source_id=source_id,
        start_date=start,
        end_date=end,
        window_hours=window_hours,
        runner=runner,
        bind=bind,
        bind_prompts=bind_prompts,
        user=user,
        progress=progress,
    )
    name = one["session_name"]
    cases = one.get("cases") or []
    await _progress(progress, "summary", 82, "正在生成解读摘要")
    summary = await _summarize(
        scope="session",
        groups=[{
            "session_name": name,
            "messageCount": one["messageCount"],
            "scan": one["scan"],
            "geminiNote": one["geminiNote"],
            "readyCount": one["readyCount"],
            "cases": [_slim_case(c, name) for c in cases],
        }],
        all_cases=cases,
    )
    await _progress(progress, "summary", 94, "解读完成")
    try:
        model = str(resolve_gemini().get("id") or "")
    except ValueError:
        model = ""
    return {
        "ok": True,
        "scope": "session",
        "session_id": sid,
        "session_name": name,
        "start_date": start,
        "end_date": end,
        "messageCount": one["messageCount"],
        "scan": one["scan"],
        "geminiNote": one["geminiNote"],
        "cases": [_slim_case(c, name) for c in cases[:12]],
        "readyCount": one["readyCount"],
        "hasOpinion": one["hasOpinion"],
        "summary": summary,
        "bindPrompts": one.get("bindPrompts") or {},
        "fileTypes": one.get("fileTypes") or {},
        "model": model,
        "engine": "file-rules-time",
    }
