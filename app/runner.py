"""任务存储与两阶段管线：①生成编辑计划(planned) → ②人工确认后写入文件(done)"""
from __future__ import annotations
import asyncio, json, os, re, secrets, shutil, time
from pathlib import Path
from .config import TASKS_DIR, SCRIPTS_DIR, PYEXE
from .llm import chat, extract_json, now_str, created_key
from . import matcher as M
from .pool import lookup_for_app, format_pool_prompt, save_snapshot

SECTION_FILES = {"基本信息": "basic-info.md", "教育": "education.md", "工作": "work.md", "论文": "papers.md", "项目": "projects.md"}
SECTION_ORDER = ["基本信息", "教育", "工作", "论文", "项目", "其他"]
TERMINAL = {"done", "failed"}
ALLOWED_OPINION_EXT = {".docx", ".wps", ".txt", ".md"}
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
    nums = M.extract_book_profile(str(name or ""), "").get("nums") or []
    return "/".join(nums)

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
    def __init__(self, root=None, concurrency: int = 2):
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
        os.replace(tmp, self.mpath(t["id"]))

    def log(self, t, msg):
        t.setdefault("log", []).append({"t": now_str(), "msg": str(msg)})
        if len(t["log"]) > 600: del t["log"][: len(t["log"]) - 600]
        self.persist(t)

    def list_meta(self):
        arr = sorted(self.tasks.values(), key=lambda x: created_key(x.get("createdAt")), reverse=True)
        return [{"id": t["id"], "status": t["status"], "engine": t["engine"], "createdAt": t["createdAt"], "app": t["app"], "error": t["error"], "hasReport": t.get("hasReport", False), "batchId": t.get("batchId")} for t in arr]

    def get(self, tid): return self.tasks.get(tid) or None

    def create(self, body) -> dict:
        engine = str(body.get("engine") or "api")
        if engine != "api": raise ValueError("未知引擎: " + engine)
        apps = body.get("app"); ops = body.get("opinions") or []
        if not isinstance(apps, dict) or ext_of(sanitize(apps.get("name", ""))) != ".docx":
            raise ValueError("申报书必须为 .docx")
        if not ops: raise ValueError("至少上传一份意见文档")
        for o in ops:
            if ext_of(sanitize(o.get("name", ""))) not in ALLOWED_OPINION_EXT:
                raise ValueError("意见类型不支持: " + str(o.get("name")))
        tid = rid(); d = self.tdir(tid)
        (d / "input").mkdir(parents=True, exist_ok=True)
        aname = sanitize(apps["name"])
        (d / "input" / aname).write_bytes(__import__("base64").b64decode(apps.get("dataB64") or ""))
        op_names = []
        for o in ops:
            n = sanitize(o["name"])
            (d / "input" / n).write_bytes(__import__("base64").b64decode(o.get("dataB64") or ""))
            op_names.append(n)
        t = {"id": tid, "dir": str(d), "engine": engine, "status": "queued", "createdAt": now_str(),
             "app": {"name": aname, "no": app_no_of(aname)}, "opinions": [{"name": n} for n in op_names],
             "log": [], "outputs": [], "deliverables": [], "hasReport": False, "error": None, "finishedAt": None}
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
        for f in os.listdir(input_dir):
            shutil.copyfile(input_dir / f, work_input / f)  # 沙盒内副本，原件不动
            ext = ext_of(f)
            stem = f[: -len(ext)] if ext else f
            target = txt_dir / (stem + ".txt")
            if ext in (".txt", ".md"):
                shutil.copyfile(input_dir / f, target); continue
            so, se, rc = await self._py([SCRIPTS_DIR / "sb_extract.py", input_dir / f, target])
            if rc != 0: self.log(t, "⚠️ 提取失败 " + f + ": " + (se or so)[:200])
            else: self.log(t, "📄 已提取 " + f + " → txt/" + target.name)

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

    def build_section_plan_messages(self, sec, items, app_text, pool_text=""):
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
        body = body.replace("{{CLAUSES}}", "\n".join(lines).strip())
        body = body.replace("{{APP_TEXT}}", app_text)
        body = body.replace("{{POOL_DATA}}", pool_text or "（未检索到库内记录，仅能使用申报书正文；缺数据写入 leftovers）")
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
                if is_docx:
                    v = await self.verify_docx(fp)
                    intact = v["ok"]; detail = v["info"]
                    if intact and f.endswith("_修改后.docx"):
                        docx_count += 1
                outputs.append({"name": f, "size": fp.stat().st_size, "dir": "output", "docxIntact": (not is_docx) or intact, "verify": detail})
                if "对照表" in f: t["hasReport"] = True
        t["outputs"] = outputs
        t["deliverables"] = [o for o in outputs if o["name"].endswith("_修改后.docx") or o["name"].endswith("_备份.docx") or o["name"] == "修改对照表.md" or "遗留事项" in o["name"]]
        return docx_count > 0

    # ---------- ① 生成编辑计划（不写文件） ----------
    async def generate_plan(self, t):
        texts = self.read_prepared_texts(t)
        if not M.is_app_content(texts["appText"]):
            raise ValueError("所选文件不像一份已填写的申报书（正文过短/未填写模板/缺封面关键字段），请检查是否选错文件")
        self.log(t, "🧭 意见按章节分类中…")
        blocks = self.collect_opinion_blocks(texts)
        if not blocks:
            raise ValueError("意见原文切分结果为空")
        self.log(t, "📌 已从意见文件切出 " + str(len(blocks)) + " 条原文")
        by_id = {b["id"]: b for b in blocks}
        allowed_sec = set(SECTION_ORDER)
        clauses = []
        try:
            c_resp = await chat(self.build_classify_messages(blocks), json_mode=True, timeout_s=180)
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
            self.log(t, "⚠️ 分类失败，整体按【其他】处理：" + str(e)[:150])
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
        self.log(t, "🗂️ 章节分布：" + "，".join(s + "×" + str(len(a)) for s, a in by_sec.items()))

        sec_order = [s for s in SECTION_ORDER if s in by_sec]
        if not sec_order:
            by_sec["其他"] = list(clauses)
            sec_order = ["其他"]
        app_no = app_no_of(t["app"]["name"])
        t.setdefault("app", {})["no"] = app_no
        self.log(t, "🔎 检索人才库/企业库…")
        snap = await lookup_for_app(t["app"]["name"], texts["appText"])
        try:
            save_snapshot(t["dir"], snap)
        except Exception:
            pass
        t["poolHit"] = snap.get("hit") or {}
        self.persist(t)
        self.log(t, "📚 " + (snap.get("summary") or "无库内匹配") + (("（" + "；".join(snap.get("notes") or []) + "）") if snap.get("notes") else ""))
        pool_text = format_pool_prompt(snap)
        self.log(t, "📝 开始按章生成计划（申报书编号 " + (app_no or "未识别") + "，共 " + str(len(sec_order)) + " 章，最多 2 路过模型）…")
        plan_sem = asyncio.Semaphore(2)
        async def plan_one(sec):
            n = len(by_sec[sec])
            self.log(t, "📝 【" + sec + "】排队出计划（" + str(n) + " 条意见）…")
            async with plan_sem:
                self.log(t, "⏳ 【" + sec + "】正在调用大模型…")
                try:
                    r = await chat(self.build_section_plan_messages(sec, by_sec[sec], texts["appText"], pool_text), json_mode=True, timeout_s=360)
                    plan = extract_json(r["content"])
                    n_e = len(plan.get("edits") or []) if isinstance(plan, dict) else 0
                    n_l = len(plan.get("leftovers") or []) if isinstance(plan, dict) else 0
                    self.log(t, "✅ 【" + sec + "】返回 " + str(n_e) + " 条编辑 / " + str(n_l) + " 条遗留")
                    return {"sec": sec, "plan": plan, "error": None}
                except Exception as e:
                    self.log(t, "⚠️ 【" + sec + "】失败：" + str(e)[:150])
                    return {"sec": sec, "plan": None, "error": str(e)[:200]}
        settled = await asyncio.gather(*(plan_one(s) for s in sec_order))

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

        edits, leftovers, failed_secs = [], [], []
        for s in settled:
            if s["error"]: failed_secs.append(s["sec"] + ": " + s["error"]); continue
            plan_edits = s["plan"].get("edits") if isinstance(s["plan"], dict) else None
            for e2 in (plan_edits if isinstance(plan_edits, list) else []):
                if not isinstance(e2, dict) or not str(e2.get("find", "")).strip(): continue
                k = norm_find(e2.get("find"))
                if any(x["_k"] == k for x in edits): continue
                if len(str(e2.get("find", ""))) > 2000 or len(str(e2.get("replace", ""))) > 8000:
                    self.log(t, "⛔ 已丢弃超长编辑（find/replace 超限），条款：" + str(e2.get("clause", ""))[:40])
                    continue
                src = resolve_item(e2, s["sec"])
                e2["_k"] = k; e2["_sec"] = s["sec"]; e2["section"] = s["sec"]; e2["appNo"] = app_no
                e2["clause"] = str(e2.get("clause") or (src["clause"] if src else "") or "")
                e2["opinion"] = (src["opinion"] if src else "") or e2.get("opinion") or e2.get("clause") or ""
                e2["opName"] = (src.get("opName") if src else "") or e2.get("opName") or ""
                e2["clauseId"] = (src["cid"] if src else "") or str(e2.get("clauseId") or "")
                edits.append(e2)
            for lv in ((s["plan"].get("leftovers") if isinstance(s["plan"], dict) else None) or []):
                leftovers.append("【" + s["sec"] + "】" + str(lv))
        if len(failed_secs) == len(settled):
            raise ValueError("全部章节计划调用失败：" + (failed_secs[0] if failed_secs else ""))
        if failed_secs: self.log(t, "⚠️ 部分章节失败：" + "；".join(failed_secs))
        if not edits and not leftovers:
            raise ValueError("各章节均未产出有效编辑")
        tmp_dir = Path(t["dir"]) / "work" / "tmp"; tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "plan.json").write_text(json.dumps({
            "appNo": app_no, "appName": t["app"]["name"], "sections": sec_order,
            "edits": edits, "leftovers": leftovers,
            "pool": {"summary": snap.get("summary") or "", "hit": snap.get("hit") or {}, "notes": snap.get("notes") or []},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(t, "🧩 合并计划：" + str(len(edits)) + " 条编辑 / " + str(len(leftovers)) + " 条遗留")
        return edits, leftovers

    async def run_task(self, t):
        try:
            t["startedAt"] = now_str()
            await self.prepare(t)
            t["status"] = "running"; self.persist(t)
            self.log(t, "🚀 大模型直连模式（生成编辑计划，等待人工确认）")
            edits, leftovers = await self.generate_plan(t)
            t["status"] = "planned"; self.persist(t)
            self.log(t, "📋 计划就绪：" + str(len(edits)) + " 条编辑 / " + str(len(leftovers)) + " 条遗留 —— 请在前端核对“修改前/修改后”，确认后才会写入文件")
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
            self.log(t, "✍️ 人工确认完成（" + str(len(edits)) + " 条编辑），开始写入文件…")
            tmp_dir = Path(t["dir"]) / "work" / "tmp"; tmp_dir.mkdir(parents=True, exist_ok=True)
            plan_path = tmp_dir / "plan.json"
            plan_path.write_text(json.dumps({
                "appNo": app_no_of(t["app"]["name"]), "appName": t["app"]["name"],
                "edits": edits, "leftovers": leftovers,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

            out_dir = Path(t["dir"]) / "work" / "output"; out_dir.mkdir(parents=True, exist_ok=True)
            stem = stem_of(t["app"]["name"])
            out_docx = out_dir / (stem + "_修改后.docx"); bak_docx = out_dir / (stem + "_备份.docx")
            so, se, rc = await self._py([SCRIPTS_DIR / "apply_edits.py", Path(t["dir"]) / "work" / "input" / t["app"]["name"], out_docx, bak_docx, plan_path], timeout=300)
            if rc != 0:
                t["status"] = "failed"; t["error"] = "编辑执行器失败：" + (se or so or "rc!=0")[:400]; return

            applied = json.loads(so).get("results") or []
            misses = sum(1 for a2 in applied if a2.get("status") == "miss")
            note = ("（" + str(misses) + " 处未命中，转人工）") if misses else ""
            self.log(t, "🎯 落盘 " + str(len(applied) - misses) + "/" + str(len(applied)) + " 处" + note)

            check_txt = tmp_dir / "_final.txt"
            await self._py([SCRIPTS_DIR / "sb_extract.py", out_docx, check_txt])
            final_text = check_txt.read_text(encoding="utf-8") if check_txt.exists() else ""
            nrm = lambda x: re.sub(r"\s+", "", str(x or ""))

            rows = []
            for i2, a2 in enumerate(applied):
                e2 = edits[i2] if i2 < len(edits) else {}
                st = "✅ 已改"
                if a2.get("status") == "miss": st = "⚠️ 未命中·需人工定位"
                else:
                    rep_n = nrm(e2.get("replace"))[:50]
                    if rep_n and rep_n not in nrm(final_text): st = "⚠️ 已改·终检未检出"
                rows.append("| " + str(i2 + 1) + " | " + str(e2.get("_sec", e2.get("section", "-"))) + " | " + esc_md(e2.get("clause"))[:70] + " | " + esc_md(e2.get("find"))[:40] + "… | " + esc_md(e2.get("opinion") or e2.get("clause"))[:70] + " | " + esc_md(e2.get("replace"))[:40] + "… | " + st + " |")
            report_lines = ["# 修改对照表", "", "> 管线：大模型出计划 → 人工修订 → 内置执行器落盘　生成时间：" + now_str(), "", "| # | 章节 | 意见条款 | 改前摘录 | 修改意见 | 改后摘录 | 结果 |", "|---|---|---|---|---|---|---|"] + rows
            (out_dir / "修改对照表.md").write_text("\n".join(report_lines), encoding="utf-8")
            lo_txt = "\n".join(str(i2 + 1) + ". " + s for i2, s in enumerate(leftovers)) if leftovers else "（无）"
            (out_dir / "遗留事项.md").write_text("# 遗留事项（需人工补充真实数据）\n\n" + lo_txt, encoding="utf-8")

            if not await self.verify_outputs(t):
                t["status"] = "failed"; t["error"] = "成品校验未通过（详见产出校验信息）"; return
            t["status"] = "done"
            self.log(t, "✅ 完成：编辑 " + str(len(applied) - misses) + "/" + str(len(applied)) + "，遗留 " + str(len(leftovers)) + " 条，产出 " + str(len(t["outputs"])) + " 个文件")
        except Exception as e:
            import traceback; t["status"] = "failed"
            t["error"] = traceback.format_exc()[-900:]
        finally:
            t["finishedAt"] = now_str(); self.persist(t)
