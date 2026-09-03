"""修改意见提到缺附件时：先查人才库，论文再查论文导出 API，并给出本系统下载链接。"""
from __future__ import annotations
import json, re, secrets
from pathlib import Path
from urllib.parse import urlparse
import httpx
from .config import load_config
from . import papers as P

NEED_RE = re.compile(
    r"缺|缺少|缺失|未上传|未提供|未附|请补充|须补充|需补充|请上传|补交|补传|补齐|不清晰|"
    r"需附全文|附全文|无法在正文|请补充提供|须补传|未给出"
)

KINDS = [
    {"id": "passport", "label": "护照", "keys": ("护照", "passport")},
    {"id": "id_doc", "label": "身份证明", "keys": ("身份证明", "身份证", "永居证", "身份证件", "证件扫描")},
    {"id": "education", "label": "学历证明", "keys": ("学历证明", "学历材料", "学位证", "毕业证", "学位证书", "毕业证书", "学历学位", "diploma", "学历佐证")},
    {"id": "work_proof", "label": "工作证明", "keys": ("工作证明", "工作经历证明", "在职证明", "任职证明")},
    {"id": "intent", "label": "意向协议", "keys": ("意向协议", "意向书", "引进协议", "合同意向")},
    {"id": "equity", "label": "股权证明", "keys": ("股权证明", "企业股权")},
    {"id": "paper", "label": "论文全文", "keys": ("论文全文", "论文pdf", "论文 pdf", "论文PDF", "论文附件", "论文材料", "论文扫描", "代表性论著", "需附全文")},
]

URL_KEYS = ("url", "file_url", "download_url", "pdf_url", "href", "link", "path", "file_path", "download")
NAME_KEYS = ("filename", "file_name", "name", "title", "title_zh", "原始文件名", "文件名", "附件名称")
KIND_KEYS = ("kind", "type", "category", "doc_type", "附件类型", "材料类型", "label", "分类")
ID_KEYS = ("file_id", "fileId", "id", "attachment_id")
PAPER_PATH = re.compile(r"paper|publication|论著|论文|pdf", re.I)
EXT_OK = re.compile(r"\.(pdf|docx?|jpe?g|png|tif{1,2}|webp|zip)$", re.I)


def extract_needed_kinds(texts) -> list:
    blob = "\n".join(str(x or "") for x in (texts or []) if str(x or "").strip())
    if not blob:
        return []
    out, seen = [], set()
    for kind in KINDS:
        if kind["id"] in seen:
            continue
        if _kind_needed(blob, kind):
            seen.add(kind["id"])
            out.append(kind)
    return out


def _kind_needed(blob: str, kind: dict) -> bool:
    for kw in kind["keys"]:
        start = 0
        low = blob if kw.isascii() else blob
        while True:
            i = low.lower().find(kw.lower(), start) if kw.isascii() else blob.find(kw, start)
            if i < 0:
                break
            window = blob[max(0, i - 24): i + len(kw) + 28]
            if NEED_RE.search(window):
                return True
            start = i + len(kw)
    if kind["id"] == "paper":
        if re.search(r"论文.{0,12}(全文|PDF|pdf|附件|扫描件)", blob) and NEED_RE.search(blob):
            return True
    return False


def _walk_files(obj, path="", depth=0, acc=None):
    if acc is None:
        acc = []
    if depth > 10 or obj is None:
        return acc
    if isinstance(obj, list):
        for i, x in enumerate(obj[:120]):
            _walk_files(x, path + "[" + str(i) + "]", depth + 1, acc)
        return acc
    if not isinstance(obj, dict):
        return acc
    url = ""
    for k in URL_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip() and v.strip() not in ("***",):
            url = v.strip()
            break
    filename = ""
    for k in NAME_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            filename = v.strip()
            if EXT_OK.search(filename) or k in ("filename", "file_name", "文件名"):
                break
    kind_s = ""
    for k in KIND_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            kind_s = v.strip()
            break
    fid = None
    for k in ID_KEYS:
        if obj.get(k) not in (None, ""):
            fid = obj.get(k)
            break
    looks = bool(url) or bool(fid and filename) or bool(filename and EXT_OK.search(filename))
    if looks:
        acc.append({
            "url": url,
            "filename": filename or (str(fid) if fid is not None else "file"),
            "title": str(obj.get("title_zh") or obj.get("title") or filename or ""),
            "kind_raw": kind_s,
            "file_id": fid,
            "path": path,
            "doi": str(obj.get("doi") or ""),
        })
    for k, v in obj.items():
        if k in URL_KEYS or k in NAME_KEYS:
            continue
        if isinstance(v, (dict, list)):
            _walk_files(v, path + "/" + str(k), depth + 1, acc)
    return acc


def _match_kind(file_item: dict, kind: dict) -> bool:
    blob = " ".join(str(file_item.get(k) or "") for k in ("filename", "title", "kind_raw", "path", "doi"))
    if kind["id"] == "paper":
        if PAPER_PATH.search(blob) or file_item.get("doi"):
            return True
    for kw in kind["keys"]:
        if kw.lower() in blob.lower():
            return True
    return False


def _pool_files(snap: dict) -> list:
    acc = []
    t = (snap or {}).get("talent") or {}
    e = (snap or {}).get("enterprise") or {}
    _walk_files({"meta": t, "payload": t.get("payload") if isinstance(t, dict) else {}}, "talent", 0, acc)
    _walk_files({"meta": e, "payload": e.get("payload") if isinstance(e, dict) else {}}, "enterprise", 0, acc)
    # 去重
    seen, out = set(), []
    for it in acc:
        key = (it.get("url") or "", it.get("filename") or "", str(it.get("file_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _attach_ids(snap: dict, app_no: str) -> list:
    ids, seen = [], set()
    keys = (snap or {}).get("keys") or {}
    talent = (snap or {}).get("talent") or {}
    for raw in list(keys.get("attachIds") or []) + [talent.get("attach_id"), app_no]:
        aid = P.norm_attach_id(raw)
        if aid and aid not in seen:
            seen.add(aid)
            ids.append(aid)
    return ids


def _new_id() -> str:
    return "a" + secrets.token_hex(3)


def _public_item(tid: str, fid: str, *, kind: str, source: str, filename: str, title: str, note: str = ""):
    return {
        "id": fid,
        "kind": kind,
        "source": source,
        "filename": filename,
        "title": title or filename,
        "download": "/api/tasks/" + tid + "/ext-files/" + fid,
        "found": True,
        "note": note,
    }


def _private_item(*, source: str, url: str, filename: str):
    return {"source": source, "url": url, "filename": filename}


async def resolve_missing(tid: str, texts, snap: dict, app_no: str, prev: dict | None = None) -> dict:
    needed = extract_needed_kinds(texts)
    cfg = load_config()
    result = prev or {
        "needed": [],
        "items": [],
        "private": {},
        "notes": [],
        "summary": "",
        "papersFetched": False,
        "papersError": "",
        "papersAttachId": "",
    }
    labels = [k["label"] for k in needed]
    result["needed"] = labels
    if not needed:
        result["summary"] = "修改意见未点名缺失附件"
        return result

    pool_files = _pool_files(snap)
    known_urls = {str(v.get("url") or "") for v in (result.get("private") or {}).values() if v}
    by_kind = {lab: [] for lab in labels}
    for it in result.get("items") or []:
        by_kind.setdefault(it.get("kind") or "", []).append(it)

    for kind in needed:
        hits = [f for f in pool_files if _match_kind(f, kind)]
        for f in hits:
            if not f.get("url"):
                result["notes"].append(kind["label"] + " 库内有文件名「" + str(f.get("filename") or "") + "」但无下载地址")
                continue
            if str(f.get("url") or "") in known_urls:
                continue
            known_urls.add(str(f.get("url") or ""))
            fid = _new_id()
            pub = _public_item(
                tid, fid,
                kind=kind["label"], source="pool",
                filename=str(f.get("filename") or "file"),
                title=str(f.get("title") or f.get("filename") or ""),
            )
            result["items"].append(pub)
            result["private"][fid] = _private_item(source="pool", url=f["url"], filename=pub["filename"])
            by_kind[kind["label"]].append(pub)

    paper_kind = next((k for k in needed if k["id"] == "paper"), None)
    pool_paper_ok = bool(by_kind.get("论文全文"))
    if paper_kind and not pool_paper_ok and not result.get("papersFetched"):
        result["papersFetched"] = True
        if not cfg.get("papersConfigured"):
            result["papersError"] = "论文 API 未配置（config.json papers.apiKey）"
            result["notes"].append(result["papersError"])
        else:
            ids = _attach_ids(snap, app_no)
            if not ids:
                result["papersError"] = "无法确定人才编号，论文系统只能按 attach_id 查询"
                result["notes"].append(result["papersError"])
            else:
                last_err = ""
                for aid in ids:
                    try:
                        data = await P.get_talent(aid)
                    except P.PapersError as e:
                        if e.code == "NOT_FOUND":
                            last_err = "论文系统没有该人才档案 attach_id=" + aid
                            result["notes"].append(last_err)
                            continue
                        last_err = e.code + ": " + e.message
                        result["notes"].append("论文系统：" + last_err)
                        if e.code in ("AUTH", "FORBIDDEN", "NOT_CONFIGURED"):
                            break
                        continue
                    result["papersAttachId"] = aid
                    files = P.public_files(data, cfg.get("papersBaseUrl") or "")
                    att = data.get("attachment") if isinstance(data.get("attachment"), dict) else {}
                    if att and not att.get("ready"):
                        result["notes"].append("论文系统档案已找到（" + aid + "）但装订附件尚未生成（可稍后下载）")
                    if not files:
                        last_err = "论文系统有档案但暂无单篇 PDF attach_id=" + aid
                        result["notes"].append(last_err)
                    for f in files:
                        u = str(f.get("url") or "")
                        if u and u in known_urls:
                            continue
                        if u:
                            known_urls.add(u)
                        fid = _new_id()
                        kind_label = str(f.get("kind") or "论文全文")
                        pub = _public_item(
                            tid, fid,
                            kind=kind_label, source="papers",
                            filename=str(f.get("filename") or "paper.pdf"),
                            title=str(f.get("title") or ""),
                            note="论文系统 attach_id=" + aid,
                        )
                        result["items"].append(pub)
                        result["private"][fid] = _private_item(
                            source="papers", url=str(f.get("url") or ""), filename=pub["filename"],
                        )
                        by_kind.setdefault(kind_label, []).append(pub)
                    break
                if not any(it.get("source") == "papers" for it in result["items"]) and last_err:
                    result["papersError"] = last_err

    found_n = len(result["items"])
    miss = [lab for lab in labels if not any(it.get("kind") == lab or (lab == "论文全文" and "论文" in str(it.get("kind") or "")) for it in result["items"])]
    parts = []
    if found_n:
        parts.append("已定位 " + str(found_n) + " 个附件下载")
    if miss:
        parts.append("未找到：" + "、".join(miss))
    result["summary"] = "；".join(parts) if parts else "未检索到可下载附件"
    return result


def leftover_lines(result: dict) -> list:
    lines = []
    needed = result.get("needed") or []
    items = result.get("items") or []
    by = {}
    for it in items:
        by.setdefault(it.get("kind") or "附件", []).append(it)
    for lab in needed:
        hits = list(by.get(lab) or [])
        if lab == "论文全文":
            for k, rows in by.items():
                if k != lab and "论文" in str(k):
                    hits.extend(rows)
        if hits:
            bits = []
            for it in hits:
                src = "人才库" if it.get("source") == "pool" else "论文系统"
                bits.append(src + " " + str(it.get("filename") or it.get("title") or "") + " " + str(it.get("download") or ""))
            lines.append("【缺附件·" + lab + "】已检索到，下载：" + " ； ".join(bits))
        else:
            extra = ""
            if lab == "论文全文" and result.get("papersError"):
                extra = "。" + str(result.get("papersError"))
            elif not extra and result.get("notes"):
                extra = "。" + "；".join(str(x) for x in result.get("notes") if lab in str(x) or (lab == "论文全文" and "论文" in str(x)))
            lines.append("【缺附件·" + lab + "】人才库未检索到可下载文件" + extra)
    seen = set()
    uniq = []
    for s in lines:
        k = re.sub(r"\s+", "", s)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
    return uniq


def format_attach_prompt(result: dict) -> str:
    if not result or not result.get("needed"):
        return "（修改意见未点名缺失护照/学历证明/论文全文等附件）"
    lines = ["## 缺失附件检索结果", "意见点名：" + "、".join(result.get("needed") or [])]
    items = result.get("items") or []
    if items:
        lines.append("已找到下载（leftovers 必须写入这些链接，不要只写无法在正文完成）：")
        for it in items:
            lines.append("- " + str(it.get("kind") or "") + " " + str(it.get("title") or it.get("filename") or "") + " → " + str(it.get("download") or ""))
    else:
        lines.append("库内与论文系统均未给出可下载文件。leftovers 写明缺哪类附件，严禁编造已上传。")
    notes = result.get("notes") or []
    if notes:
        lines.append("检索说明：" + "；".join(str(x) for x in notes[:12]))
    return "\n".join(lines)


def save_snapshot(task_dir: str | Path, result: dict) -> None:
    d = Path(task_dir) / "work" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    slim = {
        "needed": result.get("needed") or [],
        "items": result.get("items") or [],
        "notes": result.get("notes") or [],
        "summary": result.get("summary") or "",
        "papersFetched": bool(result.get("papersFetched")),
        "papersError": result.get("papersError") or "",
        "papersAttachId": result.get("papersAttachId") or "",
        "private": result.get("private") or {},
    }
    (d / "attachments.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")


def load_private(task_dir: str | Path, fid: str) -> dict | None:
    fp = Path(task_dir) / "work" / "tmp" / "attachments.json"
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    priv = (data or {}).get("private") or {}
    item = priv.get(fid)
    return item if isinstance(item, dict) else None


def public_plan_block(result: dict) -> dict:
    return {
        "summary": result.get("summary") or "",
        "needed": result.get("needed") or [],
        "items": [{k: it.get(k) for k in ("id", "kind", "source", "filename", "title", "download", "found", "note")} for it in (result.get("items") or [])],
        "notes": result.get("notes") or [],
    }


async def fetch_upstream(priv: dict):
    """按 private 记录向上游取文件，返回 (content, filename, content_type, status)."""
    if not isinstance(priv, dict) or not priv.get("url"):
        raise P.PapersError("BAD_FILE", "没有下载地址")
    cfg = load_config()
    source = str(priv.get("source") or "")
    url = str(priv.get("url") or "")
    headers = {}
    if source == "papers":
        if not cfg.get("papersConfigured"):
            raise P.PapersError("NOT_CONFIGURED", "未配置论文系统")
        url = P.abs_url(cfg["papersBaseUrl"], url)
        headers = {"X-Api-Key": cfg["papersApiKey"]}
    else:
        if url.startswith("/"):
            if not cfg.get("poolConfigured"):
                raise P.PapersError("NOT_CONFIGURED", "未配置人才库")
            url = cfg["poolBaseUrl"].rstrip("/") + url
        pool_host = urlparse(cfg.get("poolBaseUrl") or "").hostname
        if pool_host and urlparse(url).hostname == pool_host and cfg.get("poolApiKey"):
            headers = {"X-API-KEY": cfg["poolApiKey"]}
    timeout = httpx.Timeout(120.0, connect=8.0)
    from .config import httpx_trust_env
    async with httpx.AsyncClient(timeout=timeout, trust_env=httpx_trust_env(), follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
    if r.status_code == 409:
        raise P.PapersError("NOT_READY", "附件尚未生成", 409)
    if r.status_code >= 400:
        raise P.PapersError("HTTP_" + str(r.status_code), (r.text or "")[:200], r.status_code)
    fn = str(priv.get("filename") or "file")
    cd = r.headers.get("content-disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?", cd, re.I)
    if m:
        fn = m.group(1) or m.group(2) or fn
    ctype = r.headers.get("content-type") or "application/octet-stream"
    return r.content, fn, ctype, r.status_code
