"""config.json 读取：baseUrl / apiKey / model / reasoningEffort"""
from __future__ import annotations
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
FILL_MARK = "填入"

def load_config() -> dict:
    c: dict = {}
    try:
        c = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        c = {}
    base = str(c.get("baseUrl") or "")
    key = str(c.get("apiKey") or "")
    model = str(c.get("model") or "")
    effort = str(c.get("reasoningEffort") or "medium").lower()
    configured = bool(base and key and model) and not any(FILL_MARK in x for x in (base, key, model))
    pool = c.get("pool") if isinstance(c.get("pool"), dict) else {}
    pool_base = str(pool.get("baseUrl") or c.get("poolBaseUrl") or "").rstrip("/")
    pool_key = str(pool.get("apiKey") or c.get("poolApiKey") or "")
    pool_mode = str(pool.get("mode") or "all").strip() or "all"
    pool_ok = bool(pool_base and pool_key) and not any(FILL_MARK in x for x in (pool_base, pool_key))
    return {
        "baseUrl": base.rstrip("/"),
        "apiKey": key,
        "model": model,
        "reasoningEffort": effort if effort in ("low", "medium", "high") else "",
        "configured": configured,
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

def engine_label() -> str:
    cfg = load_config()
    return ("大模型直连 · " + cfg["model"]) if cfg["configured"] else "⚠ 未配置：请填写 config.json"
