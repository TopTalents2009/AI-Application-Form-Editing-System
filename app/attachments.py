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
    "conversion": "成果转化证明（专利许可或转让协议、临床试验批件、孤儿药认定函等）无法写入申报书正文。请按意见另行补传扫描件，系统不能代替上传。",
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
    {"id": "project", "label": "项目证明", "keys": ("项目证明", "项目材料", "项目论文", "项目模块", "项目扫描", "立项批文", "立项证明", "主持项目证明", "科研项目证明")},
    {"id": "conversion", "label": "成果转化证明", "keys": ("成果转化证明", "成果转化", "转化证明")},
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
    "conversion": ("成果转化", "转化证明"),
}
LOCAL_ATTACH_ROOT = Path(__file__).resolve().parent.parent / "附件"
LOCAL_EXT = {".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".zip", ".xls", ".xlsx"}

URL_KEYS = ("url", "file_url", "download_url", "pdf_url", "href", "link", "path", "file_path", "download")
NAME_KEYS = ("filename", "file_name", "name", "title", "title_zh", "原始文件名", "文件名", "附件名称")
KIND_KEYS = ("kind", "type", "category", "doc_type", "附件类型", "材料类型", "label", "分类")
ID_KEYS = ("file_id", "fileId", "id", "attachment_id")
PAPER_PATH = re.compile(r"paper|publication|论著|论文|著作|科研成果", re.I)
_CONVERSION_PROOF_RE = re.compile(
    r"成果转化.{0,16}(?:缺少|缺失|缺|需要|需补|未提供|未上传|没有).{0,8}证明"
    r"|(?:缺少|缺失|需要|需补|未提供).{0,8}成果转化.{0,12}证明"
    r"|转化证明.{0,10}(?:缺少|缺失|未提供|未上传|需补|请补)"
)
EXT_OK = re.compile(r"\.(pdf|docx?|jpe?g|png|tif{1,2}|webp|zip)$", re.I)


def classify_talent_filename(filename: str) -> tuple[str, str]:
    """聊天文件名归入人才附件类别。申报书、修改意见优先，材料名再按最长关键词。"""
    from .wecom_cases import classify_file

    name = str(filename or "").strip()
    kind = classify_file(name)
    if kind == "opinion":
        return "opinion", "修改意见"
    hit_id, hit_label, hit_len = "", "", 0
    low = name.lower()
    for item in KINDS:
        words = list(item["keys"]) + list(KIND_FOLDERS.get(item["id"]) or ())
        for kw in words:
            token = str(kw or "").strip()
            if len(token) < 2 or token.lower() not in low:
                continue
            if len(token) > hit_len:
                hit_id, hit_label, hit_len = item["id"], item["label"], len(token)
    explicit_app = bool(re.search(r"申报书|申请表|application\s*form", name, re.I))
    if kind == "app" and (explicit_app or not hit_id):
        return "app", "申报书"
    if hit_id:
        return hit_id, hit_label
    return "other", "其他"


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
    if kind["id"] == "conversion":
        return bool(_CONVERSION_PROOF_RE.search(blob or ""))
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
        if re.search(
            r"项目论文|补充.{0,24}项目.{0,16}(材料|证明|模块)|项目.{0,6}模块|两个模块|两个栏目",
            blob,
        ) and NEED_RE.search(blob):
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
            mid_key = (sid, mid) if mid else (sid, name.lower())
            file_key = (cid or sid, name.lower())
            if mid_key in seen:
                continue
            seen.add(mid_key)
            copy = {
                "source_id": sid,
                "message_id": mid,
                "session_id": cid,
                "source_label": str(hit.get("source_name") or ""),
            } if sid and mid else None
            existed = next(
                (
                    i for i, row in enumerate(acc)
                    if (str(row.get("session_id") or row.get("source_id") or ""), str(row.get("filename") or "").lower()) == file_key
                ),
                -1,
            )
            if existed >= 0:
                if copy:
                    acc[existed].setdefault("copies", []).append(copy)
                continue
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
                "copies": [copy] if copy else [],
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


def _name_key(source: str, filename: str, kind: str = "") -> str:
    return "name:" + str(source or "") + ":" + str(kind or "") + ":" + str(filename or "").strip().lower()


def _wecom_key(source_id, message_id) -> str:
    return "wecom:" + str(source_id or "") + ":" + str(message_id or "")


def _seed_known_keys(result: dict) -> set:
    """第二次补检索时还原已收录文件的去重键（含本地路径、聊天 copies、文件名）。"""
    keys = set()
    for it in (result.get("items") or []):
        if not isinstance(it, dict):
            continue
        fn = str(it.get("filename") or it.get("title") or "")
        src = str(it.get("source") or "")
        kind = str(it.get("kind") or "")
        if fn:
            keys.add(_name_key(src, fn, kind))
            keys.add(_name_key(src, fn, ""))
        dl = str(it.get("download") or "")
        if dl:
            keys.add(dl)
        lp = str(it.get("localPath") or "")
        if lp:
            keys.add(lp)
            keys.add("local:" + lp)
    for v in (result.get("private") or {}).values():
        if not isinstance(v, dict):
            continue
        url = str(v.get("url") or "")
        path = str(v.get("path") or "")
        fn = str(v.get("filename") or "")
        src = str(v.get("source") or "")
        if url:
            keys.add(url)
        if path:
            keys.add(path)
            keys.add("local:" + path)
        if fn:
            keys.add(_name_key(src, fn, ""))
        copies = v.get("copies") if isinstance(v.get("copies"), list) else []
        for c in copies:
            if not isinstance(c, dict):
                continue
            keys.add(_wecom_key(c.get("source_id"), c.get("message_id")))
        if src == "wecom":
            keys.add(_wecom_key(v.get("source_id"), v.get("message_id")))
    keys.discard("")
    return keys


def _add_notes(result: dict, extra) -> None:
    cur = list(result.get("notes") or [])
    have = set(cur)
    for n in extra or []:
        s = str(n or "").strip()
        if s and s not in have:
            cur.append(s)
            have.add(s)
    result["notes"] = cur


def _dedupe_attach_items(items: list) -> list:
    out, seen = [], set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        fn = str(it.get("filename") or it.get("title") or "").strip().lower()
        k = (str(it.get("source") or ""), str(it.get("kind") or ""), fn)
        if not fn or k in seen:
            if not fn:
                out.append(it)
            continue
        seen.add(k)
        out.append(it)
    return out


def _papers_json_from_app(app_text: str) -> list:
    text = str(app_text or "").strip()
    if not text:
        return []
    try:
        from .template_fill import _parse_papers
        rows = _parse_papers(text)
    except Exception:
        return []
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        title = str(r.get("论文题目") or r.get("title") or "").strip()
        if len(title) < 8:
            continue
        item = {"title": title}
        journal = str(r.get("发表载体") or r.get("journal") or "").strip()
        if journal:
            item["journal"] = journal
        year = str(r.get("发表时间") or r.get("year") or "")
        m = re.search(r"(20\d{2}|19\d{2})", year)
        if m:
            item["year"] = m.group(1)
        authors = str(r.get("作者") or r.get("authors") or "").strip()
        if authors:
            item["authors"] = authors
        out.append(item)
    return out[:12]


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
    result.setdefault("items", [])
    result.setdefault("private", {})
    result.setdefault("notes", [])
    result.setdefault("paperRecords", [])
    if not needed:
        result["summary"] = "修改意见未提到缺失附件"
        return result

    by_kind = {lab: [] for lab in labels}
    label_ids = {k["label"]: k["id"] for k in KINDS}
    local_found_ids = set()
    for it in result.get("items") or []:
        if not isinstance(it, dict):
            continue
        lab = str(it.get("kind") or "")
        by_kind.setdefault(lab, []).append(it)
        kid = label_ids.get(lab)
        if kid and str(it.get("source") or "") == "local":
            local_found_ids.add(kid)
    known_urls = _seed_known_keys(result)
    extra_ids = _attach_ids(snap, app_no)
    local_files, local_notes = scan_local_attachments(app_no, extra_ids, kinds=needed)
    _add_notes(result, local_notes)
    for f in local_files:
        path = str(f.get("path") or "")
        fn = str(f.get("filename") or "file")
        kind_lab = str(f.get("kind") or "附件")
        key = "local:" + path
        nk = _name_key("local", fn, kind_lab)
        if key in known_urls or path in known_urls or nk in known_urls:
            if f.get("kind_id"):
                local_found_ids.add(f["kind_id"])
            continue
        known_urls.update({key, path, nk})
        fid = _new_id()
        pub = _public_item(
            tid, fid,
            kind=kind_lab, source="local",
            filename=fn,
            title=fn,
            note="本地附件目录",
            local_path=path,
        )
        result["items"].append(pub)
        result["private"][fid] = _private_item(
            source="local", url="", filename=pub["filename"], path=path,
        )
        by_kind.setdefault(pub["kind"], []).append(pub)
        if f.get("kind_id"):
            local_found_ids.add(f["kind_id"])

    pack_files, pack_notes = await _talent_pack_files(snap, app_no)
    _add_notes(result, pack_notes)
    pool_files = pack_files + _pool_files(snap)

    for kind in needed:
        if kind["id"] in local_found_ids and by_kind.get(kind["label"]):
            continue
        hits = [f for f in pool_files if _match_kind(f, kind)]
        for f in hits:
            if not f.get("url"):
                _add_notes(result, [kind["label"] + " 库内有文件名「" + str(f.get("filename") or "") + "」但无下载地址"])
                continue
            url = str(f.get("url") or "")
            fn = str(f.get("filename") or "file")
            nk = _name_key("pool", fn, kind["label"])
            if url in known_urls or nk in known_urls:
                continue
            known_urls.update({url, nk})
            fid = _new_id()
            pub = _public_item(
                tid, fid,
                kind=kind["label"], source="pool",
                filename=fn,
                title=str(f.get("title") or fn),
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
        _add_notes(result, chat_notes)
        for f in chat_files:
            sid = str(f.get("source_id") or "")
            mid = str(f.get("message_id") or f.get("filename") or "")
            fn = str(f.get("filename") or "file")
            kind_lab = str(f.get("kind") or "附件")
            key = _wecom_key(sid, mid)
            nk = _name_key("wecom", fn, kind_lab)
            copy_hit = False
            for c in (f.get("copies") or []):
                if not isinstance(c, dict):
                    continue
                if _wecom_key(c.get("source_id"), c.get("message_id")) in known_urls:
                    copy_hit = True
                    break
            if key in known_urls or nk in known_urls or copy_hit:
                continue
            known_urls.update({key, nk})
            fid = _new_id()
            sess = str(f.get("session_name") or "")
            pub = _public_item(
                tid, fid,
                kind=kind_lab, source="wecom",
                filename=fn,
                title=fn,
                note="聊天记录" + ((" · " + sess) if sess else ""),
            )
            result["items"].append(pub)
            result["private"][fid] = _private_item(
                source="wecom", url="", filename=pub["filename"], copies=f.get("copies") or [],
            )
            by_kind.setdefault(pub["kind"], []).append(pub)

    paper_kind = next((k for k in needed if k["id"] == "paper"), None)
    if paper_kind and not result.get("papersFetched"):
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
                papers_json = _papers_json_from_app(app_text)
                person = str(PP.person_name(snap) or "")
                names = list(((snap or {}).get("keys") or {}).get("names") or [])
                for aid in ids:
                    try:
                        data, built_note = await P.ensure_talent(
                            aid,
                            papers_json=papers_json or None,
                            name=person,
                            names=names,
                        )
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
                    if built_note:
                        result["notes"].append(built_note)
                    result["papersAttachId"] = aid
                    recs = P.public_catalog(data)
                    if recs:
                        result["paperRecords"] = recs
                    files = P.public_files(data, cfg.get("papersBaseUrl") or "")
                    att = data.get("attachment") if isinstance(data.get("attachment"), dict) else {}
                    if att and not att.get("ready"):
                        result["notes"].append("论文系统档案已找到（" + aid + "）但装订附件尚未生成（可稍后下载）")
                    if not files:
                        last_err = "论文系统有档案但暂无单篇 PDF attach_id=" + aid
                        result["notes"].append(last_err)
                    for f in files:
                        fn = str(f.get("filename") or "paper.pdf")
                        if Path(fn).suffix.lower() in (".json", ".yaml", ".yml", ".txt", ".log"):
                            continue
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
                            filename=fn,
                            title=str(f.get("title") or ""),
                            note="论文系统 attach_id=" + aid,
                        )
                        for k in ("year", "journal", "doi"):
                            v = str(f.get(k) or "").strip()
                            if v:
                                pub[k] = v
                        result["items"].append(pub)
                        result["private"][fid] = _private_item(
                            source="papers", url=str(f.get("url") or ""), filename=pub["filename"],
                        )
                        by_kind.setdefault(kind_label, []).append(pub)
                    break
                if not any(it.get("source") == "papers" for it in result["items"]) and last_err:
                    result["papersError"] = last_err
    if paper_kind and not result.get("paperRecords"):
        aid = str(result.get("papersAttachId") or "")
        if not aid:
            ids2, _ = await POOL.resolve_attach_ids(snap, app_no)
            aid = ids2[0] if ids2 else ""
        if aid:
            try:
                result["paperRecords"] = P.public_catalog(await P.get_talent(aid))
                result["papersAttachId"] = aid
            except P.PapersError:
                pass
        if not result.get("paperRecords"):
            recs = catalog_from_attach_items(result.get("items") or [])
            if recs:
                result["paperRecords"] = recs

    project_kind = next((k for k in needed if k["id"] == "project"), None)
    pool_project_ok = bool(by_kind.get("项目证明")) or ("project" in local_found_ids)
    work_root = Path(task_dir) / "work" / "tmp" / "project_proof" if task_dir else Path(".")
    opinion_blob = "\n".join(str(x or "") for x in (texts if isinstance(texts, (list, tuple)) else [texts]))
    want_search = bool(re.search(r"联网检索", opinion_blob))
    want_generate = bool(re.search(r"系统生成项目证明|生成项目证明|调用生成接口", opinion_blob))
    skip_search = bool(re.search(r"不必再联网检索|直接生成项目证明", opinion_blob)) and not want_search
    if project_kind:
        ident = PP.identity_from_app_text(app_text)
        prior = PP.prior_work_context(snap, app_text)
        person = prior.get("person") or PP.person_name(snap) or ident.get("name") or ""
        projects = prior.get("projects") if prior.get("ok") else PP.extract_projects(snap, app_text)
        company = str(prior.get("company") or "") if prior.get("ok") else ""
        ids, extra = await POOL.resolve_attach_ids(snap, app_no)
        result["notes"].extend(extra)
        aid = ids[0] if ids else (ident.get("attach_id") or str(app_no or ""))
        result["notes"].append(
            "项目证明策略：人才库=" + ("已命中" if pool_project_ok else "未命中")
            + "；联网检索=" + ("是" if (not skip_search) else "否")
            + "；生成接口=" + ("是" if prior.get("ok") else "否（无来华前工作单位）")
            + ("（意见点名生成）" if want_generate else "")
            + ("（意见点名检索）" if want_search else "")
        )
        if prior.get("note"):
            result["notes"].append(str(prior.get("note")))
        run_search = not skip_search
        if not result.get("codebuddyFetched"):
            result["codebuddyFetched"] = True
            if not run_search:
                result["notes"].append("意见要求直接生成项目证明，已跳过联网检索")
            else:
                result["notes"].append("开始联网检索项目证明（CodeBuddy）")
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
                else:
                    result["notes"].append("CodeBuddy 联网检索未命中公开证明，继续调用生成接口")
        if not result.get("generateFetched"):
            result["generateFetched"] = True
            if not prior.get("ok") or not company:
                if not prior.get("note"):
                    result["notes"].append("未找到来华前工作单位，已跳过项目证明生成")
                gen = {"ok": False, "error": "", "items": []}
            else:
                result["notes"].append("开始调用项目证明生成接口")
                gen = await PP.call_generate_api(
                    person=person, attach_id=aid, company=company, projects=projects,
                    work_dir=work_root / "generate",
                    resume_pdf=PP.find_resume_pdf(task_dir),
                    language=str(prior.get("language") or ""),
                    role=str(prior.get("role") or ""),
                    start_date=str(prior.get("startDate") or ""),
                    end_date=str(prior.get("endDate") or ""),
                    forbidden_names=prior.get("forbidden") or [],
                    aliases=prior.get("aliases") or [],
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

    result["items"] = _dedupe_attach_items(result.get("items") or [])
    found_n = len(result["items"])
    miss = [lab for lab in labels if not any(it.get("kind") == lab or (lab == "论文全文" and "论文" in str(it.get("kind") or "")) for it in result["items"])]
    parts = []
    if found_n:
        parts.append("已定位 " + str(found_n) + " 个附件下载")
    if miss:
        parts.append("未找到：" + "、".join(miss))
    result["summary"] = "；".join(parts) if parts else "未检索到可下载附件"
    return result


def catalog_from_attach_items(items) -> list:
    """已找到的论文下载项里抽出可写入申报书的题录（跳过文件名/装订件）。"""
    out, seen = [], set()
    skip_suf = {".pdf", ".log", ".txt", ".json", ".yaml", ".yml", ".doc", ".docx"}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "")
        if "论文" not in kind or "装订" in kind:
            continue
        title = str(it.get("title") or "").strip()
        fn = str(it.get("filename") or "").strip()
        if not title or title == fn:
            continue
        low = title.lower()
        if any(low.endswith(s) for s in skip_suf):
            continue
        if title.startswith("装订"):
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        rec = {"title": title, "titleZh": "", "year": str(it.get("year") or "").strip(),
               "journal": str(it.get("journal") or "").strip(), "doi": str(it.get("doi") or "").strip(),
               "authors": str(it.get("authors") or "").strip(), "paperId": ""}
        out.append(rec)
        if len(out) >= 12:
            break
    return out


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
            bits, seen_hit = [], set()
            for it in hits:
                hk = (str(it.get("source") or ""), str(it.get("filename") or it.get("title") or ""), str(it.get("download") or ""))
                if hk in seen_hit:
                    continue
                seen_hit.add(hk)
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
    recs = [r for r in (result.get("paperRecords") or []) if isinstance(r, dict) and str(r.get("title") or "").strip()]
    if recs:
        titles = [str(r.get("title") or "").strip()[:80] for r in recs]
        lines.append("【申报书·代表性论文】论文系统题录须写入申报书：" + "；".join(titles[:6]))
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
        downloads, seen_dl = [], set()
        for it in hits:
            fn = str(it.get("filename") or it.get("title") or "")
            dk = (str(it.get("source") or ""), fn.lower(), str(it.get("download") or ""))
            if dk in seen_dl:
                continue
            seen_dl.add(dk)
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
            if kid == "paper":
                if attach.get("papersFetched"):
                    err = str(attach.get("papersError") or "").strip()
                    status_label = "本地、人才库、聊天记录与论文系统均未找到"
                    action += " 已查询论文系统" + (("：" + err) if err else "（无该人才档案或无可下载 PDF）") + "。"
                else:
                    status_label = "本地、人才库与聊天记录均未找到（论文系统未查询）"
                    action += " 尚未查询论文系统。"
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
        needed = result.get("needed") or []
        if "论文全文" in needed:
            lines.append("本地、人才库与论文系统均未给出可下载论文。leftovers 写明缺论文全文，严禁编造已上传。")
        else:
            lines.append("库内、联网检索与生成接口均未给出可下载文件。leftovers 写明缺哪类附件，严禁编造已上传。")
    recs = result.get("paperRecords") or []
    if recs:
        lines.append("## 论文系统题录（须补入申报书「代表性论文」栏，禁止只给附件链接、禁止因缺人才库而 leftovers）")
        for i, r in enumerate(recs, 1):
            if not isinstance(r, dict):
                continue
            bits = ["题目=" + str(r.get("title") or "")]
            if r.get("titleZh"):
                bits.append("中文题=" + str(r.get("titleZh")))
            if r.get("journal"):
                bits.append("期刊=" + str(r.get("journal")))
            if r.get("year"):
                bits.append("年=" + str(r.get("year")))
            if r.get("doi"):
                bits.append("DOI=" + str(r.get("doi")))
            if r.get("authors"):
                bits.append("作者=" + str(r.get("authors")))
            lines.append(str(i) + ". " + "；".join(bits))
        lines.append(
            "填表体例：作者排序(排序/总人数)，题目，期刊名称，年，起止页码，引用数，影响因子。"
            "find 锚定申报书论文表头或「论文」栏原文；replace 按该体例写入上列题录。"
            "作者排序未知可留空或写待核，引用数/影响因子未知必须留空，严禁编造。"
        )
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
        "paperRecords": result.get("paperRecords") or [],
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
