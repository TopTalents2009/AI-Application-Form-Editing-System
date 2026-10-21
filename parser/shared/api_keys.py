"""Read-only API keys for sharing chat records with other clients."""
import os
import hmac
import json
import secrets
from typing import List, Optional

from shared.wecom_ssh import strip_jsonc


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.jsonc")
KEY_FILE = os.path.join(PROJECT_ROOT, "export", "api_read_key.txt")


def _read_jsonc(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        try:
            data = json.loads(strip_jsonc(f.read()))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _open_api_cfg() -> dict:
    cfg = _read_jsonc(CONFIG_FILE)
    block = cfg.get("open_api")
    return block if isinstance(block, dict) else {}


def is_enabled() -> bool:
    return bool(_open_api_cfg().get("enabled", True))


def _load_or_create_file_key() -> str:
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    if os.path.isfile(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    value = "ci_" + secrets.token_urlsafe(32)
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(value)
    return value


def _append_key(bucket: List[dict], name: str, key: str) -> None:
    key = (key or "").strip()
    if not key:
        return
    if any(item["key"] == key for item in bucket):
        return
    bucket.append({"name": (name or "key").strip() or "key", "key": key})


def list_keys() -> List[dict]:
    if not is_enabled():
        return []
    cfg = _open_api_cfg()
    out: List[dict] = []
    _append_key(out, "default", str(cfg.get("read_key") or ""))
    extra = cfg.get("keys") or []
    if isinstance(extra, list):
        for index, item in enumerate(extra, start=1):
            if isinstance(item, str):
                _append_key(out, f"key{index}", item)
            elif isinstance(item, dict) and item.get("enabled") is not False:
                _append_key(out, str(item.get("name") or f"key{index}"), str(item.get("key") or ""))
    _append_key(out, "env", os.environ.get("CHATINSIGHT_API_KEY", ""))
    if not out:
        _append_key(out, "default", _load_or_create_file_key())
    return out


def _same_secret(got: str, expected: str) -> bool:
    left = (got or "").encode("utf-8")
    right = (expected or "").encode("utf-8")
    if not left or not right or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def verify(token: str) -> Optional[dict]:
    got = (token or "").strip()
    if not got or not is_enabled():
        return None
    for item in list_keys():
        if _same_secret(got, item["key"]):
            return item
    return None


def extract_token(x_api_key: str = "", authorization: str = "", query_key: str = "") -> str:
    if (x_api_key or "").strip():
        return x_api_key.strip()
    raw = (authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    if raw:
        return raw
    return (query_key or "").strip()


def homepage_info(base_urls: List[str]) -> dict:
    keys = list_keys() if is_enabled() else []
    urls = [url.rstrip("/") + "/v1" for url in base_urls]
    primary = keys[0]["key"] if keys else ""
    example_base = next((u for u in urls if "127.0.0.1" not in u), urls[0] if urls else "/v1")
    return {
        "enabled": is_enabled(),
        "key": primary,
        "keys": keys,
        "urls": urls,
        "example": (
            f'curl -s -H "X-API-Key: {primary}" "{example_base}/sources"'
            if primary else ""
        ),
    }
