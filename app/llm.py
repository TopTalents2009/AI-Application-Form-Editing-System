"""OpenAI 兼容 Chat 封装（httpx 异步），默认流式"""
from __future__ import annotations
import json, re, asyncio, time
import httpx
from urllib.parse import urlparse
from .config import (
    LLM_TIMEOUT_DEFAULT, LLM_CONNECT_TIMEOUT, LLM_TEMPERATURE, LLM_RETRIES, LLM_STREAM,
    llm_api_base, resolve_llm, httpx_trust_env, catalog_entries,
)

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

def _explain(e: Exception, url: str, timeout_s: float) -> LlmError:
    host = urlparse(url).hostname or ""
    where = host or "模型服务"
    if isinstance(e, (asyncio.TimeoutError, httpx.TimeoutException)):
        return LlmError(f"LLM 调用超时（{int(timeout_s)}s，{where}）")
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError)):
        inner = (str(e) or "").strip() or type(e).__name__
        return LlmError(f"无法连接 {where}：{inner}")
    msg = (str(e) or "").strip() or type(e).__name__
    low = msg.lower()
    if "accountoverdue" in low or "overdue balance" in low:
        return LlmError("火山账户欠费（AccountOverdueError），请到火山引擎控制台充值后再调用")
    if msg.startswith("HTTP 401") or "invalid token" in low or "incorrect api key" in low:
        return LlmError("密钥无效或未授权（HTTP 401）")
    if msg.startswith("HTTP 502") or msg.startswith("HTTP 503") or msg.startswith("HTTP 504"):
        extra = msg.split(":", 1)[-1].strip()
        return LlmError(f"{where} 返回 {msg[:8].strip()}" + ("（空响应）" if not extra else "：" + extra[:180]))
    if isinstance(e, LlmError):
        return e
    return LlmError(type(e).__name__ + ": " + msg[:300])


async def chat(messages, *, json_mode: bool = False, timeout_s: float = LLM_TIMEOUT_DEFAULT, model=None, retries=None, apply_profile_timeout: bool = True):
    try:
        prof = resolve_llm(model)
    except ValueError as e:
        raise LlmError(str(e))
    url = llm_api_base(prof["baseUrl"]) + "/chat/completions"
    if apply_profile_timeout and prof.get("timeoutSec"):
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
    trust = httpx_trust_env()
    timeout = httpx.Timeout(timeout_s, connect=LLM_CONNECT_TIMEOUT)
    n_try = int(retries) if retries is not None else LLM_RETRIES
    if n_try < 1:
        n_try = 1
    for attempt in range(1, n_try + 1):
        temp = prof.get("temperature")
        try:
            temp = float(temp) if temp not in (None, "") else LLM_TEMPERATURE
        except (TypeError, ValueError):
            temp = LLM_TEMPERATURE
        payload = {"model": prof["model"], "messages": messages, "temperature": temp, "stream": use_stream}
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
                    try:
                        data = r.json()
                    except json.JSONDecodeError:
                        data = _decode_json_object(r.text or "")
                    content = _choice_text(data)
                    usage = (data.get("usage") or {}) if isinstance(data, dict) else {}
            return {"content": content or "", "usage": usage or {}}
        except Exception as e:  # noqa: BLE001
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
            if isinstance(e, (asyncio.TimeoutError, httpx.TimeoutException)):
                break
            if m.startswith("HTTP 4") and "HTTP 429" not in m:
                break
            await asyncio.sleep(5 * min(attempt, 2))
    raise _explain(last_err or LlmError("LLM 调用失败"), url, timeout_s)


async def probe_models(model_id=None) -> dict:
    """对已配置模型发一条极短 chat，返回不含密钥的连通性结果。"""
    want = str(model_id or "").strip()
    rows = catalog_entries()
    if want:
        hit = [p for p in rows if p.get("id") == want]
        if not hit:
            from .config import model_family
            fam = model_family(want)
            hit = [p for p in rows if model_family(p.get("id") or "") == fam]
        rows = hit
    if not rows:
        return {"ok": False, "error": "没有可检测的模型", "results": []}
    out = []
    for p in rows:
        item = {
            "id": p.get("id") or "",
            "label": p.get("label") or p.get("id") or "",
            "ok": False,
            "ms": 0,
            "error": "",
            "detail": "",
        }
        if not p.get("ready"):
            item["error"] = "未配置密钥或请求地址"
            out.append(item)
            continue
        t0 = time.monotonic()
        try:
            r = await chat(
                [{"role": "user", "content": "只回复一个字：通"}],
                json_mode=False,
                timeout_s=25,
                model=p.get("id"),
                retries=1,
                apply_profile_timeout=False,
            )
            item["ms"] = int((time.monotonic() - t0) * 1000)
            text = (r.get("content") or "").strip()
            item["ok"] = True
            item["detail"] = ("已连通，回复「" + text[:24] + "」") if text else "已连通"
        except Exception as e:
            item["ms"] = int((time.monotonic() - t0) * 1000)
            item["ok"] = False
            item["error"] = str(_explain(e, llm_api_base(p.get("baseUrl") or "") + "/chat/completions", 25))[:240]
        out.append(item)
    return {"ok": all(x.get("ok") for x in out), "results": out}

_CTRL_IN_JSON = {"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _escape_ctrl_in_json_strings(s: str) -> str:
    """JSON 字符串字面量里的裸控制字符（\\n/\\t/\\x0b 等）转成合法转义；字面量外的换行不动。"""
    out = []
    in_str = False
    esc = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue
        if esc:
            out.append(ch)
            esc = False
            i += 1
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            i += 1
            continue
        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue
        o = ord(ch)
        if o < 32:
            if ch == "\r" and i + 1 < n and s[i + 1] == "\n":
                out.append("\\n")
                i += 2
                continue
            out.append(_CTRL_IN_JSON.get(ch) or ("\\u%04x" % o))
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _decode_json_object(s: str):
    last = None
    repaired = _escape_ctrl_in_json_strings(s)
    for cand in (s, repaired) if repaired != s else (s,):
        a = cand.find("{")
        if a < 0:
            continue
        for strict in (True, False):
            try:
                obj, _ = json.JSONDecoder(strict=strict).raw_decode(cand, a)
                return obj
            except json.JSONDecodeError as e:
                last = e
    if (s or "").find("{") < 0:
        raise json.JSONDecodeError("未找到 JSON 对象", s or "", 0)
    raise last or json.JSONDecodeError("JSON 解析失败", s or "", 0)


def extract_json(text: str):
    s = str(text or "").strip().lstrip("\ufeff")
    fence = chr(96) * 3
    s = re.sub("^" + fence + "(?:json)?", "", s, flags=re.I).strip()
    if s.endswith(fence):
        s = s[: -len(fence)].strip()
    return _decode_json_object(s)

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
