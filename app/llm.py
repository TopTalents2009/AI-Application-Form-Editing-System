"""OpenAI 兼容 Chat 封装（httpx 异步），默认流式"""
from __future__ import annotations
import json, re, asyncio
import httpx
from urllib.parse import urlparse
from .config import LLM_TIMEOUT_DEFAULT, LLM_CONNECT_TIMEOUT, LLM_TEMPERATURE, LLM_RETRIES, LLM_STREAM, llm_api_base, resolve_llm

FENCE = chr(96) * 3

class LlmError(Exception):
    pass

def _trunc(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"

def _as_text(piece) -> str:
    if piece is None:
        return ""
    if isinstance(piece, str):
        return piece
    if isinstance(piece, list):
        out = []
        for x in piece:
            if isinstance(x, dict):
                out.append(str(x.get("text") or x.get("content") or ""))
            else:
                out.append(str(x or ""))
        return "".join(out)
    if isinstance(piece, dict):
        return str(piece.get("text") or piece.get("content") or "")
    return str(piece)

def _choice_text(obj: dict) -> str:
    ch = ((obj.get("choices") or [{}])[0]) if isinstance(obj, dict) else {}
    delta = ch.get("delta") or {}
    msg = ch.get("message") or {}
    return (
        _as_text(delta.get("content"))
        or _as_text(msg.get("content"))
        or _as_text(delta.get("reasoning_content"))
        or _as_text(msg.get("reasoning_content"))
        or ""
    )

async def _read_sse(resp: httpx.Response) -> tuple[str, dict]:
    parts, usage, buf = [], {}, ""
    async for chunk in resp.aiter_text():
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if not line.lower().startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return "".join(parts), usage
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("usage"):
                usage = obj.get("usage") or usage
            piece = _choice_text(obj)
            if piece:
                parts.append(piece)
            err = obj.get("error")
            if err:
                raise LlmError(_trunc(str(err), 300))
    leftover = buf.strip()
    if leftover.lower().startswith("data:"):
        leftover = leftover[5:].strip()
    if leftover and leftover != "[DONE]":
        try:
            obj = json.loads(leftover)
            if isinstance(obj, dict):
                if obj.get("usage"):
                    usage = obj.get("usage") or usage
                piece = _choice_text(obj)
                if piece:
                    parts.append(piece)
        except json.JSONDecodeError:
            pass
    return "".join(parts), usage

async def chat(messages, *, json_mode: bool = False, timeout_s: float = LLM_TIMEOUT_DEFAULT, model=None):
    try:
        prof = resolve_llm(model)
    except ValueError as e:
        raise LlmError(str(e))
    url = llm_api_base(prof["baseUrl"]) + "/chat/completions"
    if prof.get("timeoutSec"):
        timeout_s = float(prof["timeoutSec"])
    use_stream = bool(prof.get("stream")) if "stream" in prof else bool(LLM_STREAM)
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + prof["apiKey"],
        "Accept": "text/event-stream" if use_stream else "application/json",
    }
    last_err = None
    with_effort = bool(prof.get("reasoningEffort"))
    with_json = bool(json_mode)
    with_stream_opts = True
    host = urlparse(url).hostname or ""
    trust = not (host in ("127.0.0.1", "localhost", "::1"))
    timeout = httpx.Timeout(timeout_s, connect=LLM_CONNECT_TIMEOUT)
    for attempt in range(1, LLM_RETRIES + 1):
        payload = {"model": prof["model"], "messages": messages, "temperature": LLM_TEMPERATURE, "stream": use_stream}
        if use_stream and with_stream_opts:
            payload["stream_options"] = {"include_usage": True}
        if with_effort and prof.get("reasoningEffort"):
            payload["reasoning_effort"] = prof["reasoningEffort"]
        if with_json:
            payload["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=trust) as client:
                if use_stream:
                    async with client.stream("POST", url, headers=headers, json=payload) as r:
                        if r.status_code >= 400:
                            body = (await r.aread()).decode("utf-8", "replace")
                            raise LlmError(f"HTTP {r.status_code}: {_trunc(body, 300)}")
                        content, usage = await asyncio.wait_for(_read_sse(r), timeout=timeout_s)
                else:
                    r = await client.post(url, headers={**headers, "Accept": "application/json"}, json=payload)
                    if r.status_code >= 400:
                        raise LlmError(f"HTTP {r.status_code}: {_trunc(r.text, 300)}")
                    data = r.json()
                    content = _choice_text(data)
                    usage = data.get("usage") or {}
            return {"content": content or "", "usage": usage or {}}
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (asyncio.TimeoutError, httpx.TimeoutException)):
                last_err = LlmError(f"LLM 调用超时（{int(timeout_s)}s）")
                break
            last_err = e
            m = str(e)
            if with_effort and m.startswith("HTTP 400") and re.search("reason|thinking|effort|未知|unknown|unexpected|invalid", m, re.I):
                with_effort = False
                continue
            if with_json and m.startswith("HTTP 400") and re.search("json|response_format|schema", m, re.I):
                with_json = False
                continue
            if use_stream and with_stream_opts and m.startswith("HTTP 400") and re.search("stream_options|include_usage", m, re.I):
                with_stream_opts = False
                continue
            if use_stream and m.startswith("HTTP 400") and re.search("stream", m, re.I):
                use_stream = False
                continue
            if m.startswith("HTTP 4") and "HTTP 429" not in m:
                break
            await asyncio.sleep(5 * min(attempt, 2))
    raise last_err or LlmError("LLM 调用失败")

def extract_json(text: str):
    s = str(text or "").strip()
    fence = chr(96) * 3
    s = re.sub("^" + fence + "(?:json)?", "", s).strip()
    if s.endswith(fence):
        s = s[: -len(fence)]
    a = s.find("{")
    if a < 0:
        raise json.JSONDecodeError("未找到 JSON 对象", s, 0)
    obj, _ = json.JSONDecoder().raw_decode(s, a)
    return obj

def now_str() -> str:
    from datetime import datetime
    d = datetime.now()
    return f"{d.year}/{d.month:02d}/{d.day:02d} {d.hour:02d}:{d.minute:02d}:{d.second:02d}"

def created_key(s):
    try:
        date, tm = str(s or "").strip().split()
        y, m, d = [int(x) for x in date.split("/")]
        hh, mm, ss = [int(x) for x in tm.split(":")]
        return (y, m, d, hh, mm, ss)
    except Exception:
        return (0, 0, 0, 0, 0, 0)
