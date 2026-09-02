"""config.json 读取：baseUrl / apiKey / model / reasoningEffort"""
from __future__ import annotations
import json, os, re, time, urllib.error, urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.default.json"
FILL_MARK = "填入"
APP_VERSION = "2.3"

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
    """统一成 OpenAI 兼容根。已带 /v1、/v3、/api/v3 的保持原样（火山方舟是 /api/v3）。"""
    u = str(url or "").strip().rstrip("/")
    if not u:
        return ""
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")].rstrip("/")
    if re.search(r"/v\d+$", u):
        return u
    return u + "/v1"

def _usable_secret(val) -> str:
    s = str(val or "").strip().strip('"').strip("'")
    if not s or FILL_MARK in s:
        return ""
    return s

def _read_config_file() -> tuple[dict, str]:
    if not CONFIG_PATH.exists():
        return {}, "找不到 config.json"
    data = CONFIG_PATH.read_bytes()
    last = "无法解析 config.json"
    for enc in ("utf-8-sig", "utf-8", "utf-16", "gbk"):
        try:
            obj = json.loads(data.decode(enc))
            if isinstance(obj, dict):
                return obj, ""
            return {}, "config.json 根节点必须是对象"
        except Exception as e:
            last = str(e) or last
    return {}, last

def load_config() -> dict:
    c, err = _read_config_file()
    base = llm_api_base(str(c.get("baseUrl") or ""))
    key = _usable_secret(c.get("apiKey") or c.get("api_key"))
    model = str(c.get("model") or "")
    effort = str(c.get("reasoningEffort") or "medium").lower()
    configured = bool(base and key and model) and FILL_MARK not in model
    pool = c.get("pool") if isinstance(c.get("pool"), dict) else {}
    pool_base = str(pool.get("baseUrl") or c.get("poolBaseUrl") or "").rstrip("/")
    pool_key = _usable_secret(pool.get("apiKey") or pool.get("api_key") or c.get("poolApiKey"))
    pool_mode = str(pool.get("mode") or "all").strip() or "all"
    pool_ok = bool(pool_base and pool_key)
    papers = c.get("papers") if isinstance(c.get("papers"), dict) else {}
    papers_base = str(papers.get("baseUrl") or c.get("papersBaseUrl") or "").rstrip("/")
    papers_key = _usable_secret(papers.get("apiKey") or papers.get("api_key") or c.get("papersApiKey"))
    if papers_key and not papers_base:
        papers_base = "http://192.168.2.8:8000"
    papers_ok = bool(papers_base and papers_key)
    mysql = c.get("mysql") if isinstance(c.get("mysql"), dict) else {}
    mysql_host = str(mysql.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        mysql_port = int(mysql.get("port") or 3306)
    except (TypeError, ValueError):
        mysql_port = 3306
    mysql_user = str(mysql.get("user") or mysql.get("username") or "root").strip() or "root"
    mysql_password = str(mysql.get("password") or "")
    mysql_db = str(mysql.get("database") or "shenbaoshu").strip() or "shenbaoshu"
    mysql_ok = bool(mysql_host and mysql_user and mysql_db)
    raw_models = c.get("models") if isinstance(c.get("models"), list) else []
    client_inbox = str(c.get("clientInbox") or "").strip()
    return {
        "baseUrl": base.rstrip("/"),
        "apiKey": key,
        "geminiApiKey": _usable_secret(c.get("geminiApiKey") or c.get("gemini_api_key")),
        "doubaoApiKey": _usable_secret(c.get("doubaoApiKey") or c.get("doubao_api_key") or c.get("volcApiKey")),
        "model": model,
        "reasoningEffort": effort if effort in ("low", "medium", "high") else "",
        "configured": configured,
        "configError": err,
        "modelsRaw": raw_models,
        "poolBaseUrl": pool_base,
        "poolApiKey": pool_key,
        "poolMode": pool_mode,
        "poolConfigured": pool_ok,
        "papersBaseUrl": papers_base,
        "papersApiKey": papers_key,
        "papersConfigured": papers_ok,
        "mysqlHost": mysql_host,
        "mysqlPort": mysql_port,
        "mysqlUser": mysql_user,
        "mysqlPassword": mysql_password,
        "mysqlDatabase": mysql_db,
        "mysqlConfigured": mysql_ok,
        "clientInbox": client_inbox,
    }

DATA_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = DATA_DIR / "tasks"
BATCHES_DIR = DATA_DIR / "batches"
CLIENT_INBOX_DIR = DATA_DIR / "client_inbox"
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
    "doubao-seed-2-0-mini-260428": "火山",
}
DOUBAO_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_ID = "doubao-seed-2-0-mini-260428"
COMPARE_FAMS = ("grok", "gemini", "doubao")
FAM_TAGS = {"grok": "Grok", "gemini": "Gemini", "doubao": "火山"}
OPINION_FIELDS = {"grok": "opinionGrok", "gemini": "opinionGemini", "doubao": "opinionDoubao"}

def fam_tag(fam: str) -> str:
    return FAM_TAGS.get(str(fam or ""), str(fam or ""))

def model_family(model_id: str) -> str:
    s = str(model_id or "").lower()
    if "gemini" in s:
        return "gemini"
    if "doubao" in s or "volc" in s or "ark.cn" in s or "seed-2-0" in s:
        return "doubao"
    return "grok"

def compare_model_profiles() -> dict:
    """目录里的 Grok / Gemini / 火山配置（可能未就绪）。生成意见对已就绪模型并发对照。"""
    grok = gemini = doubao = None
    for p in catalog_entries():
        fam = model_family(p.get("id") or "")
        if fam == "gemini" and gemini is None:
            gemini = p
        elif fam == "doubao" and doubao is None:
            doubao = p
        elif fam == "grok" and grok is None:
            grok = p
    return {"grok": grok, "gemini": gemini, "doubao": doubao}

def engine_label() -> str:
    cfg = load_config()
    return ("大模型直连 · " + cfg["model"]) if cfg["configured"] else " 未配置：请填写 config.json"

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
        own_base = str(raw.get("baseUrl") or "").strip()
        if own_base:
            base = llm_api_base(own_base)
        else:
            base = llm_api_base(cfg.get("baseUrl") or "")
        key = ""
        if isinstance(raw, dict):
            key = _usable_secret(raw.get("apiKey") or raw.get("api_key"))
        # 独立网关禁止误用顶层 Grok 密钥
        if not key and own_base:
            blob = own_base + " " + mid
            if re.search(r"12ai|gemini", blob, re.I):
                key = _usable_secret(cfg.get("geminiApiKey"))
            elif re.search(r"volces|volcengine|doubao|ark\.cn", blob, re.I):
                key = _usable_secret(cfg.get("doubaoApiKey"))
        elif not key:
            key = _usable_secret(cfg.get("apiKey"))
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
        ready = bool(base and key) and FILL_MARK not in base
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
            "family": model_family(p["id"]),
            "ready": bool(p.get("ready")),
            "default": p["id"] == default_id,
        })
    return out

def public_config() -> dict:
    """前端可展示的运行参数（不含密钥、请求地址、数据库配置）。"""
    cfg = load_config()
    models = public_models()
    default_id = next((m["id"] for m in models if m.get("default")), (models[0]["id"] if models else cfg.get("model") or ""))
    return {
        "version": APP_VERSION,
        "configured": any(m.get("ready") for m in models) or bool(cfg.get("configured")),
        "configError": cfg.get("configError") or "",
        "models": models,
        "engines": [{"id": m["id"], "label": m["label"]} for m in models] or [{"id": "api", "label": engine_label()}],
        "llm": {
            "model": default_id,
        },
    }


def ensure_default_config() -> bool:
    """若尚无默认快照，把当前 config.json 存成 config.default.json。"""
    if DEFAULT_CONFIG_PATH.exists():
        return False
    if not CONFIG_PATH.exists():
        return False
    DEFAULT_CONFIG_PATH.write_bytes(CONFIG_PATH.read_bytes())
    return True


def _write_json(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(tmp, path)


def _models_list(raw: dict) -> list:
    rows = raw.get("models") if isinstance(raw.get("models"), list) else []
    return [x for x in rows]


def _find_model(models: list, fam: str):
    for i, item in enumerate(models):
        mid = item if isinstance(item, str) else str((item or {}).get("id") or "")
        if model_family(mid) == fam:
            return i, (item if isinstance(item, dict) else {"id": item, "label": _MODEL_LABELS.get(mid) or mid})
    return None, None


def _as_int(val, fallback: int) -> int:
    if val in (None, ""):
        return fallback
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return fallback


def _merge_grok(raw: dict, grok: dict):
    if grok.get("baseUrl") is not None:
        raw["baseUrl"] = llm_api_base(str(grok.get("baseUrl") or ""))
    new_key = _usable_secret(grok.get("apiKey")) if "apiKey" in grok else ""
    if new_key:
        raw["apiKey"] = new_key
    if grok.get("reasoningEffort") is not None:
        effort = str(grok.get("reasoningEffort") or "").lower()
        raw["reasoningEffort"] = effort if effort in ("low", "medium", "high") else "medium"
    models = _models_list(raw)
    idx, cur = _find_model(models, "grok")
    entry = dict(cur) if cur else {"id": "grok-4.6", "label": "Grok"}
    if grok.get("id"):
        entry["id"] = str(grok.get("id") or "").strip() or entry.get("id") or "grok-4.6"
    if grok.get("label"):
        entry["label"] = str(grok.get("label") or "Grok").strip() or "Grok"
    if grok.get("stream"):
        entry["stream"] = True
    elif "stream" in grok:
        entry.pop("stream", None)
    if grok.get("timeoutSec") not in (None, ""):
        n = _as_int(grok.get("timeoutSec"), 0)
        if n > 0:
            entry["timeoutSec"] = n
        else:
            entry.pop("timeoutSec", None)
    if idx is None:
        models.append(entry)
    else:
        models[idx] = entry
    raw["models"] = models


def _merge_doubao(raw: dict, doubao: dict):
    models = _models_list(raw)
    idx, cur = _find_model(models, "doubao")
    entry = dict(cur) if cur else {
        "id": DOUBAO_ID,
        "label": "火山",
        "baseUrl": DOUBAO_BASE,
        "stream": True,
        "timeoutSec": 300,
        "reasoningEffort": "",
    }
    if doubao.get("id"):
        entry["id"] = str(doubao.get("id") or "").strip() or entry.get("id") or DOUBAO_ID
    if doubao.get("label"):
        entry["label"] = str(doubao.get("label") or "火山").strip() or "火山"
    if doubao.get("baseUrl") is not None:
        entry["baseUrl"] = llm_api_base(str(doubao.get("baseUrl") or DOUBAO_BASE))
    else:
        entry["baseUrl"] = llm_api_base(str(entry.get("baseUrl") or DOUBAO_BASE))
    new_key = _usable_secret(doubao.get("apiKey")) if "apiKey" in doubao else ""
    if new_key:
        entry["apiKey"] = new_key
        raw["doubaoApiKey"] = new_key
    if "stream" in doubao:
        entry["stream"] = bool(doubao.get("stream"))
    if doubao.get("timeoutSec") not in (None, ""):
        entry["timeoutSec"] = _as_int(doubao.get("timeoutSec"), 300) or 300
    if doubao.get("reasoningEffort") is not None:
        entry["reasoningEffort"] = str(doubao.get("reasoningEffort") or "")
    if idx is None:
        models.append(entry)
    else:
        models[idx] = entry
    raw["models"] = models


def _merge_gemini(raw: dict, gemini: dict):
    models = _models_list(raw)
    idx, cur = _find_model(models, "gemini")
    entry = dict(cur) if cur else {
        "id": "gemini-3.7-flash",
        "label": "Gemini",
        "baseUrl": "https://cdn.12ai.org/v1",
        "stream": True,
        "timeoutSec": 300,
        "reasoningEffort": "",
    }
    if gemini.get("id"):
        entry["id"] = str(gemini.get("id") or "").strip() or entry.get("id") or "gemini-3.7-flash"
    if gemini.get("label"):
        entry["label"] = str(gemini.get("label") or "Gemini").strip() or "Gemini"
    if gemini.get("baseUrl") is not None:
        entry["baseUrl"] = llm_api_base(str(gemini.get("baseUrl") or "https://cdn.12ai.org"))
    new_key = _usable_secret(gemini.get("apiKey")) if "apiKey" in gemini else ""
    if new_key:
        entry["apiKey"] = new_key
        raw["geminiApiKey"] = new_key
    if "stream" in gemini:
        entry["stream"] = bool(gemini.get("stream"))
    if gemini.get("timeoutSec") not in (None, ""):
        entry["timeoutSec"] = _as_int(gemini.get("timeoutSec"), 300) or 300
    if gemini.get("reasoningEffort") is not None:
        entry["reasoningEffort"] = str(gemini.get("reasoningEffort") or "")
    if idx is None:
        models.append(entry)
    else:
        models[idx] = entry
    raw["models"] = models


def save_config(payload: dict, save_as_default: bool = False) -> dict:
    """合并当前正在编辑的模型配置，并可改默认采用模型。不改数据库配置。"""
    if not isinstance(payload, dict):
        raise ValueError("配置必须是对象")
    raw, err = _read_config_file()
    if err and CONFIG_PATH.exists():
        raise ValueError(err)
    if not isinstance(raw, dict):
        raw = {}
    grok = payload.get("grok") if isinstance(payload.get("grok"), dict) else None
    gemini = payload.get("gemini") if isinstance(payload.get("gemini"), dict) else None
    doubao = payload.get("doubao") if isinstance(payload.get("doubao"), dict) else None
    if grok:
        _merge_grok(raw, grok)
    if gemini:
        _merge_gemini(raw, gemini)
    if doubao:
        _merge_doubao(raw, doubao)
    classify = str(payload.get("classifyModel") or payload.get("model") or "").strip()
    if classify:
        raw["model"] = classify
    _write_json(CONFIG_PATH, raw)
    if save_as_default or not DEFAULT_CONFIG_PATH.exists():
        DEFAULT_CONFIG_PATH.write_bytes(CONFIG_PATH.read_bytes())
    _remote_models.clear()
    return editor_config()


def restore_default_config() -> dict:
    if not DEFAULT_CONFIG_PATH.exists():
        raise ValueError("没有默认配置（config.default.json）")
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_bytes(DEFAULT_CONFIG_PATH.read_bytes())
    atomic_replace(tmp, CONFIG_PATH)
    _remote_models.clear()
    return editor_config()


def editor_config() -> dict:
    """前端编辑当前选中模型：含该模型接入参数；不含数据库配置。"""
    ensure_default_config()
    cfg = load_config()
    pair = compare_model_profiles()
    pub = public_config()

    def pack(fam, fallback_id, fallback_label, default_stream, default_timeout):
        p = pair.get(fam) or {}
        key = p.get("apiKey") or ""
        if fam == "gemini" and not key:
            key = cfg.get("geminiApiKey") or ""
        if fam == "doubao" and not key:
            key = cfg.get("doubaoApiKey") or ""
        if fam == "grok" and not key:
            key = cfg.get("apiKey") or ""
        base = p.get("baseUrl") or ""
        if fam == "grok" and not base:
            base = cfg.get("baseUrl") or ""
        if fam == "doubao" and not base:
            base = DOUBAO_BASE
        timeout = int(p.get("timeoutSec") or 0) or int(default_timeout)
        stream = bool(p.get("stream")) if p else default_stream
        if not p:
            stream = default_stream
        return {
            "id": p.get("id") or fallback_id,
            "label": p.get("label") or fallback_label,
            "family": fam,
            "baseUrl": base,
            "hasKey": bool(key),
            "reasoningEffort": p.get("reasoningEffort") or (cfg.get("reasoningEffort") if fam == "grok" else ""),
            "stream": stream,
            "timeoutSec": timeout,
            "ready": bool(p.get("ready")),
        }

    grok = pack("grok", "grok-4.6", "Grok", False, LLM_TIMEOUT_DEFAULT)
    gemini = pack("gemini", "gemini-3.7-flash", "Gemini", True, 300)
    doubao = pack("doubao", DOUBAO_ID, "火山", True, 300)
    pub["edit"] = {
        "grok": grok,
        "gemini": gemini,
        "doubao": doubao,
        "classifyModel": cfg.get("model") or grok["id"],
        "hasDefault": DEFAULT_CONFIG_PATH.exists(),
    }
    pub["configured"] = bool(grok.get("ready") or gemini.get("ready") or doubao.get("ready") or cfg.get("configured"))
    return pub
