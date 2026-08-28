"""外部只读人才库 / 企业库客户端（/api/external-read/v1）"""
from __future__ import annotations
import json, re, asyncio
from pathlib import Path
from urllib.parse import urlparse
import httpx
from .config import load_config, FILL_MARK
from . import matcher as M

PREFIX = "/api/external-read/v1"
DROP_KEYS = {
    "学术主页", "last_job_id", "edit_notes", "policy_match", "source_task_id",
}
CREDIT_RE = re.compile(r"[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}")
_lock = asyncio.Lock()


class PoolError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _err_from_body(status: int, text: str) -> PoolError:
    code, msg = "HTTP_" + str(status), (text or "")[:240]
    try:
        d = json.loads(text or "{}")
        det = d.get("detail") if isinstance(d, dict) else None
        if isinstance(det, dict):
            code = str(det.get("code") or code)
            msg = str(det.get("message") or msg)
        elif isinstance(det, str):
            msg = det
    except Exception:
        pass
    return PoolError(code, msg, status)


def _client_timeout():
    return httpx.Timeout(30.0, connect=8.0)


def _trust_env(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host not in ("127.0.0.1", "localhost", "::1")


async def _get(path: str, params: dict | None = None) -> dict:
    cfg = load_config()
    if not cfg.get("poolConfigured"):
        raise PoolError("NOT_CONFIGURED", "未配置人才库 pool.baseUrl / pool.apiKey")
    url = cfg["poolBaseUrl"].rstrip("/") + PREFIX + path
    headers = {"X-API-KEY": cfg["poolApiKey"]}
    q = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    last = None
    for attempt in range(3):
        async with _lock:
            try:
                async with httpx.AsyncClient(timeout=_client_timeout(), trust_env=_trust_env(url)) as client:
                    r = await client.get(url, headers=headers, params=q)
            except httpx.HTTPError as e:
                last = PoolError("NETWORK", str(e)[:200])
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
        if r.status_code == 429:
            wait = 60
            try:
                wait = min(int(r.headers.get("Retry-After") or "60"), 70)
            except Exception:
                pass
            last = _err_from_body(429, r.text)
            await asyncio.sleep(wait)
            continue
        if r.status_code >= 400:
            raise _err_from_body(r.status_code, r.text)
        data = r.json()
        if not isinstance(data, dict):
            raise PoolError("BAD_RESPONSE", "响应不是 JSON 对象")
        return data
    raise last or PoolError("NETWORK", "人才库请求失败")


def compact(obj, *, budget: int = 80000) -> str:
    def walk(v, depth=0):
        if depth > 8:
            return None
        if v is None or v is False:
            return None
        if v is True or isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s or s in ("***",):
                return None
            return s if len(s) <= 4000 else s[:4000] + "…"
        if isinstance(v, list):
            out = []
            for x in v[:80]:
                cv = walk(x, depth + 1)
                if cv not in (None, "", [], {}):
                    out.append(cv)
            return out or None
        if isinstance(v, dict):
            out = {}
            for k, val in v.items():
                ks = str(k)
                if ks in DROP_KEYS or ks.startswith("homepage_lookup"):
                    continue
                cv = walk(val, depth + 1)
                if cv not in (None, "", [], {}):
                    out[ks] = cv
            return out or None
        return str(v)

    cleaned = walk(obj) or {}
    text = json.dumps(cleaned, ensure_ascii=False, indent=2)
    if len(text) > budget:
        text = text[:budget] + "\n…（已截断）"
    return text


def extract_pool_keys(fname: str, app_text: str) -> dict:
    prof = M.extract_book_profile(fname, app_text)
    names = []
    lines = M.String_splitlines(app_text)
    for raw in lines[:15]:
        m = re.search(r"申\s*报\s*人\s+(.+)", str(raw or ""))
        if not m:
            continue
        nm = _clean_person_name(m.group(1))
        if nm:
            names.append(nm)
            break
    passport = _clean_person_name(_field_after(lines, ("有效证件姓名",)))
    if passport:
        names.append(passport)
    cn = _clean_person_name(_field_after(lines, ("中文(音译)名", "中文（音译）名", "中文音译名")))
    if cn:
        names.append(cn)
    nf = _clean_person_name(str(prof.get("nameFull") or ""))
    if nf:
        names.append(nf)
    company = M.pick_company(
        prof.get("ent"),
        _field_after(lines, ("申报企业", "企业名称")),
    )
    company = re.sub(r"^[|｜\s]+", "", company or "").strip()[:80]
    codes = []
    blob = re.sub(r"[\s|｜]", "", str(app_text or "")).upper()
    for m in CREDIT_RE.finditer(blob):
        if m.group(0) not in codes:
            codes.append(m.group(0))
    attach = list(prof.get("nums") or [])
    seen = set()
    uniq = []
    for n in names:
        k = _norm_name(n)
        if k and k not in seen and len(n) >= 2:
            seen.add(k)
            uniq.append(n)
    return {
        "attachIds": attach,
        "names": uniq[:4],
        "creditCodes": codes[:3],
        "company": company,
    }


def _field_after(lines, keys) -> str:
    skip_if = re.compile(r"必须与|应填写|应为|例如|不得|须严格|填写内容|中国籍|外籍必须")
    labels = set(keys) | {"照片", "性别", "出生日期"}
    for i, raw in enumerate(lines):
        s = str(raw or "").strip()
        hit = next((k for k in keys if s == k or s.startswith(k)), None)
        if not hit:
            hit = next((k for k in keys if k in s), None)
            if not hit:
                continue
            if skip_if.search(s) or len(s) > 60:
                continue
        rest = s.split(hit, 1)[-1].strip(" ：:\t|｜")
        if rest and rest not in labels and not skip_if.search(rest) and 1 < len(rest) < 80:
            return rest
        for j in range(i + 1, min(i + 4, len(lines))):
            v = str(lines[j] or "").strip(" ：:\t|｜")
            if not v or v in labels or skip_if.search(v) or len(v) >= 80:
                continue
            if any(k in v for k in keys):
                continue
            if any(x in v for x in ("申报企业", "申报省市", "申报日期", "填表须知", "填 表")):
                continue
            return v
    return ""


def _clean_person_name(n) -> str:
    n = re.sub(r"[（(]有效证件姓名[）)]", "", str(n or ""))
    n = re.sub(r"\s+", " ", n).strip(" ：:\t|｜")
    if not n or any(x in n for x in ("申报企业", "申报省市", "申报日期", "照片")):
        return ""
    if not re.search(r"[A-Za-z]{2,}|[\u4e00-\u9fa5]{2,}", n):
        return ""
    if len(n) < 2 or len(n) >= 80:
        return ""
    return n


def _norm_name(s: str) -> str:
    return re.sub(r"[\s·･.．'’\-]+", "", str(s or "")).lower()


def _pick_item(items: list, *, name_hints=None, company_hint=""):
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    hints = [_norm_name(x) for x in (name_hints or []) if x]
    ch = _norm_name(company_hint)
    best, score = items[0], -1
    for it in items:
        sc = 0
        nm = _norm_name(it.get("name") or it.get("company_name") or "")
        if nm and any(h and (h == nm or h in nm or nm in h) for h in hints):
            sc += 5
        cn = _norm_name(it.get("company_name") or "")
        if ch and cn and (ch == cn or ch in cn or cn in ch):
            sc += 5
        if sc > score:
            best, score = it, sc
    return best


async def health() -> dict:
    cfg = load_config()
    if not cfg.get("poolConfigured"):
        return {"configured": False, "ok": False, "error": "未配置 pool.baseUrl / pool.apiKey"}
    try:
        data = await _get("/health")
        return {
            "configured": True,
            "ok": bool(data.get("success") or data.get("status") == "ok"),
            "app_id": data.get("app_id"),
            "scopes": data.get("scopes") or [],
            "error": None,
        }
    except PoolError as e:
        return {"configured": True, "ok": False, "error": e.code + ": " + e.message}


async def _talent_by_attach(attach_id: str, mode: str) -> dict | None:
    data = await _get("/talents", {"attach_id": attach_id, "page": 1, "page_size": 20, "mode": mode})
    return _pick_item(data.get("items") or [])


async def _talent_by_q(q: str, mode: str, names: list) -> dict | None:
    data = await _get("/talents", {"q": q, "page": 1, "page_size": 20, "mode": mode})
    return _pick_item(data.get("items") or [], name_hints=names)


async def _enterprise_by_credit(code: str, mode: str) -> dict | None:
    data = await _get("/enterprises", {"credit_code": code, "page": 1, "page_size": 20, "mode": mode})
    return _pick_item(data.get("items") or [])


async def _enterprise_by_q(q: str, mode: str) -> dict | None:
    data = await _get("/enterprises", {"q": q, "page": 1, "page_size": 20, "mode": mode})
    return _pick_item(data.get("items") or [], company_hint=q)


async def _detail(kind: str, item: dict | None) -> dict | None:
    if not item or item.get("id") in (None, ""):
        return item
    path = ("/talents/" if kind == "talent" else "/enterprises/") + str(int(item["id"]))
    data = await _get(path)
    return data.get("item") or item


async def lookup_for_app(fname: str, app_text: str) -> dict:
    keys = extract_pool_keys(fname, app_text)
    snap = {
        "configured": bool(load_config().get("poolConfigured")),
        "ok": False,
        "error": "",
        "keys": keys,
        "talent": None,
        "enterprise": None,
        "notes": [],
        "summary": "",
        "hit": {"talent": "", "enterprise": "", "ok": False},
    }
    if not snap["configured"]:
        snap["error"] = "未配置人才库"
        snap["notes"].append("跳过检索：config.json 未填写 pool.baseUrl / pool.apiKey")
        snap["summary"] = "未配置人才库"
        return snap
    mode = load_config().get("poolMode") or "all"
    try:
        talent = None
        for aid in keys["attachIds"]:
            talent = await _talent_by_attach(aid, mode)
            if talent:
                snap["notes"].append("人才 attach_id=" + aid + " 命中")
                break
        if not talent:
            for nm in keys["names"]:
                talent = await _talent_by_q(nm, mode, keys["names"])
                if talent:
                    snap["notes"].append("人才 q=" + nm + " 命中")
                    break
        if talent:
            snap["talent"] = await _detail("talent", talent)
        else:
            snap["notes"].append("人才未命中（编号 " + "/".join(keys["attachIds"] or ["无"]) + "）")

        ent = None
        for code in keys["creditCodes"]:
            ent = await _enterprise_by_credit(code, mode)
            if ent:
                snap["notes"].append("企业 credit_code=" + code + " 命中")
                break
        if not ent and keys["company"] and len(keys["company"]) >= 4:
            ent = await _enterprise_by_q(keys["company"], mode)
            if ent:
                snap["notes"].append("企业名称命中：" + keys["company"])
        if ent:
            snap["enterprise"] = await _detail("enterprise", ent)
        elif keys["company"] or keys["creditCodes"]:
            snap["notes"].append("企业未命中")

        t = snap["talent"] or {}
        e = snap["enterprise"] or {}
        tlabel = " ".join(x for x in (str(t.get("attach_id") or ""), str(t.get("name") or "")) if x).strip()
        elabel = str(e.get("company_name") or e.get("credit_code") or "").strip()
        snap["hit"] = {"talent": tlabel, "enterprise": elabel, "ok": bool(t or e)}
        snap["ok"] = bool(t or e)
        parts = []
        if tlabel:
            parts.append("人才 " + tlabel)
        if elabel:
            parts.append("企业 " + elabel)
        snap["summary"] = "；".join(parts) if parts else "库内无匹配记录"
        return snap
    except PoolError as e:
        snap["error"] = e.code + ": " + e.message
        snap["notes"].append("检索失败：" + snap["error"])
        snap["summary"] = "检索失败"
        return snap


def format_pool_prompt(snap: dict) -> str:
    if not snap:
        return "（未检索人才库）"
    if not snap.get("configured"):
        return "（未配置人才库接口，仅能使用申报书正文；缺数据写入 leftovers）"
    if snap.get("error") and not (snap.get("talent") or snap.get("enterprise")):
        return "（人才库检索失败：" + str(snap.get("error")) + "。仅能使用申报书正文；缺数据写入 leftovers）"
    chunks = []
    if snap.get("talent"):
        t = snap["talent"]
        head = {"id": t.get("id"), "attach_id": t.get("attach_id"), "name": t.get("name"), "mode": t.get("mode"),
                "profile_summary": t.get("profile_summary"), "google_scholar_url": t.get("google_scholar_url"),
                "linkedin_url": t.get("linkedin_url")}
        chunks.append("## 人才记录\n" + compact({"meta": head, "payload": t.get("payload") or {}}))
    else:
        chunks.append("## 人才记录\n（无匹配）")
    if snap.get("enterprise"):
        e = snap["enterprise"]
        head = {"id": e.get("id"), "credit_code": e.get("credit_code"), "company_name": e.get("company_name"),
                "mode": e.get("mode"), "intro_summary": e.get("intro_summary"), "region_text": e.get("region_text")}
        chunks.append("## 企业记录\n" + compact({"meta": head, "payload": e.get("payload") or {}}))
    else:
        chunks.append("## 企业记录\n（无匹配）")
    notes = snap.get("notes") or []
    if notes:
        chunks.append("检索说明：" + "；".join(str(x) for x in notes))
    return "\n\n".join(chunks)


def save_snapshot(task_dir: str | Path, snap: dict) -> None:
    d = Path(task_dir) / "work" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in (snap or {}).items()}
    (d / "pool.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
