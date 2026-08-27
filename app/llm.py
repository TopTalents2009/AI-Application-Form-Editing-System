"""OpenAI 兼容 Chat 封装（httpx 异步），行为与 Node 版逐条对齐"""
from __future__ import annotations
import json, re, asyncio
import httpx
from .config import load_config

FENCE = chr(96) * 3

class LlmError(Exception):
    pass

def _trunc(s: str, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"

async def chat(messages, *, json_mode: bool = False, timeout_s: float = 900.0):
    cfg = load_config()
    if not cfg["configured"]:
        raise LlmError("未配置大模型 API：请编辑 agent修改申报书/config.json 填入 baseUrl / apiKey / model")
    url = cfg["baseUrl"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + cfg["apiKey"]}
    last_err = None
    with_effort = bool(cfg["reasoningEffort"])
    for attempt in range(1, 5):
        payload = {"model": cfg["model"], "messages": messages, "temperature": 0.3}
        if with_effort and cfg["reasoningEffort"]:
            payload["reasoning_effort"] = cfg["reasoningEffort"]  # 思考档位（用户设定 medium）
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            # 本地回环绕过系统代理（否则桩测试/本地网关会被转发导致502）
            trust = not (host in ("127.0.0.1", "localhost", "::1"))
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=15.0), trust_env=trust) as client:
                r = await client.post(url, headers=headers, json=payload)
            if r.status_code >= 400:
                raise LlmError(f"HTTP {r.status_code}: {_trunc(r.text, 300)}")
            data = r.json()
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            return {"content": msg.get("content") or "", "usage": data.get("usage") or {}}
        except Exception as e:  # noqa: BLE001
            last_err = e
            m = str(e)
            if with_effort and m.startswith("HTTP 400") and re.search("reason|thinking|effort|未知|unknown|unexpected|invalid", m, re.I):
                with_effort = False  # 服务端不认思考档位参数：去参立即重试
                continue
            if m.startswith("HTTP 4") and "HTTP 429" not in m:
                break  # 其余4xx(除429)不重试
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
