"""config.json 读取：baseUrl / apiKey / model / reasoningEffort"""
from __future__ import annotations
import json, os, re, time, urllib.error, urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
FILL_MARK = "填入"
APP_VERSION = "2.0"

LLM_TEMPERATURE = 0.3
LLM_RETRIES = 4
LLM_CONNECT_TIMEOUT = 15.0
LLM_TIMEOUT_DEFAULT = 900.0
LLM_TIMEOUT_CLASSIFY = 180.0
LLM_TIMEOUT_MATCH = 240.0
LLM_TIMEOUT_SECTION = 360.0
LLM_STREAM = False
PLAN_CONCURRENCY = 2
EFFORT_LABELS = {"low": "短（low）", "medium": "中（medium）", "high": "长（high）"}

def llm_api_base(url: str) -> str:
    """统一成 OpenAI 兼容根：https://cdn.12ai.org → https://cdn.12ai.org/v1"""
    u = str(url or "").strip().rstrip("/")
    if not u:
        return ""
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")].rstrip("/")
    if not re.search(r"/v1$", u):
        u = u + "/v1"
    return u

def load_config() -> dict:
    c: dict = {}
    try:
        c = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        c = {}
    base = llm_api_base(str(c.get("baseUrl") or ""))
    key = str(c.get("apiKey") or "")
    model = str(c.get("model") or "")
    effort = str(c.get("reasoningEffort") or "medium").lower()
    configured = bool(base and key and model) and not any(FILL_MARK in x for x in (base, key, model))
    pool = c.get("pool") if isinstance(c.get("pool"), dict) else {}
    pool_base = str(pool.get("baseUrl") or c.get("poolBaseUrl") or "").rstrip("/")
    pool_key = str(pool.get("apiKey") or c.get("poolApiKey") or "")
    pool_mode = str(pool.get("mode") or "all").strip() or "all"
    pool_ok = bool(pool_base and pool_key) and not any(FILL_MARK in x for x in (pool_base, pool_key))
    raw_models = c.get("models") if isinstance(c.get("models"), list) else []
    return {
        "baseUrl": base.rstrip("/"),
        "apiKey": key,
        "model": model,
        "reasoningEffort": effort if effort in ("low", "medium", "high") else "",
        "configured": configured,
        "modelsRaw": raw_models,
        "poolBaseUrl": pool_base,
        "poolApiKey": pool_key,
        "poolMode": pool_mode,
        "poolConfigured": pool_ok,
    }

DATA_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = DATA_DIR / "tasks"
BATCHES_DIR = DATA_DIR / "batches"
STATIC_DIR = DATA_DIR / "static"
SCRIPTS_DIR = DATA_DIR / "scripts"
RULES_DIR = DATA_DIR / "rules"
PYEXE = r"C:\Users\1\miniconda3\envs\work\python.exe"

def atomic_replace(tmp, dst, retries: int = 5, delay: float = 0.08):
    """Windows 下 meta.json 偶发 PermissionError，短重试后再换名。"""
    last = None
    for _ in range(retries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError as e:
            last = e
            time.sleep(delay)
    raise last

_SKIP_MODEL = re.compile(r"imagine|video|image|tts|whisper|embed|moderation|realtime|audio", re.I)
_remote_models: dict = {}
_MODEL_LABELS = {
    "grok-4.6": "Grok",
    "gemini-3.7-flash": "Gemini",
}

def engine_label() -> str:
    cfg = load_config()
    return ("大模型直连 · " + cfg["model"]) if cfg["configured"] else "⚠ 未配置：请填写 config.json"

def _effort_for(mid: str, cfg: dict, explicit=None) -> str:
    if explicit is not None:
        e = str(explicit or "").lower()
        return e if e in ("low", "medium", "high") else ""
    m = str(mid or "")
    if _SKIP_MODEL.search(m) or re.search(r"non-reasoning", m, re.I):
        return ""
    if re.search(r"grok-4|reasoning|multi-agent", m, re.I):
        return cfg.get("reasoningEffort") or "medium"
    return ""

def _remote_chat_ids(base_url: str, api_key: str) -> list:
    base_url = (base_url or "").rstrip("/")
    if not base_url or not api_key or FILL_MARK in base_url or FILL_MARK in api_key:
        return []
    now = time.time()
    hit = _remote_models.get(base_url)
    if hit and now - hit["t"] < 120:
        return list(hit["ids"])
    ids = []
    try:
        req = urllib.request.Request(
            llm_api_base(base_url) + "/models",
            headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        rows = data.get("data") if isinstance(data, dict) else data
        seen = set()
        for it in rows or []:
            mid = str((it.get("id") if isinstance(it, dict) else it) or "").strip()
            if not mid or mid in seen or _SKIP_MODEL.search(mid):
                continue
            seen.add(mid)
            ids.append(mid)
    except Exception:
        ids = list((hit or {}).get("ids") or [])
    _remote_models[base_url] = {"t": now, "ids": ids}
    return list(ids)

def catalog_entries() -> list:
    """固定目录：Grok 用顶层网关；Gemini 等可在 models 项里单独写地址/密钥。"""
    cfg = load_config()
    rows, seen = [], set()

    def push(raw):
        if isinstance(raw, str):
            raw = {"id": raw}
        if not isinstance(raw, dict):
            return
        mid = str(raw.get("id") or "").strip()
        if not mid or FILL_MARK in mid or mid in seen:
            return
        seen.add(mid)
        if str(raw.get("baseUrl") or "").strip():
            base = llm_api_base(str(raw.get("baseUrl")))
        else:
            base = llm_api_base(cfg.get("baseUrl") or "")
        if "apiKey" in raw:
            key = str(raw.get("apiKey") or "")
        else:
            key = str(cfg.get("apiKey") or "")
        effort = raw.get("reasoningEffort") if "reasoningEffort" in raw else None
        if "stream" in raw:
            stream = bool(raw.get("stream"))
        else:
            stream = bool(LLM_STREAM)
        timeout = 0.0
        if raw.get("timeoutSec") not in (None, ""):
            try:
                timeout = float(raw.get("timeoutSec"))
            except (TypeError, ValueError):
                timeout = 0.0
        ready = bool(base and key) and FILL_MARK not in base and FILL_MARK not in key
        rows.append({
            "id": mid,
            "model": mid,
            "label": str(raw.get("label") or _MODEL_LABELS.get(mid) or mid).strip() or mid,
            "baseUrl": base,
            "apiKey": key,
            "reasoningEffort": _effort_for(mid, cfg, effort),
            "stream": stream,
            "timeoutSec": timeout,
            "ready": ready,
        })

    for raw in cfg.get("modelsRaw") or []:
        push(raw)
    if cfg.get("model"):
        push({"id": cfg.get("model")})
    return rows

def llm_profiles() -> list:
    """可选模型（含密钥，仅后端使用）。"""
    return [p for p in catalog_entries() if p.get("ready")]

def resolve_llm(model_id=None) -> dict:
    cfg = load_config()
    mid = str(model_id or "").strip()
    catalog = catalog_entries()
    by_all = {p["id"]: p for p in catalog}
    if mid and mid in by_all:
        hit = by_all[mid]
        if not hit.get("ready"):
            raise ValueError("未配置 " + (hit.get("label") or mid) + "：请在 config.json 填写对应 apiKey")
        return hit
    profs = [p for p in catalog if p.get("ready")]
    if profs:
        default_id = cfg.get("model") if cfg.get("model") in {p["id"] for p in profs} else profs[0]["id"]
        return {p["id"]: p for p in profs}[default_id]
    raise ValueError("未配置大模型 API：请编辑 config.json")

def public_models() -> list:
    cfg = load_config()
    default_id = cfg.get("model") or ""
    out = []
    for p in catalog_entries():
        out.append({
            "id": p["id"],
            "label": p["label"],
            "endpoint": _host_of(p.get("baseUrl") or ""),
            "reasoningEffort": p.get("reasoningEffort") or "",
            "reasoningLabel": EFFORT_LABELS.get(p.get("reasoningEffort") or "", "关闭"),
            "stream": bool(p.get("stream")),
            "timeoutSec": int(p.get("timeoutSec") or 0),
            "ready": bool(p.get("ready")),
            "default": p["id"] == default_id,
        })
    return out

def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    u = urlparse(str(url or "").strip())
    if not u.hostname:
        return ""
    return u.hostname + ((":" + str(u.port)) if u.port else "")

def public_config() -> dict:
    """前端可展示的运行参数（不含密钥）。"""
    cfg = load_config()
    effort = cfg.get("reasoningEffort") or ""
    models = public_models()
    default_id = next((m["id"] for m in models if m.get("default")), (models[0]["id"] if models else cfg.get("model") or ""))
    return {
        "version": APP_VERSION,
        "configured": any(m.get("ready") for m in models) or bool(cfg.get("configured")),
        "models": models,
        "engines": [{"id": m["id"], "label": m["label"]} for m in models] or [{"id": "api", "label": engine_label()}],
        "llm": {
            "model": default_id,
            "endpoint": _host_of(cfg.get("baseUrl") or ""),
            "stream": bool(LLM_STREAM),
            "reasoningEffort": effort,
            "reasoningLabel": EFFORT_LABELS.get(effort, "关闭"),
            "temperature": LLM_TEMPERATURE,
            "retries": LLM_RETRIES,
            "connectTimeoutSec": int(LLM_CONNECT_TIMEOUT),
            "timeouts": {
                "classifySec": int(LLM_TIMEOUT_CLASSIFY),
                "matchSec": int(LLM_TIMEOUT_MATCH),
                "sectionPlanSec": int(LLM_TIMEOUT_SECTION),
                "defaultSec": int(LLM_TIMEOUT_DEFAULT),
            },
            "planConcurrency": PLAN_CONCURRENCY,
        },
        "pool": {
            "configured": bool(cfg.get("poolConfigured")),
            "mode": cfg.get("poolMode") or "all",
            "endpoint": _host_of(cfg.get("poolBaseUrl") or "") if cfg.get("poolConfigured") else "",
        },
    }
