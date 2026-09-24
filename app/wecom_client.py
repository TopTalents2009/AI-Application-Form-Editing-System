"""企业微信聊天记录解析服务客户端（只读 /v1）。"""
from __future__ import annotations
import asyncio, json, re, time
from urllib.parse import quote, unquote
import httpx
from .config import load_config, httpx_trust_env

PREFIX = "/v1"


class WecomError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _root() -> str:
    cfg = load_config()
    return str(cfg.get("wecomChatBaseUrl") or "http://127.0.0.1:8767").rstrip("/")


def _key() -> str:
    return str(load_config().get("wecomChatApiKey") or "")


def _headers(need_key: bool = True) -> dict:
    h = {"Accept": "application/json"}
    if need_key:
        key = _key()
        if key:
            h["X-API-Key"] = key
    return h


def _timeout():
    return httpx.Timeout(20.0, connect=5.0)


def _enc(part: str) -> str:
    return quote(str(part or ""), safe="-_.~")


def is_group(sess: dict | None) -> bool:
    if not isinstance(sess, dict):
        return False
    uid = str(sess.get("username") or sess.get("session_id") or "")
    try:
        st = int(sess.get("session_type") or 0)
    except (TypeError, ValueError):
        st = 0
    if st == 2 or uid.startswith("R:"):
        return True
    name = str(sess.get("display_name") or "")
    return "群" in name


def match_allowlist(sess: dict | None, allow: list | None) -> bool:
    names = [str(x or "").strip() for x in (allow or []) if str(x or "").strip()]
    if not names:
        return True
    if not isinstance(sess, dict):
        return False
    uid = str(sess.get("username") or sess.get("session_id") or "")
    title = str(sess.get("display_name") or "")
    blob = (uid + " " + title).lower()
    for a in names:
        if a == uid or a == title or a.lower() in blob:
            return True
    return False


def _err(status: int, text: str) -> WecomError:
    msg = (text or "")[:240]
    try:
        d = json.loads(text or "{}")
        if isinstance(d, dict):
            det = d.get("detail")
            if isinstance(det, str) and det.strip():
                msg = det.strip()
            elif isinstance(det, dict):
                msg = str(det.get("message") or det.get("detail") or msg)
    except Exception:
        pass
    if status == 401:
        return WecomError("AUTH", msg or "API Key 无效", status)
    if status == 403:
        return WecomError("FORBIDDEN", msg or "开放读取 API 已关闭", status)
    if status == 404:
        return WecomError("NOT_FOUND", msg or "电脑或会话不存在", status)
    return WecomError("HTTP_" + str(status), msg or ("HTTP " + str(status)), status)


async def _get(path: str, params: dict | None = None, *, need_key: bool = True) -> dict:
    url = _root() + path
    try:
        async with httpx.AsyncClient(timeout=_timeout(), trust_env=httpx_trust_env()) as client:
            r = await client.get(url, headers=_headers(need_key), params=params or {})
    except httpx.HTTPError as e:
        raise WecomError("NETWORK", "解析服务不可达：" + str(e)[:160])
    if r.status_code >= 400:
        raise _err(r.status_code, r.text)
    try:
        data = r.json()
    except Exception:
        raise WecomError("BAD_RESPONSE", "响应不是 JSON")
    if not isinstance(data, dict):
        raise WecomError("BAD_RESPONSE", "响应不是 JSON 对象")
    return data


async def health() -> dict:
    from . import wecom_local as L
    cfg = load_config()
    local = L.status()
    out = {
        "configured": bool(cfg.get("wecomChatConfigured")),
        "baseUrl": _root(),
        "service_ok": False,
        "auth_ok": False,
        "ok": False,
        "error": "",
        "hint": "",
        "source_count": 0,
        "readMode": "http",
    }
    if local.get("ok"):
        out["ok"] = True
        out["service_ok"] = True
        out["auth_ok"] = True
        out["readMode"] = "local"
        out["source_count"] = int(local.get("sources") or 0)
        out["dataRoot"] = local.get("root") or ""
        out["hint"] = "看板直接读取本地同步目录，消息不再整段经过解析服务"
        return out
    try:
        data = await _get(PREFIX + "/health", need_key=False)
    except WecomError as e:
        out["error"] = e.message
        out["hint"] = "请先启动企业微信聊天记录解析（start.ps1，端口 8767），并在管理后台填写 wecomChat.apiKey"
        return out
    out["service_ok"] = bool(data.get("ok") or data.get("read_api") is not False or data.get("ok") is True)
    if data.get("ok") is False and not data.get("read_api"):
        out["error"] = "解析服务异常"
        return out
    try:
        src = await list_sources()
        n = int(src.get("count") or len(src.get("items") or []))
        out["source_count"] = n
        out["ok"] = True
        out["auth_ok"] = True
        out["error"] = ""
        if not cfg.get("wecomChatConfigured"):
            out["hint"] = "未填开放 API Key 也可浏览已同步群；建议仍配置 key"
    except WecomError as e:
        out["error"] = e.message
        if e.code == "AUTH":
            out["hint"] = "API Key 无效，请对照解析服务首页重新填写"
        else:
            out["hint"] = e.message
    return out


async def _api_get(path: str, params: dict | None = None) -> object:
    url = _root() + path
    try:
        async with httpx.AsyncClient(timeout=_timeout(), trust_env=httpx_trust_env()) as client:
            r = await client.get(url, headers=_headers(False), params=params or {})
    except httpx.HTTPError as e:
        raise WecomError("NETWORK", "解析服务不可达：" + str(e)[:160])
    if r.status_code >= 400:
        raise _err(r.status_code, r.text)
    try:
        return r.json()
    except Exception:
        raise WecomError("BAD_RESPONSE", "响应不是 JSON")


def source_label(src: dict | None) -> str:
    if not isinstance(src, dict):
        return ""
    sid = str(src.get("id") or "")
    if sid == "local" or str(src.get("kind") or "") == "local":
        return "本机"
    return str(src.get("operator_name") or src.get("computer_name") or sid)


def _clock(t: object) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", str(t or ""))
    if not m:
        return str(t or "").strip()
    return f"{int(m.group(1)):02d}:{m.group(2)}"


_WX_MEDIA_URL = re.compile(
    r"https?://(?:wework\.qpic\.cn|wx\.qlogo\.cn|mmbiz\.qpic\.cn|pic\.weixin\.qq\.com|"
    r"imunion\.weixin\.qq\.com|(?:[\w.-]+\.)?weixin\.qq\.com/cgi-bin/mmae-bin/tpdownloadmedia)\S*",
    re.I,
)
_WX_FILE_NAME = re.compile(
    r"\[?(?:文件|file)?\]?\s*([^\n[\]/\\]+?\.(?:zip|rar|7z|pdf|xlsx?|docx?|pptx?|png|jpe?g|gif|webp|bmp|mp4|txt|md))",
    re.I,
)


def _normalize_msg(m: dict) -> dict:
    """文件通知常被解析成「系统消息」+ 内部下载链，收成附件字段并去掉 URL。"""
    if not isinstance(m, dict):
        return m
    text = str(m.get("text") or m.get("snippet") or "")
    urls = _WX_MEDIA_URL.findall(text)
    cleaned = _WX_MEDIA_URL.sub(" ", text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    fname = str(m.get("attachment_name") or "").strip()
    if not fname:
        hit = _WX_FILE_NAME.search(cleaned) or _WX_FILE_NAME.search(text)
        if hit:
            fname = str(hit.group(1) or "").strip()
    is_file_url = any(
        "tpdownloadmedia" in u.lower() or "imunion.weixin.qq.com" in u.lower()
        for u in urls
    )
    if is_file_url:
        m["has_attachment"] = True
        if fname:
            m["attachment_name"] = fname
    img = next((u for u in urls if re.search(r"qpic\.cn|qlogo\.cn|pic\.weixin", u, re.I)), "")
    if img and not m.get("media_url"):
        m["media_url"] = img
    if urls:
        leftover = cleaned
        shown = str(m.get("attachment_name") or fname or "").strip()
        if shown:
            leftover = re.sub(r"^\[(?:文件|file)\]\s*", "", leftover, flags=re.I)
            leftover = leftover.replace(shown, "")
            leftover = leftover.strip(" \n\t[]【】")
            leftover = re.sub(r"\s+", " ", leftover).strip()
        m["text"] = leftover
    return m


def _msg_fuse_key(m: dict) -> tuple:
    att = str(m.get("attachment_name") or "").strip()
    sender = str(m.get("sender_id") or m.get("sender") or "")
    clock = _clock(m.get("time_text"))
    typ = str(m.get("msg_type") or "")
    media = str(m.get("media_url") or "").strip()
    if media:
        return ("m", media[:160])
    if att:
        return ("f", att.lower(), sender)
    text = re.sub(r"\s+", " ", str(m.get("text") or "")).strip()[:120]
    return ("t", clock, sender, typ, text)


def _sync_stamp(value: str) -> str:
    """把同步时间收成可比较、可展示的「YYYY-MM-DD HH:MM:SS」。"""
    s = str(value or "").strip().replace("T", " ")
    if "+" in s:
        s = s.split("+", 1)[0].strip()
    if s.endswith("Z"):
        s = s[:-1].strip()
    return s[:19]


def merge_groups(pairs: list) -> list:
    buckets: dict[str, dict] = {}
    for sess, src in pairs:
        if not isinstance(sess, dict) or not isinstance(src, dict):
            continue
        uid = str(sess.get("username") or sess.get("session_id") or "").strip()
        if not uid:
            continue
        b = buckets.get(uid)
        if not b:
            b = {
                "username": uid,
                "display_name": "",
                "is_group": False,
                "msg_count": 0,
                "last_time": "",
                "synced_at": "",
                "replicas": [],
            }
            buckets[uid] = b
        name = str(sess.get("display_name") or "").strip()
        if name and (not b["display_name"] or len(name) > len(b["display_name"])):
            b["display_name"] = name
        b["is_group"] = bool(b["is_group"] or is_group(sess))
        try:
            n = int(sess.get("msg_count") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > int(b["msg_count"] or 0):
            b["msg_count"] = n
        lt = str(sess.get("last_time") or "")
        if lt > str(b.get("last_time") or ""):
            b["last_time"] = lt
        sync = _sync_stamp(sess.get("synced_at") or src.get("last_sync") or "")
        if sync > str(b.get("synced_at") or ""):
            b["synced_at"] = sync
        sid = str(src.get("id") or "")
        existing = next((r for r in b["replicas"] if r.get("source_id") == sid), None)
        if existing:
            if sync > str(existing.get("synced_at") or ""):
                existing["synced_at"] = sync
            if lt > str(existing.get("last_time") or ""):
                existing["last_time"] = lt
                existing["msg_count"] = n
            continue
        b["replicas"].append({
            "source_id": sid,
            "kind": str(src.get("kind") or ""),
            "label": source_label(src),
            "msg_count": n,
            "last_time": lt,
            "synced_at": sync,
        })
    out = list(buckets.values())
    out.sort(key=lambda x: (str(x.get("synced_at") or ""), str(x.get("last_time") or "")), reverse=True)
    return out


def merge_messages(batches: list, session_id: str = "") -> list:
    buckets: dict[tuple, dict] = {}
    order: list[tuple] = []
    cid = str(session_id or "")
    for msgs, src in batches:
        if not isinstance(src, dict):
            continue
        sid = str(src.get("id") or "")
        label = source_label(src)
        for m in msgs or []:
            if not isinstance(m, dict):
                continue
            m = _normalize_msg(dict(m))
            key = _msg_fuse_key(m)
            row = buckets.get(key)
            if not row:
                row = dict(m)
                row["copies"] = []
                buckets[key] = row
                order.append(key)
            mid = 0
            try:
                mid = int(m.get("message_id") or 0)
            except (TypeError, ValueError):
                mid = 0
            copy = {
                "source_id": sid,
                "source_label": label,
                "kind": str(src.get("kind") or ""),
                "message_id": mid,
                "session_id": cid or str(m.get("session_id") or ""),
            }
            seen = {c.get("source_id") for c in row["copies"]}
            if sid not in seen and mid:
                row["copies"].append(copy)
            if m.get("media_url") and not row.get("media_url"):
                row["media_url"] = m.get("media_url")
            if m.get("attachment_name") and not row.get("attachment_name"):
                row["attachment_name"] = m.get("attachment_name")
            if m.get("has_attachment"):
                row["has_attachment"] = True
            if not row.get("message_id") and mid:
                row["message_id"] = mid
            nt = str(m.get("time_text") or "")
            ot = str(row.get("time_text") or "")
            if nt and len(nt) > len(ot):
                row["time_text"] = nt
    out = [buckets[k] for k in order]
    out.sort(key=_msg_sort_key)
    return out


def _msg_sort_key(m: dict) -> str:
    t = str((m or {}).get("time_text") or "")
    day = re.search(r"(\d{4}-\d{2}-\d{2})", t)
    return (day.group(1) if day else "0000-00-00") + " " + _clock(t)


async def list_sources() -> dict:
    from . import wecom_local as L
    if L.available():
        items = await asyncio.to_thread(L.list_sources)
        if items:
            return {"items": items, "count": len(items), "readMode": "local"}
    try:
        rows = await _api_get("/api/sources")
        if isinstance(rows, list):
            items = [x for x in rows if isinstance(x, dict)]
            return {"items": items, "count": len(items), "readMode": "http"}
    except WecomError:
        pass
    data = await _get(PREFIX + "/sources")
    items = data.get("data") if isinstance(data.get("data"), list) else []
    return {"items": items, "count": int(data.get("count") or len(items)), "readMode": "http"}


async def _sessions_of(source_id: str, limit: int = 1000) -> list:
    from . import wecom_local as L
    sid = str(source_id or "").strip()
    if L.available() and L.has_source(sid):
        return await asyncio.to_thread(L.list_sessions, sid, limit)
    remote_limit = 5000 if int(limit or 0) <= 0 else max(1, min(int(limit), 5000))
    try:
        rows = await _api_get("/api/sources/" + _enc(sid) + "/sessions", {"limit": remote_limit})
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    except WecomError:
        pass
    data = await _get(PREFIX + "/sources/" + _enc(sid) + "/sessions", {"limit": remote_limit})
    items = data.get("data") if isinstance(data.get("data"), list) else []
    return [x for x in items if isinstance(x, dict)]


async def _messages_of(source_id: str, session_id: str, *, start_date: str = "", end_date: str = "", limit: int = 1000) -> list:
    sid = str(source_id or "").strip()
    cid = str(session_id or "").strip()
    params = {"limit": max(1, min(int(limit or 1000), 5000))}
    if start_date:
        params["start_date"] = str(start_date)[:10]
    if end_date:
        params["end_date"] = str(end_date)[:10]
    try:
        rows = await _api_get("/api/sources/" + _enc(sid) + "/messages/" + _enc(cid), params)
        if isinstance(rows, list):
            return [_normalize_msg(dict(x)) for x in rows if isinstance(x, dict)]
    except WecomError:
        pass
    data = await _get(PREFIX + "/sources/" + _enc(sid) + "/messages/" + _enc(cid), {
        "offset": 0,
        "limit": min(int(params["limit"]), 200),
        **{k: params[k] for k in ("start_date", "end_date") if k in params},
    })
    items = data.get("data") if isinstance(data.get("data"), list) else []
    return [_normalize_msg(dict(x)) for x in items if isinstance(x, dict)]


async def list_merged_groups(
    *,
    kind: str = "group",
    q: str = "",
    source_id: str = "",
    limit: int = 400,
    watch_only: bool = False,
) -> dict:
    srcs = (await list_sources()).get("items") or []
    want = str(source_id or "").strip()
    if want and want not in ("*", "all"):
        srcs = [s for s in srcs if str(s.get("id") or "") == want]
    allow = load_config().get("wecomChatGroups") or []
    qn = str(q or "").strip().lower()

    async def one(src: dict) -> list:
        sid = str(src.get("id") or "")
        if not sid:
            return []
        try:
            sessions = await _sessions_of(sid, 0 if watch_only else 1000)
        except WecomError:
            return []
        rows = []
        for it in sessions:
            if kind != "all" and not is_group(it):
                continue
            if watch_only and allow and not match_allowlist(it, allow):
                continue
            if qn:
                blob = (str(it.get("display_name") or "") + " " + str(it.get("username") or "")).lower()
                if qn not in blob:
                    continue
            rows.append((it, src))
        return rows

    pairs: list = []
    if srcs:
        for chunk in await asyncio.gather(*[one(s) for s in srcs]):
            pairs.extend(chunk)
    items = merge_groups(pairs)
    cap = max(1, min(int(limit or 400), 1000))
    if watch_only:
        cap = 1000
    if limit:
        items = items[:cap]
    return {"items": items, "count": len(items), "source_id": want or "*", "kind": kind}


def message_ids_of(m: dict | None) -> list[str]:
    out = []
    if not isinstance(m, dict):
        return out
    mid = str(m.get("message_id") or "").strip()
    if mid:
        out.append(mid)
    for c in m.get("copies") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("message_id") or "").strip()
        if cid and cid not in out:
            out.append(cid)
    return out


def find_message_index(merged: list, around_id: str = "", around_time: str = "") -> int:
    aid = str(around_id or "").strip()
    atime = str(around_time or "").strip()
    if aid:
        for i, m in enumerate(merged or []):
            if aid in message_ids_of(m):
                return i
    if atime:
        for i, m in enumerate(merged or []):
            if str((m or {}).get("time_text") or "") == atime:
                return i
    return -1


def slice_around(merged: list, *, limit: int = 80, around_id: str = "", around_time: str = "") -> dict | None:
    """把目标消息切进一页，并算出 from_end，便于各群记录窗口跳转。"""
    items = merged if isinstance(merged, list) else []
    n = len(items)
    if n <= 0:
        return None
    lim = max(1, min(int(limit or 80), 200))
    idx = find_message_index(items, around_id, around_time)
    if idx < 0:
        return None
    before = min(idx, max(8, lim // 4))
    start = idx - before
    end = start + lim
    if end > n:
        end = n
        start = max(0, end - lim)
    return {
        "start": start,
        "end": end,
        "from_end": n - end,
        "index": idx,
        "hit": idx - start,
    }


async def list_merged_messages(
    session_id: str,
    *,
    source_id: str = "",
    start_date: str = "",
    end_date: str = "",
    offset: int = 0,
    limit: int = 80,
    tail: bool = False,
    full: bool = False,
    from_end: int = 0,
    around_id: str = "",
    around_time: str = "",
) -> dict:
    from . import wecom_local as L
    cid = str(session_id or "").strip()
    if not cid:
        raise WecomError("BAD_REQUEST", "缺少 session_id")
    srcs = (await list_sources()).get("items") or []
    want = str(source_id or "").strip()
    if want and want not in ("*", "all"):
        srcs = [s for s in srcs if str(s.get("id") or "") == want]

    lim = max(1, min(int(limit or 80), 200))
    off = max(0, int(offset or 0))
    fe = max(0, int(from_end or 0))
    around_id = str(around_id or "").strip()
    around_time = str(around_time or "").strip()
    want_around = bool(around_id or around_time)
    window = bool(tail or fe)
    if full or want_around:
        per_src = 0
    elif window:
        per_src = fe + lim
    else:
        per_src = min(2000, max(off + lim, lim))

    async def one(src: dict) -> tuple:
        sid = str(src.get("id") or "")
        trunc = False
        try:
            if L.available() and L.has_source(sid):
                msgs, trunc = await asyncio.to_thread(
                    L.read_window, sid, cid,
                    need=0 if (full or want_around) else per_src,
                    start_date=start_date, end_date=end_date,
                )
            else:
                fetch = 5000 if (full or want_around) else per_src
                msgs = await _messages_of(sid, cid, start_date=start_date, end_date=end_date, limit=fetch)
                trunc = bool(fetch and len(msgs) >= fetch)
        except WecomError:
            msgs = []
        return msgs, src, trunc

    batches = list(await asyncio.gather(*[one(s) for s in srcs])) if srcs else []
    replicas = []
    nonempty = []
    display = ""
    truncated_any = False
    read_mode = "local" if L.available() else "http"
    for msgs, src, trunc in batches:
        truncated_any = truncated_any or bool(trunc)
        n = len(msgs or [])
        replicas.append({
            "source_id": str(src.get("id") or ""),
            "kind": str(src.get("kind") or ""),
            "label": source_label(src),
            "msg_count": n,
        })
        if n:
            nonempty.append((msgs, src))
            if not display:
                for m in msgs:
                    name = str((m or {}).get("session_name") or (m or {}).get("display_name") or "").strip()
                    if name:
                        display = name
                        break
    merged = merge_messages(nonempty, cid)
    have = [r for r in replicas if int(r.get("msg_count") or 0) > 0]
    if full and not want_around:
        return {
            "items": merged,
            "count": len(merged),
            "total": len(merged),
            "offset": 0,
            "limit": len(merged),
            "session_id": cid,
            "replicas": have or replicas,
            "display_name": display,
            "readMode": read_mode,
            "hasEarlier": False,
            "hasNewer": False,
            "fromEnd": 0,
        }
    if want_around:
        sliced = slice_around(merged, limit=lim, around_id=around_id, around_time=around_time)
        if sliced:
            page = merged[sliced["start"]:sliced["end"]]
            fe2 = int(sliced["from_end"] or 0)
            return {
                "items": page,
                "count": len(page),
                "total": len(merged),
                "offset": fe2,
                "limit": lim,
                "session_id": cid,
                "replicas": have or replicas,
                "display_name": display,
                "readMode": read_mode,
                "hasEarlier": sliced["start"] > 0,
                "hasNewer": fe2 > 0,
                "fromEnd": fe2,
                "aroundHit": True,
                "aroundIndex": sliced["hit"],
            }
        window = True
        fe = 0
    if window:
        end = len(merged) - fe
        if end < 0:
            end = 0
        start = max(0, end - lim)
        page = merged[start:end]
        has_earlier = bool(truncated_any or start > 0)
        return {
            "items": page,
            "count": len(page),
            "total": fe + len(page) + (1 if has_earlier else 0),
            "offset": fe,
            "limit": lim,
            "session_id": cid,
            "replicas": have or replicas,
            "display_name": display,
            "readMode": read_mode,
            "hasEarlier": has_earlier,
            "hasNewer": fe > 0,
            "fromEnd": fe,
        }
    page = merged[off: off + lim]
    return {
        "items": page,
        "count": len(page),
        "total": len(merged),
        "offset": off,
        "limit": lim,
        "session_id": cid,
        "replicas": have or replicas,
        "display_name": display,
        "readMode": read_mode,
        "hasEarlier": off + lim < len(merged),
        "hasNewer": off > 0,
        "fromEnd": 0,
    }


def merge_copy_peers(copies: list | None, peers: list | None) -> list:
    """把拥有该会话的其它电脑补进 copies，沿用已有 message_id 去查缓存。"""
    out = []
    seen = set()
    mids = []
    cid = ""
    for raw in copies or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("source_id") or "").strip()
        cid = cid or str(raw.get("session_id") or "").strip()
        try:
            mid = int(raw.get("message_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        if mid and mid not in mids:
            mids.append(mid)
        if not sid or sid in seen:
            continue
        seen.add(sid)
        row = dict(raw)
        row["source_id"] = sid
        if cid and not str(row.get("session_id") or "").strip():
            row["session_id"] = cid
        if mid:
            row["message_id"] = mid
        out.append(row)
    fallback_mid = mids[0] if mids else 0
    for p in peers or []:
        if not isinstance(p, dict):
            continue
        sid = str(p.get("source_id") or p.get("id") or "").strip()
        if not sid or sid in seen or not fallback_mid:
            continue
        seen.add(sid)
        out.append({
            "source_id": sid,
            "source_label": str(p.get("source_label") or p.get("label") or ""),
            "kind": str(p.get("kind") or ""),
            "message_id": fallback_mid,
            "session_id": cid or str(p.get("session_id") or ""),
        })
    return out


async def session_peers(session_id: str) -> list[dict]:
    """拥有该会话的电脑（本地有会话缓存的源；无本地目录的远端源仍列入）。"""
    cid = str(session_id or "").strip()
    if not cid:
        return []
    from . import wecom_local as L
    srcs = (await list_sources()).get("items") or []
    out = []
    for src in srcs:
        sid = str(src.get("id") or "").strip()
        if not sid:
            continue
        has = True
        if L.available() and L.has_source(sid):
            has = await asyncio.to_thread(L.source_has_session, sid, cid)
        if not has:
            continue
        out.append({
            "source_id": sid,
            "kind": str(src.get("kind") or ""),
            "label": source_label(src),
        })
    return out


def _pack_try(got: dict) -> dict:
    label = str(got.get("source_label") or got.get("source_id") or "")
    return {
        "source_id": got.get("source_id"),
        "label": label,
        "kind": str(got.get("kind") or ""),
        "detail": str(got.get("detail") or ""),
    }


async def fetch_attachment_any(copies: list, *, wait_s: float = 50.0) -> dict:
    jobs = []
    seen = set()
    cid = ""
    for raw in copies or []:
        if isinstance(raw, dict) and not cid:
            cid = str(raw.get("session_id") or "").strip()
    if cid:
        copies = merge_copy_peers(copies, await session_peers(cid))
    for raw in copies or []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("source_id") or "").strip()
        try:
            mid = int(raw.get("message_id") or 0)
        except (TypeError, ValueError):
            mid = 0
        cid = str(raw.get("session_id") or "").strip()
        if not sid or mid <= 0 or (sid, mid) in seen:
            continue
        seen.add((sid, mid))
        jobs.append((sid, mid, cid, str(raw.get("source_label") or source_label({"id": sid, "kind": raw.get("kind")}))))
    jobs.sort(key=lambda j: (0 if j[0] == "local" or str(j[3]) == "本机" else 1, j[0], j[1]))
    uniq = []
    seen_src = set()
    for j in jobs:
        if j[0] in seen_src:
            continue
        seen_src.add(j[0])
        uniq.append(j)
    jobs = uniq

    async def one(sid, mid, cid, label):
        try:
            got = await fetch_attachment(sid, mid, cid)
        except WecomError as e:
            got = {"kind": "error", "status": e.status or 502, "detail": e.message}
        got["source_id"] = sid
        got["source_label"] = label
        got["message_id"] = mid
        return got

    if not jobs:
        return {"kind": "missing", "status": 404, "detail": "没有可查询的电脑副本", "tried": []}

    tried_all: list = []
    missing_all: list = []
    pending: list = []
    for sid, mid, cid, label in jobs:
        got = await one(sid, mid, cid, label)
        row = _pack_try(got)
        tried_all.append(row)
        kind = row["kind"]
        if kind == "file":
            got["tried"] = tried_all
            got["hit_label"] = label
            return got
        if kind == "pending":
            pending.append((sid, mid, cid, label))
            continue
        missing_all.append(label)

    if pending:
        deadline = time.monotonic() + max(0.0, float(wait_s or 0))
        delay = 1.5
        while pending and time.monotonic() < deadline:
            await asyncio.sleep(delay)
            delay = min(4.0, delay + 0.5)
            nxt = []
            for sid, mid, cid, label in pending:
                got = await one(sid, mid, cid, label)
                row = _pack_try(got)
                tried_all.append(row)
                kind = row["kind"]
                if kind == "file":
                    got["tried"] = tried_all
                    got["hit_label"] = label
                    return got
                if kind == "pending":
                    nxt.append((sid, mid, cid, label))
                else:
                    missing_all.append(label)
            pending = nxt
        if pending:
            names = "、".join(p[3] for p in pending)
            return {
                "kind": "pending",
                "status": 202,
                "detail": "已通知「" + names + "」上的企业微信助手回传文件，但还没送到。请确认该电脑助手在线，并在企业微信里打开过此文件。",
                "tried": tried_all,
                "pending": [p[3] for p in pending],
                "filename": "",
            }

    names = "、".join(missing_all) or "各电脑"
    return {
        "kind": "missing",
        "status": 404,
        "detail": names + " 均未缓存该文件（需在对应电脑的企业微信中打开过）",
        "tried": tried_all,
        "filename": "",
    }


async def list_sessions(source_id: str, *, kind: str = "group", q: str = "", limit: int = 200) -> dict:
    sid = str(source_id or "").strip()
    if not sid:
        raise WecomError("BAD_REQUEST", "缺少 source_id")
    lim = max(1, min(int(limit or 200), 1000))
    data = await _get(PREFIX + "/sources/" + _enc(sid) + "/sessions", {"limit": lim})
    items = data.get("data") if isinstance(data.get("data"), list) else []
    allow = load_config().get("wecomChatGroups") or []
    qn = str(q or "").strip().lower()
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if kind != "all" and not is_group(it):
            continue
        if not match_allowlist(it, allow):
            continue
        if qn:
            blob = (str(it.get("display_name") or "") + " " + str(it.get("username") or "")).lower()
            if qn not in blob:
                continue
        row = dict(it)
        row["is_group"] = is_group(it)
        row["source_id"] = sid
        out.append(row)
    return {"items": out, "count": len(out), "source_id": sid, "kind": kind}


async def list_messages(
    source_id: str,
    session_id: str,
    *,
    start_date: str = "",
    end_date: str = "",
    offset: int = 0,
    limit: int = 100,
) -> dict:
    sid = str(source_id or "").strip()
    cid = str(session_id or "").strip()
    if not sid or not cid:
        raise WecomError("BAD_REQUEST", "缺少 source_id 或 session_id")
    params = {
        "offset": max(0, int(offset or 0)),
        "limit": max(1, min(int(limit or 100), 200)),
    }
    if start_date:
        params["start_date"] = str(start_date)[:10]
    if end_date:
        params["end_date"] = str(end_date)[:10]
    data = await _get(PREFIX + "/sources/" + _enc(sid) + "/messages/" + _enc(cid), params)
    items = data.get("data") if isinstance(data.get("data"), list) else []
    items = [_normalize_msg(dict(x)) for x in items if isinstance(x, dict)]
    return {
        "items": items,
        "count": int(data.get("count") or len(items)),
        "total": int(data.get("total") or len(items)),
        "offset": int(data.get("offset") or params["offset"]),
        "limit": int(data.get("limit") or params["limit"]),
        "source_id": sid,
        "session_id": cid,
    }


async def search(q: str, *, source_id: str = "", session_id: str = "", limit: int = 50) -> dict:
    from . import wecom_local as L
    qn = str(q or "").strip()
    if not qn:
        raise WecomError("BAD_REQUEST", "请输入关键词")
    if L.available() and str(session_id or "").strip():
        rows = await asyncio.to_thread(
            L.search_session, qn, str(session_id).strip(),
            source_id=str(source_id or ""), limit=limit,
        )
        items = [_normalize_msg(dict(x)) for x in rows if isinstance(x, dict)]
        return {"items": items, "count": len(items), "q": qn, "readMode": "local"}
    params = {"q": qn[:80], "limit": max(1, min(int(limit or 50), 200))}
    if source_id:
        params["source_id"] = str(source_id)
    if session_id:
        params["session_id"] = str(session_id)
    try:
        rows = await _api_get("/api/search", params)
        if isinstance(rows, list):
            items = [_normalize_msg(dict(x)) for x in rows if isinstance(x, dict)]
            return {"items": items, "count": len(items), "q": qn}
    except WecomError:
        pass
    data = await _get(PREFIX + "/search", params)
    items = data.get("data") if isinstance(data.get("data"), list) else []
    items = [_normalize_msg(dict(x)) for x in items if isinstance(x, dict)]
    return {"items": items, "count": int(data.get("count") or len(items)), "q": qn}


def _filename_from_cd(cd: str, fallback: str = "") -> str:
    s = str(cd or "")
    m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?", s, re.I)
    if not m:
        return fallback
    return unquote(m.group(1) or m.group(2) or fallback) or fallback


def _attachment_result(r: httpx.Response) -> dict:
    ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    fn = _filename_from_cd(r.headers.get("content-disposition") or "", "")
    if r.status_code == 202:
        try:
            body = r.json()
        except Exception:
            body = {}
        return {
            "kind": "pending",
            "status": 202,
            "job_id": str((body or {}).get("job_id") or ""),
            "detail": str((body or {}).get("detail") or "正在等待远端助手回传文件"),
            "filename": fn,
        }
    if r.status_code == 404:
        try:
            body = r.json()
            detail = str((body or {}).get("detail") or "文件不在缓存")
        except Exception:
            detail = "文件不在缓存"
        return {"kind": "missing", "status": 404, "detail": detail, "filename": fn}
    if r.status_code >= 400:
        raise _err(r.status_code, r.text)
    if not r.content:
        return {"kind": "missing", "status": 404, "detail": "空文件", "filename": fn}
    return {
        "kind": "file",
        "status": 200,
        "content": r.content,
        "filename": fn,
        "content_type": ct or "application/octet-stream",
    }


async def _attachment_http(path: str, params: dict | None = None) -> httpx.Response:
    url = _root() + path
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0), trust_env=httpx_trust_env()) as client:
        return await client.get(url, headers=_headers(False), params=params or {})


async def fetch_attachment(source_id: str, message_id: int, session_id: str = "") -> dict:
    """与 5173 相同：GET /api/sources/{id}/attachments/{mid}，本机缓存或远端助手回传。"""
    sid = str(source_id or "").strip()
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid = 0
    if not sid or mid <= 0:
        raise WecomError("BAD_REQUEST", "缺少 source_id 或 message_id")
    cid = str(session_id or "").strip()
    if sid != "local" and not cid:
        raise WecomError("BAD_REQUEST", "远端文件需要 session_id")
    params = {"session_id": cid} if cid else {}
    try:
        r = await _attachment_http("/api/sources/" + _enc(sid) + "/attachments/" + str(mid), params)
        return _attachment_result(r)
    except httpx.HTTPError as e:
        raise WecomError("NETWORK", "解析服务不可达：" + str(e)[:160])


async def stream_attachment(source_id: str, message_id: int, session_id: str = "") -> dict:
    """流式转发解析服务附件，避免大文件在本进程整包缓冲。"""
    sid = str(source_id or "").strip()
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        mid = 0
    if not sid or mid <= 0:
        raise WecomError("BAD_REQUEST", "缺少 source_id 或 message_id")
    cid = str(session_id or "").strip()
    if sid != "local" and not cid:
        raise WecomError("BAD_REQUEST", "远端文件需要 session_id")
    params = {"session_id": cid} if cid else {}
    url = _root() + "/api/sources/" + _enc(sid) + "/attachments/" + str(mid)
    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0), trust_env=httpx_trust_env())
    try:
        r = await client.send(client.build_request("GET", url, headers=_headers(False), params=params), stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        raise WecomError("NETWORK", "解析服务不可达：" + str(e)[:160])
    ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    fn = _filename_from_cd(r.headers.get("content-disposition") or "", "")
    if r.status_code != 200:
        try:
            raw = await r.aread()
        finally:
            await r.aclose()
            await client.aclose()
        class _Buf:
            def __init__(self, status, headers, content):
                self.status_code = status
                self.headers = headers
                self.content = content or b""
            @property
            def text(self):
                return self.content.decode("utf-8", "ignore")
            def json(self):
                return json.loads(self.text or "{}")
        return _attachment_result(_Buf(r.status_code, r.headers, raw))

    async def chunks():
        try:
            async for b in r.aiter_bytes(65536):
                yield b
        finally:
            await r.aclose()
            await client.aclose()

    return {
        "kind": "stream",
        "status": 200,
        "filename": fn,
        "content_type": ct or "application/octet-stream",
        "chunks": chunks,
    }


async def proxy_media(url: str) -> dict:
    u = str(url or "").strip()
    if not u.lower().startswith("http"):
        raise WecomError("BAD_REQUEST", "无效图片地址")
    target = _root() + "/api/media/proxy"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0), trust_env=httpx_trust_env()) as client:
            r = await client.get(target, headers=_headers(False), params={"url": u})
    except httpx.HTTPError as e:
        raise WecomError("NETWORK", "解析服务不可达：" + str(e)[:160])
    if r.status_code >= 400:
        raise _err(r.status_code, r.text)
    ct = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    return {"content": r.content, "content_type": ct or "image/jpeg"}
