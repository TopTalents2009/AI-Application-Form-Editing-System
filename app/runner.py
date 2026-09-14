"""任务存储与两阶段管线：①生成编辑计划(planned) → ②人工确认后写入文件(done)"""
from __future__ import annotations
import asyncio, json, os, re, secrets, shutil, time
from pathlib import Path
from .config import TASKS_DIR, SCRIPTS_DIR, PYEXE, LLM_TIMEOUT_CLASSIFY, LLM_TIMEOUT_SECTION, PLAN_CONCURRENCY, atomic_replace, resolve_gemini, compare_model_profiles, model_family, COMPARE_FAMS, OPINION_FIELDS, fam_tag
from .llm import chat, extract_json, extract_json_lenient, now_str, created_key, LlmError
from . import matcher as M
from .opinion_extract import ALLOWED_OPINION_EXT, ensure_txt as extract_to_txt
from .pdf_app import (
    ALLOWED_APP_EXT, APP_EXT_HINT, EXCEL_APP_EXT, WORD_APP_EXT,
    backup_name, edited_name, ensure_app_docx, is_backup_output, is_edited_output,
    finalize_scanned_docx, pdf_kind, sniff_pdf, work_docx_name,
)
from .pool import lookup_for_app, format_pool_prompt, save_snapshot
from .attachments import resolve_missing, format_attach_prompt, save_snapshot as save_attach_snapshot, leftover_lines, public_plan_block, public_attach_hit
from .report_docx import write_compare_docx
from .form_reqs import extract_form_requirements, check_text_limits, check_replace_limits, enforce_edit_limits, limit_hint
from .edit_validate import validate_structured_edits, clauses_from_edits
from .form_kind import classify as classify_form
from .hj_form import (
    HJ_LAYOUT_HINTS, HJ_SECTION_ENUM, HJ_SECTION_FILES, HJ_SECTION_ORDER, QM_SECTION_ENUM,
    hj_form_requirements, is_hj_app, remap_hj_section, slice_hj_section,
)
from .inline_opinions import (
    INLINE_OP_NAME, NO_OPINION_MSG, extract_inline_opinion_text, split_inline_units,
)

SECTION_FILES = {"基本信息": "basic-info.md", "教育": "education.md", "工作": "work.md", "论文": "papers.md", "项目": "projects.md"}
SECTION_ORDER = ["基本信息", "教育", "工作", "论文", "项目", "其他"]
TERMINAL = {"done", "failed"}
PYENV = dict(os.environ, PYTHONIOENCODING="utf-8")
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
ROOT_DIR = Path(__file__).resolve().parent.parent

def rid() -> str:
    return format(int(time.time() * 1000), "x") + "-" + secrets.token_hex(3)

def sanitize(name: str) -> str:
    base = str(name or "f").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip()
    return cleaned or "f"

def ext_of(name: str) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""

def norm_find(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def find_in_app_text(app_text: str, find: str) -> bool:
    """落盘前预检：find 是否在申报书正文中（去空白后子串匹配）。"""
    f = norm_find(find)
    if not f or len(f) < 4:
        return False
    body = norm_find(app_text)
    if f in body:
        return True
    if len(f) >= 8:
        probe = f[: max(6, int(len(f) * 0.8))]
        return probe in body
    return False


def annotate_edits_precheck(edits: list, app_text: str) -> int:
    """为每条 edit 标注 findOk，返回未命中条数。"""
    miss = 0
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        ok = find_in_app_text(app_text, str(e.get("find") or ""))
        e["findOk"] = bool(ok)
        if not ok:
            miss += 1
    return miss


def detect_merge_risk_edits(edits: list) -> list[str]:
    """检测 replace 可能合并多条目、抹除其余行的风险。"""
    notes = []
    pat = re.compile(r"(论文\s*\d+|项目\s*\d+|专利\s*\d+|论著\s*\d+)", re.I)
    for i, e in enumerate(edits or [], 1):
        if not isinstance(e, dict):
            continue
        rep = str(e.get("replace") or "")
        find = str(e.get("find") or "")
        r_m = pat.findall(rep)
        f_m = pat.findall(find)
        if len(r_m) >= 2 and len(f_m) < 2:
            notes.append(
                "【多条合并风险】第 " + str(i) + " 条 replace 含 "
                + str(len(r_m)) + " 个条目标记，但改前摘录只锚定一处，可能抹除其余条目"
            )
        clause = str(e.get("clause") or "") + str(e.get("opinion") or "")
        if re.search(r"\d+\s*条.*正序|正序.*\d+\s*条|补充\s*\d+\s*条", clause) and len(r_m) < 2:
            notes.append(
                "【多条合并风险】第 " + str(i) + " 条意见要求多条正序填写，但仅 1 条 edit，建议拆成多条"
            )
    return notes


_QM_SECTION_KEYS = (
    ("论文", ("论文", "论著", "期刊", "影响因子", "一作", "通讯作者")),
    ("项目", ("项目成果", "科研项目", "资助金额")),
    ("教育", ("教育经历", "学历", "学位", "毕业院校")),
    ("工作", ("工作经历", "任职", "履历", "博士后")),
    ("基本信息", ("研究方向", "助理教授", "副教授", "职务", "申报人主要从事", "基本情况", "回国", "来华")),
)


def guess_qm_section(text: str) -> str:
    blob = str(text or "")
    scores = {}
    for sec, keys in _QM_SECTION_KEYS:
        n = sum(1 for k in keys if k in blob)
        if n:
            scores[sec] = n
    if not scores:
        return "其他"
    return max(scores, key=lambda k: scores[k])


def parse_classify_clauses(cj: dict, blocks: list, hj: bool, allowed_sec: set) -> list:
    by_id = {b["id"]: b for b in blocks}
    clauses = []
    used = set()
    for i, c in enumerate((cj or {}).get("clauses") or []):
        if not isinstance(c, dict):
            continue
        sid = norm_sid(c.get("sourceId"))
        blk = by_id.get(sid)
        clause = str(c.get("clause") or "").strip()
        if not clause and blk:
            clause = " ".join(blk["text"].split())[:80]
        if not clause:
            continue
        section = str(c.get("section") or "其他").strip()
        if hj:
            section = remap_hj_section(section, clause, blk["text"] if blk else clause)
        else:
            section = guess_qm_section((blk or {}).get("text") or clause) if section == "其他" else section
        if section not in allowed_sec:
            section = "其他"
        cid = unique_cid(sid or ("C" + str(i + 1)), used)
        clauses.append({
            "cid": cid, "sourceId": sid, "section": section, "clause": clause,
            "opinion": blk["text"] if blk else clause,
            "opName": blk["name"] if blk else "",
        })
    return clauses


def classify_clauses_heuristic(blocks: list, hj: bool, allowed_sec: set) -> list:
    clauses = []
    used = set()
    for b in blocks:
        text = str(b.get("text") or "")
        if hj:
            section = remap_hj_section("其他", text, text)
        else:
            section = guess_qm_section(text)
        if section not in allowed_sec:
            section = "其他"
        sid = norm_sid(b.get("id"))
        cid = unique_cid(sid or ("C" + str(len(clauses) + 1)), used)
        clauses.append({
            "cid": cid, "sourceId": sid, "section": section,
            "clause": " ".join(text.split())[:80],
            "opinion": text, "opName": b.get("name") or "",
        })
    return clauses


def clause_covered(clause: dict, edits: list, leftovers: list) -> bool:
    sid = norm_sid(clause.get("sourceId") or clause.get("cid"))
    cid = norm_sid(clause.get("cid"))
    for e in edits or []:
        eid = norm_sid(e.get("clauseId"))
        if eid and eid in (sid, cid):
            return True
        ck = norm_find(clause.get("clause"))
        if ck and ck == norm_find(e.get("clause")):
            return True
    blob = "\n".join(str(x) for x in (leftovers or []))
    for token in (sid, cid):
        if not token:
            continue
        if re.search(r"针对\s*" + re.escape(token) + r"(?:\b|[：:])", blob, re.I):
            return True
        if ("【意见未覆盖】" + token) in blob:
            return True
    return False


def esc_md(s) -> str:
    return str(s or "").replace("|", "/").replace("\n", " ")

def stem_of(name: str) -> str:
    i = name.rfind(".")
    return name[:i] if i > 0 else name

def app_no_of(name: str) -> str:
    nums = M.extract_book_nums(str(name or ""))
    return str(nums[0]) if nums else ""

def compare_docx_stem(t) -> str:
    no = str((t.get("app") or {}).get("no") or "").strip()
    if not no:
        no = app_no_of((t.get("app") or {}).get("name") or "")
    no = no.split("/")[0].strip()
    no = re.sub(r'[<>:"/\\|?*\s]+', "", no)
    return (no + "-修改对照表") if no else "修改对照表"

ITEM_HEAD = re.compile(
    r"^(?:"
    r"\d{1,3}[\.、．\)]\s*"
    r"|[（(][一二三四五六七八九十\d]+[）)]\s*"
    r"|[①②③④⑤⑥⑦⑧⑨⑩]\s*"
    r")"
)

def norm_sid(s) -> str:
    s = str(s or "").strip().upper().strip("[]() ")
    m = re.fullmatch(r"S(\d+)([A-Z]*)", s) or re.fullmatch(r"(\d+)([A-Z]*)", s)
    if m:
        return "S" + str(int(m.group(1))) + (m.group(2) or "")
    return s

def split_source_units(text: str) -> list:
    text = str(text or "").replace("\xa0", " ").replace("\u3000", " ")
    units = []
    for blk in M.split_opinion_blocks(text):
        units.extend(_split_items(blk))
    return units

def _chunk_has_item(cur) -> bool:
    return any(ITEM_HEAD.match(x.strip()) for x in cur if str(x).strip())


def _split_items(blk: str) -> list:
    lines = str(blk or "").replace("\r\n", "\n").split("\n")
    chunks, cur = [], []

    def push():
        if cur and "".join(cur).strip():
            chunks.append("\n".join(cur).strip())
        cur.clear()

    for line in lines:
        s = line.strip()
        if not s:
            if cur and len("\n".join(cur)) >= 40:
                push()
            continue
        # 已是编号条目时，下一条编号必须切开。短句如「3.承担项目需要证明材料;」
        # 以前因不足 20 字被粘到下一条，导致「论文重新梳理」丢失。
        if ITEM_HEAD.match(s) and cur and (_chunk_has_item(cur) or len("".join(cur).strip()) >= 20):
            push()
        cur.append(line)
    push()
    if not chunks:
        t = str(blk or "").strip()
        return [t] if t else []
    merged = []
    for c in chunks:
        if merged and len(c) < 16 and not ITEM_HEAD.match(c.lstrip()):
            merged[-1] += "\n" + c
        else:
            merged.append(c)
    fixed = []
    i = 0
    while i < len(merged):
        c = merged[i]
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        if nxt is not None and len(c) < 48 and not ITEM_HEAD.match(c.lstrip()) and not re.search(r"[。！？;；]", c):
            merged[i + 1] = c + "\n" + nxt
            i += 1
            continue
        fixed.append(c)
        i += 1
    return fixed

def unique_cid(base: str, used: set) -> str:
    cid = base or "C"
    n = 2
    while cid in used:
        extra = chr(ord("A") + n - 1) if n < 26 else str(n)
        cid = (base or "C") + extra
        n += 1
    used.add(cid)
    return cid

class TaskStore:
    def __init__(self, root=None, concurrency: int = PLAN_CONCURRENCY):
        self.root = Path(root or TASKS_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks = {}
        self.queue = asyncio.Queue()
        self._worker = None
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._loop = None
        self.load_all()

    def tdir(self, tid): return self.root / tid
    def mpath(self, tid): return self.tdir(tid) / "meta.json"

    def load_all(self):
        if not self.root.exists(): return
        for tid in os.listdir(self.root):
            mp = self.mpath(tid)
            if not mp.exists(): continue
            try:
                t = json.loads(mp.read_text(encoding="utf-8")); t["dir"] = str(self.tdir(tid))
                if t.get("status") not in TERMINAL | {"planned"}:
                    t["status"] = "failed"; t["error"] = "服务重启导致中断"
                self.tasks[t["id"]] = t
            except Exception:
                pass

    def persist(self, t):
        clone = {k: v for k, v in t.items() if k != "dir"}
        tmp = str(self.mpath(t["id"])) + ".tmp"
        Path(tmp).write_text(json.dumps(clone, ensure_ascii=False, indent=2), encoding="utf-8")
        atomic_replace(tmp, self.mpath(t["id"]))

    def log(self, t, msg):
        t.setdefault("log", []).append({"t": now_str(), "msg": str(msg)})
        if len(t["log"]) > 600: del t["log"][: len(t["log"]) - 600]
        self.persist(t)

    def list_meta(self):
        arr = sorted(self.tasks.values(), key=lambda x: created_key(x.get("createdAt")), reverse=True)
        out = []
        for t in arr:
            self._ensure_app_mode(t)
            out.append({"id": t["id"], "status": t["status"], "engine": t["engine"], "model": t.get("model"), "createdAt": t["createdAt"], "app": t["app"], "error": t["error"], "hasReport": t.get("hasReport", False), "batchId": t.get("batchId"), "owner": t.get("owner") or "",
                 "deliverables": [{"name": o["name"], "size": o.get("size", 0)} for o in (t.get("deliverables") or [])]})
        return out

    def get(self, tid): return self.tasks.get(tid) or None

    def create(self, body, owner: str = None) -> dict:
        engine = str(body.get("engine") or "api")
        model_id = body.get("model")
        if engine and engine != "api" and not model_id:
            model_id = engine
            engine = "api"
        if engine != "api":
            raise ValueError("未知引擎: " + engine)
        prof = resolve_gemini(model_id)
        apps = body.get("app"); ops = body.get("opinions") or []
        if not isinstance(apps, dict) or ext_of(sanitize(apps.get("name", ""))) not in ALLOWED_APP_EXT:
            raise ValueError("申报书必须为 " + APP_EXT_HINT)
        for o in ops:
            if ext_of(sanitize(o.get("name", ""))) not in ALLOWED_OPINION_EXT:
                raise ValueError("意见类型不支持（Word / Excel / 图片 / txt / md）: " + str(o.get("name")))
        tid = rid(); d = self.tdir(tid)
        (d / "input").mkdir(parents=True, exist_ok=True)
        aname = sanitize(apps["name"])
        raw = __import__("base64").b64decode(apps.get("dataB64") or "")
        if ext_of(aname) == ".pdf":
            sniff_pdf(raw, aname)
        (d / "input" / aname).write_bytes(raw)
        op_names = []
        for o in ops:
            n = sanitize(o["name"])
            (d / "input" / n).write_bytes(__import__("base64").b64decode(o.get("dataB64") or ""))
            op_names.append(n)
        app_meta = {"name": aname, "no": app_no_of(aname), "workDocx": work_docx_name(aname)}
        given_mode = str(apps.get("mode") or "").strip().upper()
        if given_mode in ("QM", "HJ"):
            app_meta["mode"] = given_mode
        t = {"id": tid, "dir": str(d), "engine": engine, "model": prof["id"], "modelLabel": prof["label"],
             "status": "queued", "createdAt": now_str(),
             "app": app_meta,
             "opinions": [{"name": n} for n in op_names],
             "log": [], "outputs": [], "deliverables": [], "hasReport": False, "error": None, "finishedAt": None}
        if owner:
            t["owner"] = str(owner)
        if body.get("batchId"):
            t["batchId"] = str(body.get("batchId"))
        self.tasks[tid] = t; self.persist(t)
        return t

    def bind_loop(self, loop):
        # 直发模型：任务由 enqueue 直接 create_task，无需常驻 worker
        self._loop = loop

    def _spawn_task(self, tid):
        t = self.tasks.get(tid)
        if not t or t.get("status") in TERMINAL | {"planned"}: return
        async def _run():
            async with self._sem:
                await self.run_task(t)
        asyncio.create_task(_run())

    def enqueue(self, tid):
        spawn = lambda: self._spawn_task(tid)
        try:
            asyncio.get_running_loop()
            spawn()
        except RuntimeError:
            loop = getattr(self, "_loop", None)
            if loop is None:
                raise RuntimeError("服务未初始化完成，请稍后重试")
            loop.call_soon_threadsafe(spawn)
    def plan_path(self, t): return Path(t["dir"]) / "work" / "tmp" / "plan.json"

    def load_plan(self, t):
        p = self.plan_path(t)
        if not p.exists(): return None
        return json.loads(p.read_text(encoding="utf-8"))

    def replan(self, tid) -> str:
        t = self.tasks.get(tid)
        if not t: raise ValueError("任务不存在")
        if t["status"] != "planned" and t["status"] != "failed":
            raise ValueError("当前状态 " + t["status"] + " 不能重试（仅失败或待确认可重试）")
        t["status"] = "queued"
        t["error"] = None
        t["finishedAt"] = None
        t["retryCount"] = int(t.get("retryCount") or 0) + 1
        self.log(t, "重试第 " + str(t["retryCount"]) + " 次，重新生成计划")
        self.enqueue(tid)
        return tid

    async def prepare(self, t):
        input_dir = Path(t["dir"]) / "input"
        work = Path(t["dir"]) / "work"
        work_input, txt_dir, out_dir, tmp_dir = work / "input", work / "txt", work / "output", work / "tmp"
        for d2 in (work_input, txt_dir, out_dir, tmp_dir): d2.mkdir(parents=True, exist_ok=True)
        app_name = (t.get("app") or {}).get("name") or ""
        work_docx = work_docx_name(app_name)
        t.setdefault("app", {})["workDocx"] = work_docx
        for f in os.listdir(input_dir):
            shutil.copyfile(input_dir / f, work_input / f)  # 沙盒内副本，原件不动
            ext = ext_of(f)
            stem = f[: -len(ext)] if ext else f
            target = txt_dir / (stem + ".txt")
            try:
                if f == app_name and ext == ".pdf":
                    kind = pdf_kind(work_input / f)
                    t.setdefault("app", {})["pdfKind"] = kind
                    engine = await ensure_app_docx(
                        work_input / f, work_input / work_docx,
                        log_fn=lambda msg: self.log(t, msg),
                    )
                    if kind == "scanned":
                        self.log(t, "扫描 PDF 已 OCR 识别并生成 Word 工作稿 " + work_docx + "（" + str(engine) + "）")
                        self.log(t, "提示：扫描件落盘时将按根目录 QM.docx / HJ.docx 模板重新生成申报书；请重点核对对照表")
                    else:
                        self.log(t, "数字 PDF 已转为 Word 工作稿 " + work_docx + "（" + str(engine) + "）")
                    await extract_to_txt(work_input / work_docx, target)
                else:
                    await extract_to_txt(work_input / f, target)
                self.log(t, "已提取 " + f + " → txt/" + target.name)
            except Exception as e:
                msg = str(e)[:240]
                self.log(t, "提取失败 " + f + ": " + msg)
                if f != app_name:
                    raise ValueError("意见「" + f + "」提取失败：" + msg)
                raise ValueError("申报书「" + f + "」提取失败：" + msg)
        if not t.get("opinions"):
            src = work_input / app_name
            if ext_of(app_name) == ".pdf":
                cand = work_input / work_docx
                if cand.exists():
                    src = cand
            text, n_cmt = extract_inline_opinion_text(src)
            if not text:
                raise ValueError(NO_OPINION_MSG)
            (txt_dir / (stem_of(INLINE_OP_NAME) + ".txt")).write_text(text, encoding="utf-8")
            t["opinions"] = [{"name": INLINE_OP_NAME}]
            t["inlineOpinions"] = True
            self.log(t, "未上传意见文档，已从申报书标注栏提取 " + str(n_cmt) + " 条修改意见")
        self._classify_prepared(t)
        self.persist(t)

    async def _py(self, args, timeout=120):
        proc = await asyncio.create_subprocess_exec(PYEXE, *[str(a) for a in args],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=PYENV)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
            return out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), proc.returncode or 0
        except asyncio.TimeoutError:
            proc.kill()
            return "", "timeout", -1

    def read_prepared_texts(self, t):
        txt_dir = Path(t["dir"]) / "work" / "txt"
        ap = txt_dir / (stem_of(t["app"]["name"]) + ".txt")
        opinion_files = []
        for o in t["opinions"]:
            p = txt_dir / (stem_of(o["name"]) + ".txt")
            if p.exists():
                opinion_files.append({"name": o["name"], "text": p.read_text(encoding="utf-8")})
        if not ap.exists(): raise ValueError("申报书文本缺失（预处理提取失败）")
        if not opinion_files:
            raise ValueError(NO_OPINION_MSG if t.get("inlineOpinions") else "意见文本全部缺失（预处理提取失败）")
        return {"appText": ap.read_text(encoding="utf-8"), "opinionFiles": opinion_files}

    def _set_app_mode(self, t, mode: str, persist: bool = False) -> str:
        mode = str(mode or "").strip().upper()
        if mode not in ("QM", "HJ"):
            return str((t.get("app") or {}).get("mode") or "")
        cur = str((t.get("app") or {}).get("mode") or "")
        if cur == mode:
            return mode
        t.setdefault("app", {})["mode"] = mode
        if persist:
            self.persist(t)
        return mode

    def _classify_text(self, t, text: str, persist: bool = False) -> str:
        name = str((t.get("app") or {}).get("name") or "")
        return self._set_app_mode(t, classify_form(text, name), persist=persist)

    def _classify_prepared(self, t) -> str:
        name = str((t.get("app") or {}).get("name") or "")
        ap = Path(t["dir"]) / "work" / "txt" / (stem_of(name) + ".txt")
        if not ap.exists():
            return str((t.get("app") or {}).get("mode") or "")
        try:
            text = ap.read_text(encoding="utf-8")
        except Exception:
            return str((t.get("app") or {}).get("mode") or "")
        mode = self._classify_text(t, text, persist=False)
        if mode:
            self.log(t, "申报书模板 " + mode)
        return mode

    def _ensure_app_mode(self, t) -> str:
        cur = str((t.get("app") or {}).get("mode") or "").upper()
        if cur in ("QM", "HJ"):
            return cur
        name = str((t.get("app") or {}).get("name") or "")
        for p in (
            Path(t.get("dir") or "") / "work" / "txt" / (stem_of(name) + ".txt"),
            Path(t.get("dir") or "") / "txt" / (stem_of(name) + ".txt"),
        ):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            return self._classify_text(t, text, persist=True)
        return ""

    def collect_opinion_blocks(self, texts) -> list:
        blocks = []
        for of in texts.get("opinionFiles") or []:
            raw = str(of.get("text") or "")
            units = split_inline_units(raw)
            if not units:
                units = split_source_units(raw)
            if not units:
                raw = raw.strip()
                if raw:
                    units = [raw]
            for u in units:
                blocks.append({"id": "S" + str(len(blocks) + 1), "name": of["name"], "text": u})
        return blocks

    def _hj_mode(self, t, app_text: str = "") -> bool:
        app = t.get("app") or {}
        return is_hj_app(str(app.get("mode") or ""), app_text, str(app.get("name") or ""))

    def _section_spec(self, hj: bool):
        if hj:
            return list(HJ_SECTION_ORDER), dict(HJ_SECTION_FILES)
        return list(SECTION_ORDER), dict(SECTION_FILES)

    def build_classify_messages(self, blocks, hj: bool = False):
        tpl = (ROOT_DIR / "CLASSIFY_PROMPT.md").read_text(encoding="utf-8")
        enum = HJ_SECTION_ENUM if hj else QM_SECTION_ENUM
        tpl = tpl.replace("{{SECTION_ENUM}}", enum)
        parts = ["[" + b["id"] + "] 来源文件：" + b["name"] + "\n" + b["text"] for b in blocks]
        return [{"role": "user", "content": tpl.replace("{{OPINIONS}}", "\n\n----\n\n".join(parts))}]

    def load_rules(self, sec, hj: bool = False):
        files = HJ_SECTION_FILES if hj else SECTION_FILES
        fname = files.get(sec)
        fp = RULES_DIR / fname if fname else None
        return fp.read_text(encoding="utf-8") if fp and Path(fp).exists() else ""

    def build_section_plan_messages(self, sec, items, app_text, pool_text="", attach_text="", hj: bool = False):
        tpl = (ROOT_DIR / "SECTION_PLAN_TEMPLATE.md").read_text(encoding="utf-8")
        rules = self.load_rules(sec, hj=hj) or "（本章节暂无专门规则，按申报书通用规范处理）"
        lines = []
        for it in items:
            cid = str(it.get("cid") or "")
            clause = str(it.get("clause") or "").strip()
            opinion = str(it.get("opinion") or clause).strip()
            lines.append("[" + cid + "] " + clause)
            if opinion:
                lines.append("原文：" + opinion)
            hint = limit_hint(sec, clause, opinion, app_text)
            if hint:
                lines.append(hint)
            lines.append("")
        form_reqs = extract_form_requirements(app_text)
        if hj:
            extra = hj_form_requirements(app_text)
            form_reqs = extra + (("\n\n" + form_reqs) if form_reqs else "")
        body = tpl.replace("{{SECTION}}", sec).replace("{{RULES}}", rules)
        body = body.replace("{{FORM_REQS}}", form_reqs)
        body = body.replace("{{CLAUSES}}", "\n".join(lines).strip())
        body = body.replace("{{APP_TEXT}}", slice_hj_section(app_text, sec) if hj else app_text)
        body = body.replace("{{POOL_DATA}}", pool_text or "（未检索到库内记录，仅能使用申报书正文；缺数据写入 leftovers）")
        body = body.replace("{{ATTACH_DATA}}", attach_text or "（修改意见未点名缺失附件）")
        body = body.replace("{{LAYOUT_HINTS}}", HJ_LAYOUT_HINTS if hj else "")
        return [{"role": "user", "content": body}]

    async def verify_docx(self, fp):
        so, se, rc = await self._py([SCRIPTS_DIR / "sb_verify.py", fp], timeout=120)
        return {"ok": rc == 0 and so.startswith("OK"), "info": (so or se).strip()[:160]}

    async def verify_outputs(self, t):
        out_dir = Path(t["dir"]) / "work" / "output"
        outputs = []; ok_count = 0; t["hasReport"] = False
        if out_dir.exists():
            for f in sorted(os.listdir(out_dir)):
                fp = out_dir / f
                if not fp.is_file() or fp.stat().st_size == 0: continue
                intact, detail = True, ""
                if is_edited_output(f):
                    v = await self.verify_docx(fp)
                    intact = v["ok"]; detail = v["info"]
                    if intact:
                        ok_count += 1
                outputs.append({"name": f, "size": fp.stat().st_size, "dir": "output", "docxIntact": intact, "verify": detail})
                if "对照表" in f: t["hasReport"] = True
        t["outputs"] = outputs
        t["deliverables"] = [o for o in outputs if is_edited_output(o["name"]) or is_backup_output(o["name"]) or "对照表" in o["name"] or "遗留事项" in o["name"]]
        return ok_count > 0

    # ---------- ① 生成编辑计划（不写文件） ----------
    async def generate_plan(self, t):
        texts = self.read_prepared_texts(t)
        if not M.is_app_content(texts["appText"]):
            app = t.get("app") or {}
            if ext_of(app.get("name")) == ".pdf":
                extra = "。扫描 PDF 识别或数字 PDF 转换后表格可能丢失，建议尽量改传 Word / Excel" if app.get("pdfKind") == "scanned" else "。数字 PDF 转换后表格可能丢失，建议改传 Word / Excel"
            else:
                extra = ""
            raise ValueError("所选文件不像一份已填写的申报书（正文过短/未填写模板/缺封面关键字段），请检查是否选错文件" + extra)
        kind = self._classify_text(t, texts["appText"], persist=False)
        if kind:
            self.log(t, "申报书对照模板判定为 " + kind)
        hj = self._hj_mode(t, texts["appText"])
        sec_all, _sec_files = self._section_spec(hj)
        if hj:
            self.log(t, "HJ 按印刷栏位切章：" + "、".join(sec_all[:-1]))
        self.log(t, "使用模型 " + str(t.get("modelLabel") or t.get("model") or "默认"))
        self.log(t, "意见按章节分类中…")
        blocks = self.collect_opinion_blocks(texts)
        if not blocks:
            raise ValueError("意见原文切分结果为空")
        self.log(t, "已从意见文件切出 " + str(len(blocks)) + " 条原文")
        allowed_sec = set(sec_all)
        clauses = []
        classify_err = ""
        try:
            c_resp = await chat(self.build_classify_messages(blocks, hj=hj), json_mode=True, timeout_s=LLM_TIMEOUT_CLASSIFY, model=t.get("model"))
            cj = extract_json_lenient(c_resp["content"])
            clauses = parse_classify_clauses(cj, blocks, hj, allowed_sec)
        except Exception as e:
            classify_err = str(e)[:180]
        if not clauses:
            if classify_err:
                self.log(t, "分类 JSON 解析失败，启用关键词兜底分类：" + classify_err[:120])
            else:
                self.log(t, "分类结果为空，启用关键词兜底分类")
            clauses = classify_clauses_heuristic(blocks, hj, allowed_sec)
        elif classify_err:
            self.log(t, "分类 JSON 经修复后解析成功（" + str(len(clauses)) + " 条）")

        # —— 反馈修复（#10 重复输出 / #11 意见漏提）——
        # 1) 去重：同一意见正文或条款摘要完全相同的条目只保留第一条，避免「同一条意见输出两次」
        _seen_op = set()
        _seen_clause = set()
        _dedup = []
        for _c in clauses:
            _op = norm_find(_c.get("opinion") or "")
            _cl = norm_find(_c.get("clause") or "")
            if _op and _op in _seen_op:
                continue
            if _cl and _cl in _seen_clause:
                continue
            if _op:
                _seen_op.add(_op)
            if _cl:
                _seen_clause.add(_cl)
            _dedup.append(_c)
        clauses = _dedup

        if not clauses:
            clauses = classify_clauses_heuristic(blocks, hj, allowed_sec)
        # 2) 兜底覆盖：LLM 漏掉的来源意见补一条，避免「意见未被提取/未修改」
        if clauses:
            _covered = {c.get("sourceId") for c in clauses}
            _used2 = {c.get("cid") for c in clauses}
            for b in blocks:
                if b["id"] in _covered:
                    continue
                cid = unique_cid(b["id"], _used2)
                text = str(b.get("text") or "")
                if hj:
                    section = remap_hj_section("其他", text, text)
                else:
                    section = guess_qm_section(text)
                if section not in allowed_sec:
                    section = "其他"
                clauses.append({
                    "cid": cid, "sourceId": b["id"], "section": section,
                    "clause": " ".join(text.split())[:80],
                    "opinion": text, "opName": b.get("name") or "",
                })
                self.log(t, "补充被漏掉的来源意见：" + b["id"])
        by_sec = {}
        for c in clauses:
            by_sec.setdefault(c["section"], []).append(c)
        self.log(t, "章节分布：" + "，".join(s + "×" + str(len(a)) for s, a in by_sec.items()))

        sec_order = [s for s in sec_all if s in by_sec]
        if not sec_order:
            by_sec["其他"] = list(clauses)
            sec_order = ["其他"]
        app_no = app_no_of(t["app"]["name"])
        t.setdefault("app", {})["no"] = app_no
        self.log(t, "检索人才库/企业库…")
        snap = await lookup_for_app(t["app"]["name"], texts["appText"], mode=str((t.get("app") or {}).get("mode") or ""))
        try:
            save_snapshot(t["dir"], snap)
        except Exception:
            pass
        t["poolHit"] = snap.get("hit") or {}
        self.persist(t)
        self.log(t, "" + (snap.get("summary") or "无库内匹配") + (("（" + "；".join(snap.get("notes") or []) + "）") if snap.get("notes") else ""))
        pool_text = format_pool_prompt(snap)
        opinion_blob = [c.get("opinion") or "" for c in clauses] + [c.get("clause") or "" for c in clauses]
        attach = {"needed": [], "items": [], "private": {}, "notes": [], "summary": ""}
        self.log(t, "检索缺失附件（人才库优先；项目证明再走联网检索/生成接口）…")
        try:
            attach = await resolve_missing(t["id"], opinion_blob, snap, app_no, app_text=texts.get("appText") or "", task_dir=t["dir"])
            save_attach_snapshot(t["dir"], attach)
        except Exception as e:
            attach["notes"] = list(attach.get("notes") or []) + ["缺附件检索失败：" + str(e)[:160]]
            self.log(t, "缺附件检索失败：" + str(e)[:160])
        t["attachHit"] = public_attach_hit(attach)
        self.persist(t)
        if attach.get("needed"):
            self.log(t, "" + (attach.get("summary") or "缺附件检索完成") + (("（" + "；".join(attach.get("notes") or []) + "）") if attach.get("notes") else ""))
        attach_text = format_attach_prompt(attach)
        pair = compare_model_profiles()
        gem = pair.get("gemini") or {}
        if not gem.get("ready"):
            raise ValueError("未配置 Gemini：请在管理后台填写 Gemini 地址和密钥")
        primary_id = str(gem.get("id") or t.get("model") or "")
        primary_fam = "gemini"
        models_for_plan = [("gemini", primary_id, gem.get("label") or "Gemini")]
        n_models = 1
        self.log(t, "开始按章生成计划（Gemini，思考中度，温度 0.1），申报书编号 " + (app_no or "未识别") + "，共 " + str(len(sec_order)) + " 章，章节并发 " + str(PLAN_CONCURRENCY) + "…")
        sec_sem = asyncio.Semaphore(PLAN_CONCURRENCY)

        async def plan_one(sec, model_id, fam, label):
            tag = fam_tag(fam)
            self.log(t, "⏳ 【" + sec + "·" + tag + "】正在调用 " + str(label) + "…")
            try:
                r = await chat(self.build_section_plan_messages(sec, by_sec[sec], texts["appText"], pool_text, attach_text, hj=hj), json_mode=True, timeout_s=LLM_TIMEOUT_SECTION, model=model_id)
                plan = extract_json_lenient(r["content"])
                n_e = len(plan.get("edits") or []) if isinstance(plan, dict) else 0
                n_l = len((plan.get("leftovers") if isinstance(plan, dict) else None) or [])
                self.log(t, "【" + sec + "·" + tag + "】返回 " + str(n_e) + " 条编辑 / " + str(n_l) + " 条遗留")
                return {"sec": sec, "plan": plan, "error": None, "fam": fam, "model": model_id}
            except Exception as e:
                self.log(t, "【" + sec + "·" + tag + "】失败：" + str(e)[:150])
                return {"sec": sec, "plan": None, "error": str(e)[:200], "fam": fam, "model": model_id}

        async def plan_sec(sec):
            n = len(by_sec[sec])
            names = " + ".join(fam_tag(fam) for fam, _, _ in models_for_plan)
            async with sec_sem:
                self.log(t, "【" + sec + "】同时提交 " + names + "（" + str(n) + " 条意见）…")
                rows = await asyncio.gather(*(plan_one(sec, mid, fam, label) for fam, mid, label in models_for_plan))
                return list(rows)

        sec_results = await asyncio.gather(*(plan_sec(s) for s in sec_order))
        settled = [row for group in sec_results for row in group]
        items_by_cid = {c["cid"]: c for c in clauses}

        def resolve_item(e2, sec):
            cid = norm_sid(e2.get("clauseId"))
            src = items_by_cid.get(cid) or items_by_cid.get(str(e2.get("clauseId") or "").strip())
            if not src:
                cl = str(e2.get("clause") or "").strip()
                for it in by_sec.get(sec) or []:
                    if cl and (cl == it["clause"] or cl in it["clause"] or it["clause"] in cl):
                        src = it
                        break
            return src

        def collect_for(fam):
            edits, leftovers, failed = [], [], []
            rows = [s for s in settled if s.get("fam") == fam]
            if not rows:
                return edits, leftovers, failed
            tag = fam_tag(fam)
            for s in rows:
                if s.get("error"):
                    failed.append(s["sec"] + "(" + tag + "): " + s["error"])
                    continue
                plan_edits = s["plan"].get("edits") if isinstance(s.get("plan"), dict) else None
                for e2 in (plan_edits if isinstance(plan_edits, list) else []):
                    if not isinstance(e2, dict) or not str(e2.get("find", "")).strip():
                        continue
                    k = norm_find(e2.get("find"))
                    if any(x["_k"] == k for x in edits):
                        continue
                    if len(str(e2.get("find", ""))) > 2000 or len(str(e2.get("replace", ""))) > 8000:
                        self.log(t, "已丢弃超长编辑（" + tag + "），条款：" + str(e2.get("clause", ""))[:40])
                        continue
                    src = resolve_item(e2, s["sec"])
                    item = dict(e2)
                    item["_k"] = k
                    item["_sec"] = s["sec"]
                    item["section"] = s["sec"]
                    item["appNo"] = app_no
                    item["clause"] = str(item.get("clause") or (src["clause"] if src else "") or "")
                    item["opinion"] = (src["opinion"] if src else "") or item.get("opinion") or item.get("clause") or ""
                    item["opName"] = (src.get("opName") if src else "") or item.get("opName") or ""
                    item["clauseId"] = (src["cid"] if src else "") or str(item.get("clauseId") or "")
                    edits.append(item)
                for lv in ((s["plan"].get("leftovers") if isinstance(s.get("plan"), dict) else None) or []):
                    leftovers.append("【" + tag + "·" + s["sec"] + "】" + str(lv))
            return edits, leftovers, failed

        edits_map, lo_map, fail_map = {}, {}, {}
        for fam in COMPARE_FAMS:
            e, lo, fail = collect_for(fam)
            edits_map[fam], lo_map[fam], fail_map[fam] = e, lo, fail
        failed_secs = [x for fam in COMPARE_FAMS for x in fail_map[fam]]
        if not any(edits_map[f] or lo_map[f] for f in COMPARE_FAMS):
            if failed_secs:
                raise ValueError("全部章节计划调用失败：" + (failed_secs[0] if failed_secs else ""))
            raise ValueError("各章节均未产出有效编辑")
        if failed_secs:
            self.log(t, "部分章节失败：" + "；".join(failed_secs))

        def match_alt(e, pool, used):
            cid = str(e.get("clauseId") or "").strip()
            k = e.get("_k")
            for a in pool:
                if id(a) in used:
                    continue
                if k and a.get("_k") == k:
                    return a
            if cid:
                hits = [a for a in pool if id(a) not in used and str(a.get("clauseId") or "").strip() == cid]
                if len(hits) == 1:
                    return hits[0]
            ck = re.sub(r"\s+", "", str(e.get("clause") or ""))[:80]
            if ck:
                hits = []
                for a in pool:
                    if id(a) in used:
                        continue
                    ak = re.sub(r"\s+", "", str(a.get("clause") or ""))[:80]
                    if ak and ak == ck:
                        hits.append(a)
                if len(hits) == 1:
                    return hits[0]
            return None

        def leftover_bits(rows, fam):
            tag = fam_tag(fam)
            out = []
            for lv in rows:
                s = str(lv or "").strip()
                m = re.match(r"【(Grok|Gemini|火山)·([^】]+)】(.*)", s, re.S)
                if not m or m.group(1) != tag:
                    continue
                rest = (m.group(3) or "").strip()
                cids = []
                cm = re.match(r"\[([^\]]+)\]\s*(.*)", rest, re.S)
                if cm:
                    cids = [x.strip() for x in re.split(r"[/,，、]", cm.group(1)) if x.strip()]
                    rest = (cm.group(2) or "").strip()
                else:
                    cm = re.match(r"(S[\w]+)(?:\s*/\s*S[\w]+)*\s*[：:]\s*(.*)", rest, re.S)
                    if cm:
                        head = rest.split("：", 1)[0].split(":", 1)[0]
                        cids = [x.strip() for x in re.split(r"[/,，、]", head) if x.strip()]
                        rest = (cm.group(2) or "").strip()
                out.append({"sec": m.group(2), "cids": cids, "text": rest or s})
            return out

        def fill_empty_opinions(edits_rows, leftovers_rows):
            by_fam = {fam: leftover_bits(leftovers_rows, fam) for fam in COMPARE_FAMS}
            used_lo = {fam: set() for fam in COMPARE_FAMS}
            for e in edits_rows:
                for fam in COMPARE_FAMS:
                    field = OPINION_FIELDS[fam]
                    if str(e.get(field) or "").strip():
                        continue
                    cid = str(e.get("clauseId") or "").strip()
                    sec = str(e.get("section") or e.get("_sec") or "")
                    bits = by_fam[fam]
                    picked = None
                    if cid:
                        for i, b in enumerate(bits):
                            if i in used_lo[fam]:
                                continue
                            if cid in (b.get("cids") or []):
                                picked = i
                                break
                    if picked is None and sec:
                        cand = [i for i, b in enumerate(bits) if i not in used_lo[fam] and b.get("sec") == sec]
                        if len(cand) == 1:
                            picked = cand[0]
                    if picked is None:
                        continue
                    e[field] = bits[picked]["text"]
                    used_lo[fam].add(picked)

        order = [primary_fam] if primary_fam in COMPARE_FAMS else []
        for f in COMPARE_FAMS:
            if f not in order:
                order.append(f)
        primary_use = next((f for f in order if edits_map[f]), order[0])
        if primary_use != primary_fam and edits_map[primary_use]:
            self.log(t, "主模型本章无有效编辑，改用 " + fam_tag(primary_use) + " 作为主计划")
            primary_fam = primary_use

        used = {fam: set() for fam in COMPARE_FAMS}
        edits = []

        def apply_hits(row, hits, source_fam):
            for fam in COMPARE_FAMS:
                hit = hits.get(fam)
                row[OPINION_FIELDS[fam]] = str((hit or {}).get("replace") or "")
            src_field = OPINION_FIELDS.get(source_fam)
            if src_field:
                row[src_field] = row.get(src_field) or str(row.get("replace") or "")
            row["replace"] = str(row.get("replace") or "")
            return row

        for e in edits_map[primary_use]:
            hits = {}
            for fam in COMPARE_FAMS:
                if fam == primary_use:
                    hits[fam] = e
                    used[fam].add(id(e))
                else:
                    alt = match_alt(e, edits_map[fam], used[fam])
                    hits[fam] = alt
                    if alt:
                        used[fam].add(id(alt))
            edits.append(apply_hits(e, hits, primary_use))
        for fam in COMPARE_FAMS:
            if fam == primary_use:
                continue
            for e in edits_map[fam]:
                if id(e) in used[fam]:
                    continue
                if any(x.get("_k") == e.get("_k") for x in edits):
                    continue
                extra = dict(e)
                extra["replace"] = str(e.get("replace") or "")
                hits = {fam: e}
                used[fam].add(id(e))
                for f2 in COMPARE_FAMS:
                    if f2 == fam:
                        continue
                    alt = match_alt(e, edits_map[f2], used[f2])
                    if alt:
                        hits[f2] = alt
                        used[f2].add(id(alt))
                edits.append(apply_hits(extra, hits, fam))

        leftovers = []
        missing_clauses = [c for c in clauses if not clause_covered(c, edits, leftovers)]
        if missing_clauses:
            miss_ids = "、".join(norm_sid(c.get("sourceId") or c.get("cid")) for c in missing_clauses[:8])
            if len(missing_clauses) > 8:
                miss_ids += "…"
            self.log(t, "计划未覆盖 " + str(len(missing_clauses)) + " 条意见（" + miss_ids + "），补发专项计划…")
            sup_sec = missing_clauses[0].get("section") or "其他"
            if len({c.get("section") or "其他" for c in missing_clauses}) > 1:
                sup_sec = "其他"
            tag = fam_tag(primary_fam)
            try:
                r = await chat(
                    self.build_section_plan_messages(sup_sec, missing_clauses, texts["appText"], pool_text, attach_text, hj=hj),
                    json_mode=True, timeout_s=LLM_TIMEOUT_SECTION, model=primary_id,
                )
                sup_plan = extract_json_lenient(r["content"])
                sup_edits = 0
                for e2 in (sup_plan.get("edits") if isinstance(sup_plan, dict) else None) or []:
                    if not isinstance(e2, dict) or not str(e2.get("find", "")).strip():
                        continue
                    k = norm_find(e2.get("find"))
                    if any(x.get("_k") == k for x in edits):
                        continue
                    src = items_by_cid.get(norm_sid(e2.get("clauseId"))) or next(
                        (c for c in missing_clauses if norm_sid(c.get("cid")) == norm_sid(e2.get("clauseId"))),
                        None,
                    )
                    item = dict(e2)
                    item["_k"] = k
                    item["_sec"] = sup_sec
                    item["section"] = sup_sec
                    item["appNo"] = app_no
                    item["clause"] = str(item.get("clause") or (src["clause"] if src else "") or "")
                    item["opinion"] = (src["opinion"] if src else "") or item.get("opinion") or item.get("clause") or ""
                    item["opName"] = (src.get("opName") if src else "") or item.get("opName") or ""
                    item["clauseId"] = (src["cid"] if src else "") or str(item.get("clauseId") or "")
                    item[OPINION_FIELDS[primary_fam]] = str(item.get("replace") or "")
                    edits.append(item)
                    sup_edits += 1
                for lv in ((sup_plan.get("leftovers") if isinstance(sup_plan, dict) else None) or []):
                    leftovers.append("【" + tag + "·" + sup_sec + "】" + str(lv))
                self.log(t, "专项计划补回 " + str(sup_edits) + " 条编辑 / " + str(len((sup_plan.get("leftovers") if isinstance(sup_plan, dict) else None) or [])) + " 条遗留")
            except Exception as e:
                self.log(t, "专项计划失败：" + str(e)[:150])
            for c in missing_clauses:
                if clause_covered(c, edits, leftovers):
                    continue
                sid = norm_sid(c.get("sourceId") or c.get("cid"))
                sec = str(c.get("section") or "其他")
                excerpt = " ".join(str(c.get("opinion") or "").split())[:220]
                msg = "【意见未覆盖】" + sid + " " + str(c.get("clause") or "")[:50] + "：" + excerpt
                leftovers.append("【" + tag + "·" + sec + "】" + msg)
                self.log(t, "兜底遗留未覆盖意见：" + sid)

        seen_lo = set()
        for fam in [primary_use] + [f for f in COMPARE_FAMS if f != primary_use]:
            for lv in lo_map[fam]:
                key = re.sub(r"\s+", "", str(lv))
                if key in seen_lo:
                    continue
                seen_lo.add(key)
                leftovers.append(lv)
        fill_empty_opinions(edits, leftovers)
        hj_mode = is_hj_app(
            str((t.get("app") or {}).get("mode") or ""),
            texts.get("appText") or "",
            str(t.get("app", {}).get("name") or ""),
        )
        edits, struct_issues = validate_structured_edits(edits, clauses, texts["appText"], hj=hj_mode)
        for msg in struct_issues:
            tag_msg = msg if msg.startswith("【") else "【结构化校验】" + msg
            if tag_msg not in leftovers:
                leftovers.append(tag_msg)
        if struct_issues:
            self.log(t, "结构化校验：" + str(len(struct_issues)) + " 条（个人贡献/正序/行覆盖/限字）")
        n_cut = enforce_edit_limits(edits, texts["appText"])
        for msg in n_cut:
            leftovers.append("【表内限字】" + msg)
        if n_cut:
            self.log(t, "已按申报书印刷上限压缩 " + str(len(n_cut)) + " 条超限修改意见")
        for msg in check_replace_limits(edits, texts["appText"]):
            leftovers.append("【表内限字】" + msg)
        try:
            attach = await resolve_missing(t["id"], opinion_blob + leftovers, snap, app_no, prev=attach, app_text=texts.get("appText") or "", task_dir=t["dir"])
            save_attach_snapshot(t["dir"], attach)
        except Exception as e:
            self.log(t, "缺附件补检索失败：" + str(e)[:160])
        t["attachHit"] = public_attach_hit(attach)
        self.persist(t)
        lo_blob = "\n".join(str(x) for x in leftovers)
        for line in leftover_lines(attach):
            dls = re.findall(r"/api/tasks/\S+/ext-files/\S+", line)
            if dls and all(x in lo_blob for x in dls):
                continue
            if line in leftovers:
                continue
            leftovers.append(line)
            lo_blob += "\n" + line

        if not edits and not leftovers:
            raise ValueError("各章节均未产出有效编辑")
        pre_miss = annotate_edits_precheck(edits, texts["appText"])
        if pre_miss:
            self.log(t, "计划预检：" + str(pre_miss) + "/" + str(len(edits)) + " 条改前摘录未在申报书正文中匹配")
            leftovers.append(
                "【计划预检】" + str(pre_miss) + " 条改前摘录在申报书中找不到，落盘可能失败，请核对「修改前」锚点"
            )
        for msg in detect_merge_risk_edits(edits):
            if msg not in leftovers:
                leftovers.append(msg)
        tmp_dir = Path(t["dir"]) / "work" / "tmp"; tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "plan.json").write_text(json.dumps({
            "appNo": app_no, "appName": t["app"]["name"], "sections": sec_order,
            "clauses": clauses, "edits": edits, "leftovers": leftovers, "precheckMiss": pre_miss,
            "compareModels": {
                "primary": primary_id,
                "primaryFamily": primary_fam,
                "grok": (pair.get("grok") or {}).get("id") or "",
                "gemini": (pair.get("gemini") or {}).get("id") or "",
                "doubao": (pair.get("doubao") or {}).get("id") or "",
            },
            "pool": {"summary": snap.get("summary") or "", "hit": snap.get("hit") or {}, "notes": snap.get("notes") or []},
            "attachments": public_plan_block(attach),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        counts = " / ".join(fam_tag(f) + " " + str(len(edits_map[f])) for f in COMPARE_FAMS)
        self.log(t, "合并对照计划：" + str(len(edits)) + " 条编辑 / " + str(len(leftovers)) + " 条遗留（" + counts + "）")
        return edits, leftovers

    async def run_task(self, t):
        try:
            t["startedAt"] = now_str()
            await self.prepare(t)
            t["status"] = "running"; self.persist(t)
            self.log(t, "大模型直连模式（生成编辑计划，等待人工确认）")
            edits, leftovers = await self.generate_plan(t)
            t["status"] = "planned"; self.persist(t)
            self.log(t, "计划就绪：" + str(len(edits)) + " 条编辑 / " + str(len(leftovers)) + " 条遗留 —— 请在前端核对“修改前/修改后”，确认后才会写入文件")
        except ValueError as e:
            t["status"] = "failed"; t["error"] = str(e); t["finishedAt"] = now_str(); self.persist(t)
        except LlmError as e:
            t["status"] = "failed"; t["error"] = str(e); t["finishedAt"] = now_str(); self.persist(t)
        except Exception as e:
            import traceback; t["status"] = "failed"
            t["error"] = traceback.format_exc()[-900:]
            t["finishedAt"] = now_str(); self.persist(t)

    # ---------- ② 人工确认后写入文件 ----------
    async def apply_confirmed(self, t, edits, leftovers):
        try:
            t["status"] = "running"; t["error"] = None; t.pop("applyWarning", None); self.persist(t)
            self.log(t, "人工确认完成（" + str(len(edits)) + " 条编辑），开始写入文件…")
            tmp_dir = Path(t["dir"]) / "work" / "tmp"; tmp_dir.mkdir(parents=True, exist_ok=True)
            plan_path = tmp_dir / "plan.json"
            plan_clauses = []
            if plan_path.exists():
                try:
                    plan_clauses = json.loads(plan_path.read_text(encoding="utf-8")).get("clauses") or []
                except Exception:
                    plan_clauses = []
            plan_path.write_text(json.dumps({
                "appNo": app_no_of(t["app"]["name"]), "appName": t["app"]["name"],
                "clauses": plan_clauses, "edits": edits, "leftovers": leftovers,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            out_dir = Path(t["dir"]) / "work" / "output"; out_dir.mkdir(parents=True, exist_ok=True)
            stem = stem_of(t["app"]["name"])
            src_app = Path(t["dir"]) / "work" / "input" / work_docx_name((t.get("app") or {}).get("workDocx") or t["app"]["name"])
            if not src_app.exists():
                src_app = Path(t["dir"]) / "work" / "input" / work_docx_name(t["app"]["name"])
            src_ext = src_app.suffix.lower()
            if not src_app.exists() or src_ext not in (WORD_APP_EXT | EXCEL_APP_EXT):
                raise ValueError("没有可用于落盘的申报书工作稿（PDF 需先转换成 .docx）")
            app_text = ""
            try:
                app_text = self.read_prepared_texts(t)["appText"]
            except Exception:
                txt_p = Path(t["dir"]) / "work" / "txt" / (stem + ".txt")
                if txt_p.exists():
                    app_text = txt_p.read_text(encoding="utf-8")
            leftovers = list(leftovers or [])
            hj_mode = is_hj_app(
                str((t.get("app") or {}).get("mode") or ""),
                app_text,
                str(t.get("app", {}).get("name") or ""),
            )
            clause_ctx = plan_clauses or clauses_from_edits(edits)
            edits, struct_issues = validate_structured_edits(edits, clause_ctx, app_text, hj=hj_mode)
            for msg in struct_issues:
                tag_msg = msg if msg.startswith("【") else "【结构化校验】" + msg
                if tag_msg not in leftovers:
                    leftovers.append(tag_msg)
            if struct_issues:
                self.log(t, "落盘前结构化校验：" + str(len(struct_issues)) + " 条")
            n_cut = enforce_edit_limits(edits, app_text)
            for msg in n_cut:
                tag = "【表内限字】" + msg
                if tag not in leftovers:
                    leftovers.append(tag)
            for msg in check_replace_limits(edits, app_text):
                tag = "【表内限字】" + msg
                if tag not in leftovers:
                    leftovers.append(tag)
            actionable = [e for e in (edits or []) if str(e.get("find") or "").strip()]
            pre_miss = []
            for i, e in enumerate(actionable):
                if not find_in_app_text(app_text, str(e.get("find") or "")):
                    pre_miss.append(i)
            if pre_miss:
                self.log(t, "落盘前预检：" + str(len(pre_miss)) + "/" + str(len(actionable)) + " 条 find 未在申报书正文中匹配")
                for idx in pre_miss[:8]:
                    e = actionable[idx]
                    msg = "【预检未命中】第 " + str(idx + 1) + " 条（" + str(e.get("clause") or e.get("clauseId") or "")[:50] + "）：改前摘录在申报书中找不到"
                    if msg not in leftovers:
                        leftovers.append(msg)
            if actionable and len(pre_miss) == len(actionable):
                raise ValueError(
                    "全部 " + str(len(actionable)) + " 条编辑的改前摘录在申报书中均未找到，无法落盘。"
                    "请核对计划是否与原件一致，或改传 Word / Excel 原件（扫描 PDF 命中率较低）"
                )
            out_app = out_dir / edited_name(stem, src_ext)
            bak_app = out_dir / backup_name(stem, src_ext)
            apply_timeout = 600 if src_ext in EXCEL_APP_EXT else 300
            so, se, rc = await self._py([SCRIPTS_DIR / "apply_edits.py", src_app, out_app, bak_app, plan_path], timeout=apply_timeout)
            if rc != 0:
                t["status"] = "failed"; t["error"] = "编辑执行器失败：" + (se or so or "rc!=0")[:400]; return

            if (t.get("app") or {}).get("pdfKind") == "scanned" and src_ext in WORD_APP_EXT:
                kind = str((t.get("app") or {}).get("mode") or "").upper()
                if kind not in ("QM", "HJ"):
                    kind = str(self._classify_text(t, app_text, persist=False) or "HJ").upper()
                from .template_fill import apply_text_edits

                render_txt = tmp_dir / "_render_source.txt"
                render_body = apply_text_edits(app_text, edits)
                if not str(render_body or "").strip():
                    try:
                        await extract_to_txt(out_app, render_txt)
                        render_body = render_txt.read_text(encoding="utf-8") if render_txt.exists() else ""
                    except Exception:
                        await self._py([SCRIPTS_DIR / "sb_extract.py", out_app, render_txt])
                        render_body = render_txt.read_text(encoding="utf-8") if render_txt.exists() else ""
                render_txt.write_text(render_body, encoding="utf-8")
                ro, re_, rc_r = await self._py(
                    [SCRIPTS_DIR / "render_declaration.py", render_txt, kind, out_app],
                    timeout=300,
                )
                rendered = False
                if rc_r == 0:
                    try:
                        info = json.loads(ro or "{}")
                    except Exception:
                        info = {}
                    if info.get("ok"):
                        rendered = True
                        self.log(
                            t,
                            "扫描件已按「" + kind + "」模板（QM.docx/HJ.docx）生成申报书（"
                            + str(info.get("talent") or "") + " / "
                            + str(info.get("enterprise") or "")[:40] + "）",
                        )
                    else:
                        self.log(t, "模板渲染失败：" + str(info.get("error") or ro or "")[:160])
                else:
                    self.log(t, "模板渲染失败：" + str(re_ or ro or "")[:160])
                if not rendered:
                    pdf_src = Path(t["dir"]) / "work" / "input" / str((t.get("app") or {}).get("name") or "")
                    if pdf_src.exists() and pdf_src.suffix.lower() == ".pdf":
                        try:
                            tmp_fmt = out_app.with_suffix(".fmt.docx")
                            finalize_scanned_docx(pdf_src, out_app, tmp_fmt)
                            shutil.move(str(tmp_fmt), str(out_app))
                            self.log(t, "已回退为 PDF 截图版式（每页嵌入原件 + 修改后文字层）")
                        except Exception as e:
                            self.log(t, "版式重排也失败，保留文字稿输出：" + str(e)[:160])
                    else:
                        self.log(t, "未找到 PDF 原件，保留文字稿输出")

            applied = json.loads(so).get("results") or []
            misses = sum(1 for a2 in applied if a2.get("status") == "miss")
            skips = sum(1 for a2 in applied if a2.get("status") == "skip")
            hits = sum(1 for a2 in applied if str(a2.get("status") or "").startswith("hit"))
            tried = len(applied) - skips
            note = ("（" + str(misses) + " 处未命中，转人工）") if misses else ""
            self.log(t, "落盘 " + str(hits) + "/" + str(len(applied)) + " 处" + note)
            if tried >= 2 and misses / tried > 0.5:
                t["applyWarning"] = "过半编辑未命中（" + str(misses) + "/" + str(tried) + "）"
                self.log(t, "警告：过半编辑未命中，请重点核对修改对照表")

            check_txt = tmp_dir / "_final.txt"
            try:
                await extract_to_txt(out_app, check_txt)
            except Exception:
                await self._py([SCRIPTS_DIR / "sb_extract.py", out_app, check_txt])
            final_text = check_txt.read_text(encoding="utf-8") if check_txt.exists() else ""
            limit_hits = check_text_limits(final_text)
            for msg in limit_hits:
                leftovers.append("【表内限字】" + msg)
            if limit_hits:
                self.log(t, "表内限字未达标 " + str(len(limit_hits)) + " 处，已写入遗留事项")
            nrm = lambda x: re.sub(r"\s+", "", str(x or ""))

            rows = []
            row_dicts = []
            for i2, a2 in enumerate(applied):
                e2 = edits[i2] if i2 < len(edits) else {}
                st = " 已改"
                if a2.get("status") == "miss": st = " 未命中·需人工定位"
                elif a2.get("status") == "skip": st = " 空锚点·已跳过"
                else:
                    rep_n = nrm(e2.get("replace"))[:50]
                    if rep_n and rep_n not in nrm(final_text): st = " 已改·终检未检出"
                sec = str(e2.get("_sec", e2.get("section", "-")))
                clause = str(e2.get("clause") or "")
                find = str(e2.get("find") or "")
                opinion = str(e2.get("opinion") or e2.get("clause") or "")
                om = str(e2.get("opinionGemini") or e2.get("opinion") or "")
                replace = str(e2.get("replace") or "")
                row_dicts.append({
                    "n": i2 + 1, "section": sec, "clause": clause, "find": find,
                    "opinion": opinion, "opinionGemini": om,
                    "replace": replace, "status": st,
                })
                rows.append("| " + str(i2 + 1) + " | " + sec + " | " + esc_md(clause)[:70] + " | " + esc_md(find)[:40] + "… | " + esc_md(om)[:70] + " | " + esc_md(replace)[:40] + "… | " + st + " |")
            report_lines = ["# 修改对照表", "", "> 管线：Gemini 出计划 → 人工修订 → 内置执行器落盘　生成时间：" + now_str(), "", "| # | 章节 | 意见条款 | 改前摘录 | Gemini修改意见 | 改后摘录 | 结果 |", "|---|---|---|---|---|---|---|"] + rows
            (out_dir / "修改对照表.md").write_text("\n".join(report_lines), encoding="utf-8")
            try:
                for old in out_dir.glob("*修改对照表.docx"):
                    try: old.unlink()
                    except Exception: pass
                docx_stem = compare_docx_stem(t)
                write_compare_docx(
                    out_dir / (docx_stem + ".docx"),
                    app_name=t["app"]["name"],
                    app_no=app_no_of(t["app"]["name"]) or str((t.get("app") or {}).get("no") or ""),
                    created=now_str(),
                    rows=row_dicts,
                    leftovers=leftovers,
                )
                self.log(t, "已生成修改对照表（Markdown + Word " + docx_stem + ".docx）")
            except Exception as e:
                self.log(t, "对照表 Word 生成失败，已保留 Markdown：" + str(e)[:120])
            lo_txt = "\n".join(str(i2 + 1) + ". " + s for i2, s in enumerate(leftovers)) if leftovers else "（无）"
            (out_dir / "遗留事项.md").write_text("# 遗留事项（需人工补充真实数据）\n\n" + lo_txt, encoding="utf-8")

            if tried > 0 and hits == 0:
                await self.verify_outputs(t)
                t["status"] = "failed"
                t["error"] = (
                    "全部 " + str(tried) + " 条编辑均未写入申报书（find 未命中）。"
                    "已生成修改对照表供核对，请修正改前摘录后重试"
                )
                return
            if not await self.verify_outputs(t):
                t["status"] = "failed"; t["error"] = "成品校验未通过（详见产出校验信息）"; return
            t["status"] = "done"
            self.log(t, "完成：编辑 " + str(hits) + "/" + str(len(applied)) + "，遗留 " + str(len(leftovers)) + " 条，产出 " + str(len(t["outputs"])) + " 个文件")
        except ValueError as e:
            t["status"] = "failed"; t["error"] = str(e)
        except LlmError as e:
            t["status"] = "failed"; t["error"] = str(e)
        except Exception as e:
            import traceback; t["status"] = "failed"
            t["error"] = traceback.format_exc()[-900:]
        finally:
            t["finishedAt"] = now_str(); self.persist(t)
