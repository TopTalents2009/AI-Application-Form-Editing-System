"""任务存储与两阶段管线：①生成编辑计划(planned) → ②人工确认后写入文件(done)"""
from __future__ import annotations
import asyncio, json, os, re, secrets, shutil, time
from pathlib import Path
from .config import TASKS_DIR, SCRIPTS_DIR, PYEXE, LLM_TIMEOUT_CLASSIFY, LLM_TIMEOUT_SECTION, PLAN_CONCURRENCY, atomic_replace, resolve_llm, compare_model_profiles, model_family, COMPARE_FAMS, OPINION_FIELDS, fam_tag
from .llm import chat, extract_json, now_str, created_key
from . import matcher as M
from .opinion_extract import ALLOWED_OPINION_EXT, ensure_txt as extract_to_txt
from .pdf_app import ALLOWED_APP_EXT, ensure_app_docx, sniff_pdf, work_docx_name
from .pool import lookup_for_app, format_pool_prompt, save_snapshot
from .attachments import resolve_missing, format_attach_prompt, save_snapshot as save_attach_snapshot, leftover_lines, public_plan_block
from .report_docx import write_compare_docx
from .form_reqs import extract_form_requirements, check_text_limits, check_replace_limits

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
        if ITEM_HEAD.match(s) and cur and len("".join(cur).strip()) >= 20:
            push()
        cur.append(line)
    push()
    if not chunks:
        t = str(blk or "").strip()
        return [t] if t else []
    merged = []
    for c in chunks:
        if merged and len(c) < 16:
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
        return [{"id": t["id"], "status": t["status"], "engine": t["engine"], "model": t.get("model"), "createdAt": t["createdAt"], "app": t["app"], "error": t["error"], "hasReport": t.get("hasReport", False), "batchId": t.get("batchId"), "owner": t.get("owner") or "",
                 "deliverables": [{"name": o["name"], "size": o.get("size", 0)} for o in (t.get("deliverables") or [])]} for t in arr]

    def get(self, tid): return self.tasks.get(tid) or None

    def create(self, body, owner: str = None) -> dict:
        engine = str(body.get("engine") or "api")
        model_id = body.get("model")
        if engine and engine != "api" and not model_id:
            model_id = engine
            engine = "api"
        if engine != "api":
            raise ValueError("未知引擎: " + engine)
        prof = resolve_llm(model_id)
        apps = body.get("app"); ops = body.get("opinions") or []
        if not isinstance(apps, dict) or ext_of(sanitize(apps.get("name", ""))) not in ALLOWED_APP_EXT:
            raise ValueError("申报书必须为 .docx 或数字版 .pdf")
        if not ops: raise ValueError("至少上传一份意见文档")
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
        t = {"id": tid, "dir": str(d), "engine": engine, "model": prof["id"], "modelLabel": prof["label"],
             "status": "queued", "createdAt": now_str(),
             "app": {"name": aname, "no": app_no_of(aname), "workDocx": work_docx_name(aname)},
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
            raise ValueError("当前状态 " + t["status"] + " 不能重新生成计划")
        t["status"] = "queued"; t["error"] = None; self.persist(t)
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
                    engine = await ensure_app_docx(work_input / f, work_input / work_docx)
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
        if not opinion_files: raise ValueError("意见文本全部缺失（预处理提取失败）")
        return {"appText": ap.read_text(encoding="utf-8"), "opinionFiles": opinion_files}

    def collect_opinion_blocks(self, texts) -> list:
        blocks = []
        for of in texts.get("opinionFiles") or []:
            units = split_source_units(of.get("text") or "")
            if not units:
                raw = str(of.get("text") or "").strip()
                if raw:
                    units = [raw]
            for u in units:
                blocks.append({"id": "S" + str(len(blocks) + 1), "name": of["name"], "text": u})
        return blocks

    def build_classify_messages(self, blocks):
        tpl = (ROOT_DIR / "CLASSIFY_PROMPT.md").read_text(encoding="utf-8")
        parts = ["[" + b["id"] + "] 来源文件：" + b["name"] + "\n" + b["text"] for b in blocks]
        return [{"role": "user", "content": tpl.replace("{{OPINIONS}}", "\n\n----\n\n".join(parts))}]

    def load_rules(self, sec):
        fname = SECTION_FILES.get(sec)
        fp = RULES_DIR / fname if fname else None
        return fp.read_text(encoding="utf-8") if fp and Path(fp).exists() else ""

    def build_section_plan_messages(self, sec, items, app_text, pool_text="", attach_text=""):
        tpl = (ROOT_DIR / "SECTION_PLAN_TEMPLATE.md").read_text(encoding="utf-8")
        rules = self.load_rules(sec) or "（本章节暂无专门规则，按申报书通用规范处理）"
        lines = []
        for it in items:
            cid = str(it.get("cid") or "")
            clause = str(it.get("clause") or "").strip()
            opinion = str(it.get("opinion") or clause).strip()
            lines.append("[" + cid + "] " + clause)
            if opinion:
                lines.append("原文：" + opinion)
            lines.append("")
        body = tpl.replace("{{SECTION}}", sec).replace("{{RULES}}", rules)
        body = body.replace("{{FORM_REQS}}", extract_form_requirements(app_text))
        body = body.replace("{{CLAUSES}}", "\n".join(lines).strip())
        body = body.replace("{{APP_TEXT}}", app_text)
        body = body.replace("{{POOL_DATA}}", pool_text or "（未检索到库内记录，仅能使用申报书正文；缺数据写入 leftovers）")
        body = body.replace("{{ATTACH_DATA}}", attach_text or "（修改意见未点名缺失附件）")
        return [{"role": "user", "content": body}]

    async def verify_docx(self, fp):
        so, se, rc = await self._py([SCRIPTS_DIR / "sb_verify.py", fp], timeout=120)
        return {"ok": rc == 0 and so.startswith("OK"), "info": (so or se).strip()[:160]}

    async def verify_outputs(self, t):
        out_dir = Path(t["dir"]) / "work" / "output"
        outputs = []; docx_count = 0; t["hasReport"] = False
        if out_dir.exists():
            for f in sorted(os.listdir(out_dir)):
                fp = out_dir / f
                if not fp.is_file() or fp.stat().st_size == 0: continue
                is_docx = ext_of(f) == ".docx"
                intact, detail = True, ""
                if is_docx and f.endswith("_修改后.docx"):
                    v = await self.verify_docx(fp)
                    intact = v["ok"]; detail = v["info"]
                    if intact:
                        docx_count += 1
                outputs.append({"name": f, "size": fp.stat().st_size, "dir": "output", "docxIntact": (not is_docx) or intact, "verify": detail})
                if "对照表" in f: t["hasReport"] = True
        t["outputs"] = outputs
        t["deliverables"] = [o for o in outputs if o["name"].endswith("_修改后.docx") or o["name"].endswith("_备份.docx") or "对照表" in o["name"] or "遗留事项" in o["name"]]
        return docx_count > 0

    # ---------- ① 生成编辑计划（不写文件） ----------
    async def generate_plan(self, t):
        texts = self.read_prepared_texts(t)
        if not M.is_app_content(texts["appText"]):
            extra = "。数字 PDF 转换后表格可能丢失，建议改传 Word（.docx）" if ext_of((t.get("app") or {}).get("name")) == ".pdf" else ""
            raise ValueError("所选文件不像一份已填写的申报书（正文过短/未填写模板/缺封面关键字段），请检查是否选错文件" + extra)
        self.log(t, "使用模型 " + str(t.get("modelLabel") or t.get("model") or "默认"))
        self.log(t, "意见按章节分类中…")
        blocks = self.collect_opinion_blocks(texts)
        if not blocks:
            raise ValueError("意见原文切分结果为空")
        self.log(t, "已从意见文件切出 " + str(len(blocks)) + " 条原文")
        by_id = {b["id"]: b for b in blocks}
        allowed_sec = set(SECTION_ORDER)
        clauses = []
        try:
            c_resp = await chat(self.build_classify_messages(blocks), json_mode=True, timeout_s=LLM_TIMEOUT_CLASSIFY, model=t.get("model"))
            cj = extract_json(c_resp["content"])
            used = set()
            for i, c in enumerate(cj.get("clauses") or []):
                if not isinstance(c, dict): continue
                sid = norm_sid(c.get("sourceId"))
                blk = by_id.get(sid)
                clause = str(c.get("clause") or "").strip()
                if not clause and blk:
                    clause = " ".join(blk["text"].split())[:80]
                if not clause:
                    continue
                section = str(c.get("section") or "其他").strip()
                if section not in allowed_sec:
                    section = "其他"
                cid = unique_cid(sid or ("C" + str(i + 1)), used)
                clauses.append({
                    "cid": cid, "sourceId": sid, "section": section, "clause": clause,
                    "opinion": blk["text"] if blk else clause,
                    "opName": blk["name"] if blk else "",
                })
        except Exception as e:
            self.log(t, "分类失败，整体按【其他】处理：" + str(e)[:150])
        if not clauses:
            used = set()
            for b in blocks:
                cid = unique_cid(b["id"], used)
                clauses.append({
                    "cid": cid, "sourceId": b["id"], "section": "其他",
                    "clause": " ".join(b["text"].split())[:80],
                    "opinion": b["text"], "opName": b["name"],
                })
        by_sec = {}
        for c in clauses:
            by_sec.setdefault(c["section"], []).append(c)
        self.log(t, "章节分布：" + "，".join(s + "×" + str(len(a)) for s, a in by_sec.items()))

        sec_order = [s for s in SECTION_ORDER if s in by_sec]
        if not sec_order:
            by_sec["其他"] = list(clauses)
            sec_order = ["其他"]
        app_no = app_no_of(t["app"]["name"])
        t.setdefault("app", {})["no"] = app_no
        self.log(t, "检索人才库/企业库…")
        snap = await lookup_for_app(t["app"]["name"], texts["appText"])
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
        self.log(t, "检索缺失附件（人才库优先，论文走论文系统）…")
        try:
            attach = await resolve_missing(t["id"], opinion_blob, snap, app_no)
            save_attach_snapshot(t["dir"], attach)
        except Exception as e:
            attach["notes"] = list(attach.get("notes") or []) + ["缺附件检索失败：" + str(e)[:160]]
            self.log(t, "缺附件检索失败：" + str(e)[:160])
        t["attachHit"] = {"summary": attach.get("summary") or "", "needed": attach.get("needed") or [], "found": len(attach.get("items") or [])}
        self.persist(t)
        if attach.get("needed"):
            self.log(t, "" + (attach.get("summary") or "缺附件检索完成") + (("（" + "；".join(attach.get("notes") or []) + "）") if attach.get("notes") else ""))
        attach_text = format_attach_prompt(attach)
        pair = compare_model_profiles()
        primary_id = str(t.get("model") or "")
        primary_fam = model_family(primary_id)
        models_for_plan = []
        skip_note = []
        for fam in COMPARE_FAMS:
            prof = pair.get(fam)
            if prof and prof.get("ready"):
                models_for_plan.append((fam, prof["id"], prof.get("label") or fam_tag(fam)))
            else:
                skip_note.append(fam_tag(fam) + " 未配置")
                if fam == primary_fam:
                    models_for_plan.append((fam, primary_id, str(t.get("modelLabel") or primary_id)))
        if not models_for_plan:
            models_for_plan.append((primary_fam, primary_id, str(t.get("modelLabel") or primary_id)))
        if skip_note:
            self.log(t, "对照模型：" + "；".join(skip_note))
        n_models = len(models_for_plan)
        names_all = "、".join(fam_tag(fam) for fam, _, _ in models_for_plan)
        self.log(t, "开始按章多模型对照（每章同时向 " + names_all + " 提交，主模型 " + str(t.get("modelLabel") or primary_id) + "，申报书编号 " + (app_no or "未识别") + "，共 " + str(len(sec_order)) + " 章 × " + str(n_models) + " 模型，章节并发 " + str(PLAN_CONCURRENCY) + "）…")
        sec_sem = asyncio.Semaphore(PLAN_CONCURRENCY)

        async def plan_one(sec, model_id, fam, label):
            tag = fam_tag(fam)
            self.log(t, "⏳ 【" + sec + "·" + tag + "】正在调用 " + str(label) + "…")
            try:
                r = await chat(self.build_section_plan_messages(sec, by_sec[sec], texts["appText"], pool_text, attach_text), json_mode=True, timeout_s=LLM_TIMEOUT_SECTION, model=model_id)
                plan = extract_json(r["content"])
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
        seen_lo = set()
        for fam in [primary_use] + [f for f in COMPARE_FAMS if f != primary_use]:
            for lv in lo_map[fam]:
                key = re.sub(r"\s+", "", str(lv))
                if key in seen_lo:
                    continue
                seen_lo.add(key)
                leftovers.append(lv)
        fill_empty_opinions(edits, leftovers)
        for msg in check_replace_limits(edits, texts["appText"]):
            leftovers.append("【表内限字】" + msg)
        try:
            attach = await resolve_missing(t["id"], opinion_blob + leftovers, snap, app_no, prev=attach)
            save_attach_snapshot(t["dir"], attach)
        except Exception as e:
            self.log(t, "缺附件补检索失败：" + str(e)[:160])
        t["attachHit"] = {"summary": attach.get("summary") or "", "needed": attach.get("needed") or [], "found": len(attach.get("items") or [])}
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
        tmp_dir = Path(t["dir"]) / "work" / "tmp"; tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "plan.json").write_text(json.dumps({
            "appNo": app_no, "appName": t["app"]["name"], "sections": sec_order,
            "edits": edits, "leftovers": leftovers,
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
        except Exception as e:
            import traceback; t["status"] = "failed"
            t["error"] = traceback.format_exc()[-900:]
            t["finishedAt"] = now_str(); self.persist(t)

    # ---------- ② 人工确认后写入文件 ----------
    async def apply_confirmed(self, t, edits, leftovers):
        try:
            t["status"] = "running"; t["error"] = None; self.persist(t)
            self.log(t, "人工确认完成（" + str(len(edits)) + " 条编辑），开始写入文件…")
            tmp_dir = Path(t["dir"]) / "work" / "tmp"; tmp_dir.mkdir(parents=True, exist_ok=True)
            plan_path = tmp_dir / "plan.json"
            plan_path.write_text(json.dumps({
                "appNo": app_no_of(t["app"]["name"]), "appName": t["app"]["name"],
                "edits": edits, "leftovers": leftovers,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            out_dir = Path(t["dir"]) / "work" / "output"; out_dir.mkdir(parents=True, exist_ok=True)
            stem = stem_of(t["app"]["name"])
            src_docx = Path(t["dir"]) / "work" / "input" / work_docx_name((t.get("app") or {}).get("workDocx") or t["app"]["name"])
            if src_docx.suffix.lower() != ".docx" or not src_docx.exists():
                src_docx = Path(t["dir"]) / "work" / "input" / work_docx_name(t["app"]["name"])
            if not src_docx.exists():
                raise ValueError("没有可用于落盘的 Word 工作稿（数字 PDF 需先转换成 .docx）")
            out_docx = out_dir / (stem + "_修改后.docx"); bak_docx = out_dir / (stem + "_备份.docx")
            so, se, rc = await self._py([SCRIPTS_DIR / "apply_edits.py", src_docx, out_docx, bak_docx, plan_path], timeout=300)
            if rc != 0:
                t["status"] = "failed"; t["error"] = "编辑执行器失败：" + (se or so or "rc!=0")[:400]; return

            applied = json.loads(so).get("results") or []
            misses = sum(1 for a2 in applied if a2.get("status") == "miss")
            hits = sum(1 for a2 in applied if str(a2.get("status") or "").startswith("hit"))
            note = ("（" + str(misses) + " 处未命中，转人工）") if misses else ""
            self.log(t, "落盘 " + str(hits) + "/" + str(len(applied)) + " 处" + note)

            check_txt = tmp_dir / "_final.txt"
            await self._py([SCRIPTS_DIR / "sb_extract.py", out_docx, check_txt])
            final_text = check_txt.read_text(encoding="utf-8") if check_txt.exists() else ""
            leftovers = list(leftovers or [])
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
                og = str(e2.get("opinionGrok") or "")
                om = str(e2.get("opinionGemini") or "")
                od = str(e2.get("opinionDoubao") or "")
                replace = str(e2.get("replace") or "")
                row_dicts.append({
                    "n": i2 + 1, "section": sec, "clause": clause, "find": find,
                    "opinion": opinion, "opinionGrok": og, "opinionGemini": om, "opinionDoubao": od,
                    "replace": replace, "status": st,
                })
                rows.append("| " + str(i2 + 1) + " | " + sec + " | " + esc_md(clause)[:70] + " | " + esc_md(find)[:40] + "… | " + esc_md(og)[:70] + " | " + esc_md(om)[:70] + " | " + esc_md(od)[:70] + " | " + esc_md(replace)[:40] + "… | " + st + " |")
            report_lines = ["# 修改对照表", "", "> 管线：Grok / Gemini / 火山 多模型出计划 → 人工修订 → 内置执行器落盘　生成时间：" + now_str(), "", "| # | 章节 | 意见条款 | 改前摘录 | Grok修改意见 | Gemini修改意见 | 火山修改意见 | 改后摘录 | 结果 |", "|---|---|---|---|---|---|---|---|---|"] + rows
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

            if not await self.verify_outputs(t):
                t["status"] = "failed"; t["error"] = "成品校验未通过（详见产出校验信息）"; return
            t["status"] = "done"
            self.log(t, "完成：编辑 " + str(hits) + "/" + str(len(applied)) + "，遗留 " + str(len(leftovers)) + " 条，产出 " + str(len(t["outputs"])) + " 个文件")
        except Exception as e:
            import traceback; t["status"] = "failed"
            t["error"] = traceback.format_exc()[-900:]
        finally:
            t["finishedAt"] = now_str(); self.persist(t)
