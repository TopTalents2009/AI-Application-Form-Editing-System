"""修改意见提到缺附件时：先查人才库，论文再查论文导出 API，并给出本系统下载链接。"""
from __future__ import annotations
import asyncio, json, re, secrets
from pathlib import Path
from urllib.parse import urlparse
import httpx
from .config import load_config
from . import papers as P
from . import pool as POOL
from . import project_proof as PP

NEED_RE = re.compile(
    r"缺|缺少|缺失|未上传|未提供|未附|请补充|须补充|需补充|请上传|补交|补传|补齐|不清晰|"
    r"需附全文|附全文|无法在正文|请补充提供|须补传|未给出|"
    r"修改|更换|替换|更新|重传|重新上传|重新提供|请提供|须提供|应提供|必须提供|"
    r"更正|校正|改为|改成|改至|有误|错误|不符|不一致"
)
_PASSPORT_FORM_RE = re.compile(r"护照\s*(号码|号)|护照号")
_PASSPORT_SCAN_RE = re.compile(r"扫描|复印|附件|证明|不清晰|缺|上传|补传|材料")
# 证明材料上的栏位（时间、日期等）系统改不了申报书正文
_PROOF_ATTR_RE = re.compile(
    r"时间|日期|起止|入职|离职|到职|任期|任职期限|年限|年月|签发|有效期|单位名称|任职单位"
)
_PROOF_KIND_IDS = {"work_proof", "education", "id_doc", "intent"}
_TASK_ACTION = {
    "passport": "护照扫描件无法写入申报书正文。请按审核意见更新护照材料后，在申报系统或材料包中替换/补传。",
    "id_doc": "身份证明扫描件无法通过系统改申报书。请更换清晰件或按意见补齐后另行上传。",
    "education": "学历/学位证明扫描件无法通过系统修改（含证明上的时间、日期）。请按意见更正后另行上传。",
    "work_proof": "工作经历证明、在职证明等扫描件无法通过系统修改（含证明上的起止时间、入职/离职日期）。请按意见更正后另行上传。",
    "intent": "意向协议等合同扫描件无法写入申报书。请按意见更新后另行上传。",
    "equity": "股权证明无法写入申报书正文。请按意见准备材料后另行上传。",
    "paper": "论文全文/PDF 附件无法写入申报书论著栏。请按意见补传全文或替换扫描件。",
    "photo": "证件照无法通过改申报书正文替换。请按规格另行上传。",
    "sign": "电子签/签字扫描无法在正文完成。请在客户端或材料包中补签、补传。",
    "project": "项目证明/立项批文无法写入申报书项目表。请按意见补传证明材料（勿用论文代替）。",
}

KINDS = [
    {"id": "passport", "label": "护照", "keys": ("护照", "passport")},
    {"id": "id_doc", "label": "身份证明", "keys": ("身份证明", "身份证", "永居证", "身份证件", "证件扫描")},
    {"id": "education", "label": "学历证明", "keys": ("学历证明", "学历材料", "学位证", "毕业证", "学位证书", "毕业证书", "学历学位", "diploma", "学历佐证")},
    {"id": "work_proof", "label": "工作证明", "keys": ("工作证明", "工作经历证明", "在职证明", "任职证明")},
    {"id": "intent", "label": "意向协议", "keys": ("意向协议", "意向书", "引进协议", "合同意向")},
    {"id": "equity", "label": "股权证明", "keys": ("股权证明", "企业股权")},
    {"id": "paper", "label": "论文全文", "keys": ("论文全文", "论文pdf", "论文 pdf", "论文PDF", "论文附件", "论文材料", "论文扫描", "代表性论著", "需附全文", "科研成果")},
    {"id": "photo", "label": "证件照", "keys": ("证件照", "一寸照", "白底照")},
    {"id": "sign", "label": "电子签", "keys": ("电子签", "电子签名", "签字扫描", "签名扫描")},
    {"id": "project", "label": "项目证明", "keys": ("项目证明", "项目材料", "项目扫描", "立项批文", "立项证明", "主持项目证明", "科研项目证明")},
]
KIND_POOL_ID = {
    "passport": "passport",
    "id_doc": "passport",
    "education": "edu",
    "work_proof": "work",
    "paper": "research",
    "photo": "photo",
    "sign": "sign",
    "project": "project",
}
KIND_FOLDERS = {
    "passport": ("护照",),
    "work_proof": ("工作经历", "工作证明", "在职证明"),
    "education": ("学历", "学历证明", "学位", "教育经历"),
    "id_doc": ("身份证明", "身份证", "证件"),
    "photo": ("证件照",),
    "sign": ("电子签", "签名"),
    "intent": ("意向协议", "意向书"),
    "equity": ("股权", "股权证明"),
    "paper": ("论文", "科研成果"),
    "project": ("项目证明", "项目"),
}
LOCAL_ATTACH_ROOT = Path(__file__).resolve().parent.parent / "附件"
LOCAL_EXT = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".zip", ".xls", ".xlsx"}

URL_KEYS = ("url", "file_url", "download_url", "pdf_url", "href", "link", "path", "file_path", "download")
NAME_KEYS = ("filename", "file_name", "name", "title", "title_zh", "原始文件名", "文件名", "附件名称")
KIND_KEYS = ("kind", "type", "category", "doc_type", "附件类型", "材料类型", "label", "分类")
ID_KEYS = ("file_id", "fileId", "id", "attachment_id")
PAPER_PATH = re.compile(r"paper|publication|论著|论文|著作|科研成果", re.I)
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
            window = blob[max(0, i - 36): i + len(kw) + 48]
            if not _skip_form_field(kind, window) and (NEED_RE.search(window) or _proof_attr_hit(kind, window)):
                return True
            start = i + len(kw)
    if kind["id"] == "work_proof":
        if re.search(
            r"(工作证明|工作经历证明|在职证明|任职证明|海外工作证明).{0,80}(时间|日期|起止|入职|离职|到职|任期|任职期限|年限|签发)",
            blob,
        ) or re.search(
            r"(时间|日期|起止|入职|离职|到职|任期).{0,40}(工作证明|工作经历证明|在职证明|任职证明)",
            blob,
        ):
            return True
    if kind["id"] == "education":
        if re.search(
            r"(学历证明|学位证|毕业证|学位证书|毕业证书|学历材料|学历佐证).{0,80}(时间|日期|起止|年月|毕业|入学)",
            blob,
        ) or re.search(
            r"(时间|日期|毕业|入学).{0,40}(学历证明|学位证|毕业证)",
            blob,
        ):
            return True
    if kind["id"] == "paper":
        if re.search(r"论文.{0,12}(全文|PDF|pdf|附件|扫描件)", blob) and NEED_RE.search(blob):
            return True
    if kind["id"] == "project":
        if re.search(r"(主持.{0,6}项目|立项|科研项目).{0,16}(证明|批文|批复|附件材料)", blob):
            if NEED_RE.search(blob) or re.search(r"必须提供|须提供|应提供|请提供|严禁用论文代替", blob):
                return True
    return False


def _proof_attr_hit(kind: dict, window: str) -> bool:
    """证明扫描件上的时间、日期等，即使没写「更换附件」也要进任务清单。"""
    if (kind or {}).get("id") not in _PROOF_KIND_IDS:
        return False
    return bool(_PROOF_ATTR_RE.search(window or ""))


def _skip_form_field(kind: dict, window: str) -> bool:
    """护照号码、工作经历表格改写等正文栏可改，不当成附件任务。"""
    kid = (kind or {}).get("id")
    w = window or ""
    if kid == "passport":
        if _PASSPORT_FORM_RE.search(w) and not _PASSPORT_SCAN_RE.search(w):
            return True
        return False
    if kid == "work_proof":
        # 「工作经历请按职务职责改写」是正文表，不是工作证明扫描件
        if "证明" not in w and re.search(r"职务职责|贡献改写|限\s*\d+\s*字", w):
            return True
        return False
    return False


def _local_id_cands(app_no: str, extra_ids=None) -> list:
    out, seen = [], set()
    for raw in list(extra_ids or []) + [app_no]:
        for cand in (str(raw or "").strip(), P.norm_attach_id(raw)):
            if not cand or cand in seen:
                continue
            seen.add(cand)
            out.append(cand)
            stripped = cand.lstrip("0") or cand
            if stripped not in seen:
                seen.add(stripped)
                out.append(stripped)
            if cand.isdigit():
                for n in (4, 5, 6):
                    pad = cand.zfill(n)
                    if pad not in seen:
                        seen.add(pad)
                        out.append(pad)
    return out


def _person_dirs(ids: list) -> list:
    root = LOCAL_ATTACH_ROOT
    if not root.is_dir():
        return []
    found, seen = [], set()
    for cand in ids or []:
        for name in (cand, str(cand).zfill(4), str(cand).zfill(5), str(cand).lstrip("0") or cand):
            p = root / str(name)
            try:
                key = str(p.resolve()) if p.exists() else ""
            except OSError:
                key = ""
            if p.is_dir() and key and key not in seen:
                seen.add(key)
                found.append(p)
    return found


def _classify_local_file(folder: str, filename: str) -> str:
    """文件名优先；否则按所在子目录。学历证明即使放在护照夹也归学历。"""
    blob = str(filename or "")
    folder = str(folder or "")
    name_hits = []
    for kind in KINDS:
        kid = kind["id"]
        if any(kw and kw.lower() in blob.lower() for kw in kind["keys"]):
            if kid == "passport" and _skip_form_field(kind, blob):
                continue
            name_hits.append(kid)
    if name_hits:
        if "education" in name_hits:
            return "education"
        if "work_proof" in name_hits:
            return "work_proof"
        return name_hits[0]
    for kid, names in KIND_FOLDERS.items():
        if folder in names or any(n and n in folder for n in names):
            return kid
    for kind in KINDS:
        if any(kw and kw in folder for kw in kind["keys"]):
            return kind["id"]
    return ""


def scan_local_attachments(app_no: str, extra_ids=None, kinds=None) -> tuple[list, list]:
    """在项目「附件/{编号}/{护照|工作经历}/」中查找。"""
    notes = []
    root = LOCAL_ATTACH_ROOT
    if not root.is_dir():
        notes.append("本地附件目录不存在：" + str(root))
        return [], notes
    ids = _local_id_cands(app_no, extra_ids)
    dirs = _person_dirs(ids)
    if not dirs:
        notes.append("本地附件未找到编号目录（" + " / ".join(ids[:8] or [str(app_no or "")]) + "）")
        return [], notes
    notes.append("本地附件命中目录：" + "、".join(d.name for d in dirs))
    want = {k["id"]: k for k in (kinds or KINDS)}
    acc, seen = [], set()
    for person in dirs:
        for fp in person.rglob("*"):
            if not fp.is_file() or fp.suffix.lower() not in LOCAL_EXT:
                continue
            kid = _classify_local_file(fp.parent.name, fp.name)
            if not kid or kid not in want:
                continue
            key = str(fp.resolve())
            if key in seen:
                continue
            seen.add(key)
            acc.append({
                "kind_id": kid,
                "kind": want[kid]["label"],
                "filename": fp.name,
                "path": key,
            })
    if acc:
        notes.append("本地附件命中 " + str(len(acc)) + " 个文件")
    else:
        notes.append("本地编号目录内没有匹配的护照/学历证明/工作经历证明等文件")
    return acc, notes


_SKIP_CHAT_FILE = re.compile(r"申报书|申请表|修改意见|对照表|任务清单|修改后|_备份|意见反馈")


def _wecom_blob(hit: dict) -> str:
    return " ".join(str((hit or {}).get(k) or "") for k in (
        "attachment_name", "filename", "text", "snippet", "session_name", "sender",
    ))


def _wecom_person_ok(hit: dict, ids, names) -> bool:
    blob = _wecom_blob(hit)
    for aid in ids or []:
        if aid and str(aid) in blob:
            return True
    for n in names or []:
        n = str(n or "").strip()
        if len(n) >= 2 and n in blob:
            return True
    return False


async def scan_wecom_attachments(app_no: str, extra_ids=None, names=None, kinds=None) -> tuple[list, list]:
    """本机与人才库都没有时，按编号/姓名在聊天记录里找护照、工作证明等文件。"""
    from . import wecom_client as W
    from .wecom_cases import strip_cache_prefix
    notes = []
    want = {k["id"]: k for k in (kinds or KINDS)}
    if not want:
        return [], notes
    ids = _local_id_cands(app_no, extra_ids)[:4]
    name_list, seen_n = [], set()
    for raw in names or []:
        for n in re.split(r"[/／,，;；]", str(raw or "")):
            n = n.strip()
            if n and n not in seen_n and n not in ("***",):
                seen_n.add(n)
                name_list.append(n)
    name_list = name_list[:4]
    if not ids and not name_list:
        notes.append("无人才编号/姓名，未在聊天记录中检索附件")
        return [], notes
    qs = []
    def addq(q):
        q = re.sub(r"\s+", " ", str(q or "")).strip()
        if q and q not in qs and len(q) >= 2:
            qs.append(q)
    for aid in ids[:3]:
        addq(aid)
    for n in name_list[:2]:
        addq(n[:20])
    for k in want.values():
        lab = str(k.get("label") or "")
        if lab:
            addq(lab)
            if ids:
                addq(str(ids[0]) + " " + lab)
            elif name_list:
                addq(name_list[0][:12] + " " + lab)
    qs = qs[:8]
    async def one(q):
        try:
            return await W.search(q, limit=50)
        except W.WecomError as e:
            return {"items": [], "error": e.message}
    batches = await asyncio.gather(*[one(q) for q in qs]) if qs else []
    if batches and all(b.get("error") and not b.get("items") for b in batches):
        notes.append("聊天记录未检索：" + str(batches[0].get("error") or "")[:120])
        return [], notes
    acc, seen = [], set()
    per_kind = {}
    for data in batches:
        for hit in data.get("items") or []:
            if not isinstance(hit, dict):
                continue
            raw_name = str(hit.get("attachment_name") or hit.get("filename") or "").strip()
            if not raw_name:
                continue
            mid = hit.get("message_id") or 0
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                mid = 0
            name = strip_cache_prefix(raw_name, mid)
            if _SKIP_CHAT_FILE.search(name):
                continue
            suf = Path(name).suffix.lower()
            if suf and suf not in LOCAL_EXT:
                continue
            kid = _classify_local_file("", name)
            if not kid or kid not in want:
                continue
            if not _wecom_person_ok(hit, ids, name_list):
                continue
            sid = str(hit.get("source_id") or "").strip()
            cid = str(hit.get("session_id") or "").strip()
            key = (sid, mid or name.lower())
            if key in seen:
                continue
            seen.add(key)
            if per_kind.get(kid, 0) >= 4:
                continue
            per_kind[kid] = per_kind.get(kid, 0) + 1
            acc.append({
                "kind_id": kid,
                "kind": want[kid]["label"],
                "filename": name,
                "source_id": sid,
                "message_id": mid,
                "session_id": cid,
                "session_name": str(hit.get("session_name") or ""),
                "copies": [{
                    "source_id": sid,
                    "message_id": mid,
                    "session_id": cid,
                    "source_label": str(hit.get("source_name") or ""),
                }] if sid and mid else [],
            })
    if acc:
        notes.append("聊天记录命中 " + str(len(acc)) + " 个附件")
    else:
        notes.append("聊天记录未找到与编号/姓名匹配的护照/学历证明/工作经历证明等文件")
    return acc, notes


def _safe_local_file(path: str) -> Path:
    p = Path(str(path or "")).resolve()
    root = LOCAL_ATTACH_ROOT.resolve()
    if p != root and root not in p.parents:
        raise P.PapersError("BAD_FILE", "本地附件路径非法")
    if not p.is_file():
        raise P.PapersError("BAD_FILE", "本地附件不存在：" + p.name)
    return p


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
    pk = str(file_item.get("poolKind") or "")
    mapped = KIND_POOL_ID.get(kind["id"])
    if pk and mapped:
        return pk == mapped
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


async def _talent_pack_files(snap: dict, app_no: str) -> tuple[list, list]:
    acc, notes = [], []
    if not load_config().get("poolConfigured"):
        return acc, notes
    ids, extra = await POOL.resolve_attach_ids(snap, app_no)
    notes.extend(extra)
    if not ids:
        if not extra:
            notes.append("无法确定人才编号，未查人才库附件包")
        return acc, notes
    last_err = ""
    for aid in ids:
        try:
            pack = await POOL.talent_files_detail(aid)
        except POOL.PoolError as e:
            last_err = "人才库附件包 attach_id=" + aid + "：" + e.code + " " + e.message
            notes.append(last_err)
            if e.code in ("NOT_CONFIGURED", "AUTH", "INVALID_API_KEY", "SCOPE_DENIED", "FORBIDDEN"):
                break
            continue
        if not pack:
            last_err = "人才库附件包 attach_id=" + aid + " 无文件"
            notes.append(last_err)
            continue
        req_by_name = {}
        for it in ((pack.get("required") or {}).get("items") or []):
            if not isinstance(it, dict):
                continue
            hit = str(it.get("hit") or "").strip()
            if hit:
                req_by_name[hit] = it
        n = 0
        for f in pack.get("files") or []:
            if not isinstance(f, dict) or str(f.get("type") or "file") == "dir":
                continue
            name = str(f.get("name") or "").strip()
            rel = str(f.get("rel_path") or "").strip()
            if not name or not rel:
                continue
            req = req_by_name.get(name) or {}
            acc.append({
                "url": POOL.talent_file_url(aid, rel),
                "filename": name,
                "title": name,
                "kind_raw": str(req.get("label") or ""),
                "file_id": rel,
                "path": rel,
                "doi": "",
                "poolKind": str(req.get("id") or ""),
            })
            n += 1
        if n:
            notes.append("人才库附件包 attach_id=" + aid + " 共 " + str(n) + " 个文件")
            break
        last_err = "人才库附件包 attach_id=" + aid + " 列表为空"
        notes.append(last_err)
    if not acc and last_err and last_err not in notes:
        notes.append(last_err)
    return acc, notes


def _attach_ids(snap: dict, app_no: str) -> list:
    ids, seen = [], set()
    keys = (snap or {}).get("keys") or {}
    talent = (snap or {}).get("talent") or {}
    for raw in list(keys.get("attachIds") or []) + [POOL.talent_attach_id(talent), app_no]:
        aid = P.norm_attach_id(raw)
        if aid and aid not in seen:
            seen.add(aid)
            ids.append(aid)
    return ids


def _new_id() -> str:
    return "a" + secrets.token_hex(3)


def _public_item(tid: str, fid: str, *, kind: str, source: str, filename: str, title: str, note: str = "", local_path: str = ""):
    d = {
        "id": fid,
        "kind": kind,
        "source": source,
        "filename": filename,
        "title": title or filename,
        "download": "/api/tasks/" + tid + "/ext-files/" + fid,
        "found": True,
        "note": note,
    }
    if local_path:
        d["localPath"] = local_path
    return d


def _private_item(*, source: str, url: str, filename: str, path: str = "", copies=None):
    d = {"source": source, "url": url, "filename": filename}
    if path:
        d["path"] = path
    if copies:
        d["copies"] = copies
    return d


async def resolve_missing(tid: str, texts, snap: dict, app_no: str, prev: dict | None = None, app_text: str = "", task_dir: str | Path | None = None) -> dict:
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
        "codebuddyFetched": False,
        "codebuddyError": "",
        "generateFetched": False,
        "generateError": "",
    }
    labels = [k["label"] for k in needed]
    result["needed"] = labels
    if not needed:
        result["summary"] = "修改意见未提到缺失附件"
        return result

    by_kind = {lab: [] for lab in labels}
    for it in result.get("items") or []:
        by_kind.setdefault(it.get("kind") or "", []).append(it)
    known_urls = {str(v.get("url") or v.get("path") or "") for v in (result.get("private") or {}).values() if v}
    local_found_ids = set()
    extra_ids = _attach_ids(snap, app_no)
    local_files, local_notes = scan_local_attachments(app_no, extra_ids, kinds=needed)
    result["notes"] = list(result.get("notes") or []) + local_notes
    for f in local_files:
        key = "local:" + str(f.get("path") or "")
        if key in known_urls:
            continue
        known_urls.add(key)
        fid = _new_id()
        pub = _public_item(
            tid, fid,
            kind=str(f.get("kind") or "附件"), source="local",
            filename=str(f.get("filename") or "file"),
            title=str(f.get("filename") or ""),
            note="本地附件目录",
            local_path=str(f.get("path") or ""),
        )
        result["items"].append(pub)
        result["private"][fid] = _private_item(
            source="local", url="", filename=pub["filename"], path=str(f.get("path") or ""),
        )
        by_kind.setdefault(pub["kind"], []).append(pub)
        if f.get("kind_id"):
            local_found_ids.add(f["kind_id"])

    pack_files, pack_notes = await _talent_pack_files(snap, app_no)
    result["notes"] = list(result.get("notes") or []) + pack_notes
    pool_files = pack_files + _pool_files(snap)

    for kind in needed:
        if kind["id"] in local_found_ids and by_kind.get(kind["label"]):
            continue
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

    missing_kinds = [
        k for k in needed
        if k["id"] not in local_found_ids and not by_kind.get(k["label"])
    ]
    if missing_kinds:
        names = list(((snap or {}).get("keys") or {}).get("names") or [])
        pn = PP.person_name(snap)
        if pn:
            names = [pn] + names
        chat_files, chat_notes = await scan_wecom_attachments(
            app_no, extra_ids, names, kinds=missing_kinds,
        )
        result["notes"] = list(result.get("notes") or []) + chat_notes
        for f in chat_files:
            key = "wecom:" + str(f.get("source_id") or "") + ":" + str(f.get("message_id") or f.get("filename") or "")
            if key in known_urls:
                continue
            known_urls.add(key)
            fid = _new_id()
            sess = str(f.get("session_name") or "")
            pub = _public_item(
                tid, fid,
                kind=str(f.get("kind") or "附件"), source="wecom",
                filename=str(f.get("filename") or "file"),
                title=str(f.get("filename") or ""),
                note="聊天记录" + ((" · " + sess) if sess else ""),
            )
            result["items"].append(pub)
            result["private"][fid] = _private_item(
                source="wecom", url="", filename=pub["filename"], copies=f.get("copies") or [],
            )
            by_kind.setdefault(pub["kind"], []).append(pub)

    paper_kind = next((k for k in needed if k["id"] == "paper"), None)
    pool_paper_ok = bool(by_kind.get("论文全文")) or ("paper" in local_found_ids)
    if paper_kind and not pool_paper_ok and not result.get("papersFetched"):
        result["papersFetched"] = True
        if not cfg.get("papersConfigured"):
            result["papersError"] = "论文 API 未配置（config.json papers.apiKey）"
            result["notes"].append(result["papersError"])
        else:
            ids, extra = await POOL.resolve_attach_ids(snap, app_no)
            result["notes"].extend(extra)
            if not ids:
                result["papersError"] = extra[-1] if extra else "无法确定人才编号，论文系统只能按 attach_id 查询"
                if result["papersError"] not in result["notes"]:
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

    project_kind = next((k for k in needed if k["id"] == "project"), None)
    pool_project_ok = bool(by_kind.get("项目证明")) or ("project" in local_found_ids)
    work_root = Path(task_dir) / "work" / "tmp" / "project_proof" if task_dir else Path(".")
    if project_kind and not pool_project_ok:
        person = PP.person_name(snap)
        projects = PP.extract_projects(snap, app_text)
        company = str(((snap or {}).get("keys") or {}).get("company") or "")
        ids, extra = await POOL.resolve_attach_ids(snap, app_no)
        result["notes"].extend(extra)
        aid = ids[0] if ids else str(app_no or "")
        if not result.get("codebuddyFetched"):
            result["codebuddyFetched"] = True
            cb = await asyncio.to_thread(
                PP.run_codebuddy_search,
                person=person, attach_id=aid, projects=projects,
                work_dir=work_root / "codebuddy",
            )
            if cb.get("error"):
                result["codebuddyError"] = str(cb.get("error") or "")
                result["notes"].append("项目证明联网检索：" + result["codebuddyError"])
            n_cb = 0
            for f in cb.get("items") or []:
                u = str(f.get("url") or "")
                if not u or u in known_urls:
                    continue
                known_urls.add(u)
                fid = _new_id()
                pub = _public_item(
                    tid, fid,
                    kind="项目证明", source="codebuddy",
                    filename=str(f.get("filename") or "project-proof"),
                    title=str(f.get("title") or f.get("filename") or ""),
                    note=str(f.get("note") or "CodeBuddy 联网检索"),
                )
                result["items"].append(pub)
                result["private"][fid] = _private_item(source="codebuddy", url=u, filename=pub["filename"])
                by_kind.setdefault("项目证明", []).append(pub)
                n_cb += 1
            if n_cb:
                result["notes"].append("CodeBuddy 联网检索命中 " + str(n_cb) + " 个项目证明")
        if not by_kind.get("项目证明") and not result.get("generateFetched"):
            result["generateFetched"] = True
            gen = await PP.call_generate_api(
                person=person, attach_id=aid, company=company, projects=projects,
                work_dir=work_root / "generate",
                resume_pdf=PP.find_resume_pdf(task_dir),
            )
            if gen.get("error"):
                result["generateError"] = str(gen.get("error") or "")
                result["notes"].append("项目证明生成：" + result["generateError"])
            n_gen = 0
            for f in gen.get("items") or []:
                u = str(f.get("url") or "")
                if not u or u in known_urls:
                    continue
                known_urls.add(u)
                fid = _new_id()
                pub = _public_item(
                    tid, fid,
                    kind="项目证明", source="generate",
                    filename=str(f.get("filename") or "project-proof"),
                    title=str(f.get("title") or f.get("filename") or ""),
                    note=str(f.get("note") or "生成 API"),
                )
                result["items"].append(pub)
                result["private"][fid] = _private_item(source="generate", url=u, filename=pub["filename"])
                by_kind.setdefault("项目证明", []).append(pub)
                n_gen += 1
            if n_gen:
                result["notes"].append("生成 API 产出 " + str(n_gen) + " 个项目证明")

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
                src = {"pool": "人才库", "papers": "论文系统", "codebuddy": "联网检索", "generate": "生成接口", "local": "本地附件", "wecom": "聊天记录"}.get(it.get("source"), str(it.get("source") or "外部"))
                bits.append(src + " " + str(it.get("filename") or it.get("title") or "") + " " + str(it.get("download") or ""))
            lines.append("【缺附件·" + lab + "】已检索到，下载：" + " ； ".join(bits))
        else:
            extra = ""
            if lab == "论文全文" and result.get("papersError"):
                extra = "。" + str(result.get("papersError"))
            elif lab == "项目证明":
                bits = [str(x) for x in (result.get("codebuddyError"), result.get("generateError")) if x]
                extra = "。" + "；".join(bits) if bits else ""
            elif not extra and result.get("notes"):
                extra = "。" + "；".join(str(x) for x in result.get("notes") if lab in str(x) or (lab == "论文全文" and "论文" in str(x)))
            lines.append("【缺附件·" + lab + "】未检索到可下载文件" + extra)
    seen = set()
    uniq = []
    for s in lines:
        k = re.sub(r"\s+", "", s)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
    return uniq


def _task_snip(texts: list, kind: dict, limit: int = 90) -> str:
    keys = list((kind or {}).get("keys") or ())
    for raw in texts or []:
        s = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not s:
            continue
        for kw in keys:
            i = s.lower().find(kw.lower()) if str(kw).isascii() else s.find(kw)
            if i < 0:
                continue
            window = s[max(0, i - 16): i + len(kw) + 48]
            if _skip_form_field(kind, window):
                continue
            return window[:limit]
    return ""


def build_task_list(texts=None, *, attach: dict | None = None, leftovers=None, clauses=None) -> list:
    """护照 / 学历证明 / 工作经历证明等无法改申报书正文的事项 → 人工任务清单。"""
    rows = []
    for c in clauses or []:
        if isinstance(c, dict):
            rows.append(c.get("opinion") or "")
            rows.append(c.get("clause") or "")
        else:
            rows.append(c)
    rows.extend(texts or [])
    rows.extend(leftovers or [])
    blob_parts = [str(x or "").strip() for x in rows if str(x or "").strip()]
    kinds = extract_needed_kinds(blob_parts)
    attach = attach if isinstance(attach, dict) else {}
    needed_labels = list(attach.get("needed") or [])
    by_label = {k["label"]: k for k in KINDS}
    extra = []
    for lab in needed_labels:
        k = by_label.get(str(lab))
        if k and k not in kinds:
            extra.append(k)
    kinds = kinds + extra
    items_by = {}
    for it in attach.get("items") or []:
        if not isinstance(it, dict):
            continue
        items_by.setdefault(str(it.get("kind") or "附件"), []).append(it)
    out, seen = [], set()
    for kind in kinds:
        kid = str(kind.get("id") or "")
        if not kid or kid in seen:
            continue
        seen.add(kid)
        lab = str(kind.get("label") or kid)
        hits = list(items_by.get(lab) or [])
        if kid == "paper":
            for k, rows2 in items_by.items():
                if k != lab and "论文" in str(k):
                    hits.extend(rows2)
        downloads = []
        for it in hits:
            downloads.append({
                "filename": it.get("filename") or it.get("title") or "",
                "title": it.get("title") or it.get("filename") or "",
                "download": it.get("download") or "",
                "source": it.get("source") or "",
                "localPath": it.get("localPath") or "",
            })
        found = bool(downloads)
        srcs = {str(d.get("source") or "") for d in downloads}
        action = _TASK_ACTION.get(kid) or (lab + "无法通过系统改申报书正文，请按意见另行准备材料。")
        if "local" in srcs:
            status, status_label = "found", "本地附件已找到"
            action += " 已在本机「附件」目录找到文件，请下载核对后按意见替换或补传。"
        elif "wecom" in srcs:
            status, status_label = "found", "聊天记录已找到"
            action += " 本地目录与人才库未找到，已在企业微信聊天记录中找到文件，请下载核对后按意见替换或补传。"
        elif found:
            status, status_label = "found", "人才库已找到"
            action += " 本地目录未找到，已从人才库检索到参考文件，请人工核对。"
        else:
            status, status_label = "need_upload", "本地、人才库与聊天记录均未找到"
            action += " 本地「附件」目录、人才库与聊天记录均无对应文件，需申报人自行准备。"
        out.append({
            "id": kid,
            "title": lab,
            "action": action,
            "status": status,
            "statusLabel": status_label,
            "snippet": _task_snip(blob_parts, kind),
            "downloads": downloads,
        })
    return out


def public_task_list(items: list) -> list:
    keys = ("id", "title", "action", "status", "statusLabel", "snippet", "downloads")
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({k: it.get(k) for k in keys})
    return out


def format_task_list_md(items: list, *, app_name: str = "", app_no: str = "") -> str:
    lines = ["# 任务清单（系统无法修改的附件）", ""]
    if app_name or app_no:
        lines.append("申报书：" + (app_name or "—") + (("　编号 " + app_no) if app_no else ""))
        lines.append("")
    lines.append("护照、学历证明、工作经历证明等扫描件/附件不能写入申报书正文（含证明上的时间、日期）。请按下列事项人工补传或替换。")
    lines.append("")
    if not items:
        lines.append("（本任务修改意见未点名需另行办理的附件）")
        return "\n".join(lines) + "\n"
    for i, it in enumerate(items, 1):
        lines.append("## " + str(i) + ". " + str(it.get("title") or "附件"))
        lines.append("- 状态：" + str(it.get("statusLabel") or it.get("status") or ""))
        if it.get("snippet"):
            lines.append("- 意见摘录：" + str(it.get("snippet")))
        lines.append("- 办理说明：" + str(it.get("action") or ""))
        dls = it.get("downloads") or []
        if dls:
            lines.append("- 参考下载：")
            for d in dls:
                name = d.get("filename") or d.get("title") or "文件"
                src = {"local": "本地附件", "pool": "人才库", "papers": "论文系统", "wecom": "聊天记录"}.get(d.get("source"), d.get("source") or "")
                href = d.get("download") or ""
                loc = d.get("localPath") or ""
                bit = "  - " + (("[" + src + "] ") if src else "") + name
                if href:
                    bit += " → " + href
                if loc:
                    bit += "　本地 " + loc
                lines.append(bit)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_attach_prompt(result: dict) -> str:
    if not result or not result.get("needed"):
        return "（修改意见未提到缺失附件）"
    lines = ["## 缺失附件检索结果", "意见点名：" + "、".join(result.get("needed") or [])]
    items = result.get("items") or []
    if items:
        lines.append("已找到下载（leftovers 必须写入这些链接，不要只写无法在正文完成）：")
        for it in items:
            lines.append("- " + str(it.get("kind") or "") + " " + str(it.get("title") or it.get("filename") or "") + " → " + str(it.get("download") or ""))
    else:
        lines.append("库内、联网检索与生成接口均未给出可下载文件。leftovers 写明缺哪类附件，严禁编造已上传。")
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
        "codebuddyFetched": bool(result.get("codebuddyFetched")),
        "codebuddyError": result.get("codebuddyError") or "",
        "generateFetched": bool(result.get("generateFetched")),
        "generateError": result.get("generateError") or "",
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
        "items": [{k: it.get(k) for k in ("id", "kind", "source", "filename", "title", "download", "found", "note", "localPath")} for it in (result.get("items") or [])],
        "notes": result.get("notes") or [],
    }


def public_attach_hit(result: dict) -> dict:
    block = public_plan_block(result)
    block["found"] = len(block.get("items") or [])
    return block


async def fetch_upstream(priv: dict):
    """按 private 记录向上游取文件，返回 (content, filename, content_type, status)."""
    if not isinstance(priv, dict):
        raise P.PapersError("BAD_FILE", "没有下载地址")
    cfg = load_config()
    source = str(priv.get("source") or "")
    url = str(priv.get("url") or "")
    headers = {}
    if source == "local" or priv.get("path"):
        p = _safe_local_file(str(priv.get("path") or ""))
        fn = str(priv.get("filename") or p.name)
        suf = p.suffix.lower()
        ctype = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".webp": "image/webp",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".zip": "application/zip",
        }.get(suf, "application/octet-stream")
        return p.read_bytes(), fn, ctype, 200
    if source == "wecom":
        from . import wecom_client as W
        copies = priv.get("copies") if isinstance(priv.get("copies"), list) else []
        if not copies:
            sid = str(priv.get("source_id") or "")
            try:
                mid = int(priv.get("message_id") or 0)
            except (TypeError, ValueError):
                mid = 0
            if sid and mid:
                copies = [{"source_id": sid, "message_id": mid, "session_id": str(priv.get("session_id") or "")}]
        got = await W.fetch_attachment_any(copies, wait_s=25)
        kind = str(got.get("kind") or "")
        if kind == "pending":
            raise P.PapersError("NOT_READY", str(got.get("detail") or "聊天记录附件正在回传，请稍后重试"), 409)
        if kind != "file" or not got.get("content"):
            raise P.PapersError("BAD_FILE", str(got.get("detail") or "聊天记录附件不在缓存"))
        fn = str(priv.get("filename") or got.get("filename") or "file")
        ctype = str(got.get("content_type") or "application/octet-stream")
        return got["content"], fn, ctype, 200
    if not url:
        raise P.PapersError("BAD_FILE", "没有下载地址")
    if source == "papers":
        if not cfg.get("papersConfigured"):
            raise P.PapersError("NOT_CONFIGURED", "未配置论文系统")
        full = P.abs_url(cfg["papersBaseUrl"], url)
        if cfg.get("papersApiKey"):
            url = full
            headers = {"X-Api-Key": cfg["papersApiKey"]}
        else:
            content, fn, ctype, status = await P.fetch_file(full)
            fn = str(priv.get("filename") or fn)
            return content, fn, ctype, status
    elif source in ("codebuddy", "generate") and not re.match(r"https?://", url, re.I):
        local = Path(url[7:] if url.startswith("file://") else url)
        if not local.is_file():
            raise P.PapersError("BAD_FILE", "本地项目证明不存在：" + str(local))
        fn = str(priv.get("filename") or local.name)
        ctype = "application/pdf" if local.suffix.lower() == ".pdf" else "application/octet-stream"
        return local.read_bytes(), fn, ctype, 200
    elif source == "generate":
        gen = (cfg.get("projectProof") or {}).get("generate") or {}
        if not gen.get("configured"):
            raise P.PapersError("NOT_CONFIGURED", "未配置项目证明生成 API")
        url = P.abs_url(gen.get("baseUrl") or "", url)
        headers = PP.generate_headers(gen)
        key = str(gen.get("apiKey") or "")
        if key and "X-Api-Key" not in headers and "Authorization" not in headers:
            headers["X-Api-Key"] = key
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
