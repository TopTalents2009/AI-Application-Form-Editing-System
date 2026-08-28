"""批次处理：提取 → 匹配 → LLM仲裁 → ready；start 时逐书派生任务"""
from __future__ import annotations
import asyncio, base64, json, os, re, secrets, shutil, time
from pathlib import Path
from .config import BATCHES_DIR, SCRIPTS_DIR, PYEXE, load_config, LLM_TIMEOUT_MATCH, atomic_replace, resolve_llm
from .llm import chat, extract_json, now_str, created_key
from . import matcher as M

PYENV = dict(os.environ, PYTHONIOENCODING="utf-8")
ALLOWED = (".docx", ".wps", ".txt", ".md")

def _rid(): return "b" + format(int(time.time() * 1000), "x") + "-" + secrets.token_hex(3)
def _sanitize(name): 
    base = str(name or "f").replace("\\", "/").split("/")[-1]
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", base).strip() or "f"
def _ext(n): i = n.rfind("."); return n[i:].lower() if i >= 0 else ""

def _as_int(v):
    try:
        return int(v)
    except Exception:
        return None

class BatchStore:
    def __init__(self, root=None, runner=None):
        self.root = Path(root or BATCHES_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.batches = {}
        self.load_all()

    def bdir(self, bid): return self.root / bid

    def persist(self, b):
        clone = {k: v for k, v in b.items() if k != "dir"}
        tmp = str(self.bdir(b["id"]) / "meta.json") + ".tmp"
        Path(tmp).write_text(json.dumps(clone, ensure_ascii=False, indent=2), encoding="utf-8")
        atomic_replace(tmp, self.bdir(b["id"]) / "meta.json")

    def log(self, b, msg):
        b.setdefault("log", []).append({"t": now_str(), "msg": str(msg)})
        self.persist(b)

    def load_all(self):
        if not self.root.exists(): return
        for bid in os.listdir(self.root):
            mp = self.bdir(bid) / "meta.json"
            if not mp.exists(): continue
            try:
                b = json.loads(mp.read_text(encoding="utf-8")); b["dir"] = str(self.bdir(bid))
                if b.get("status") not in ("ready", "started"):
                    b["status"] = "failed"; b["error"] = b.get("error") or "中断"
                self.batches[b["id"]] = b
            except Exception:
                pass

    async def _ensure_txt(self, src: Path, out: Path) -> bool:
        ext = _ext(str(src))
        if ext in (".txt", ".md"):
            shutil.copyfile(src, out); return True
        proc = await asyncio.create_subprocess_exec(PYEXE, str(SCRIPTS_DIR / "sb_extract.py"), str(src), str(out), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=PYENV)
        await proc.communicate()
        return proc.returncode == 0

    async def create(self, body) -> dict:
        apps = body.get("apps") or []; ops = body.get("opinions") or []
        if not apps: raise ValueError("至少上传一份申报书")
        for a in apps:
            if _ext(_sanitize(a.get("name", ""))) != ".docx":
                raise ValueError("申报书必须为 .docx: " + str(a.get("name")))
        if not ops: raise ValueError("至少上传一份意见文档")
        for o in ops:
            if _ext(_sanitize(o.get("name", ""))) not in ALLOWED:
                raise ValueError("意见类型不支持: " + str(o.get("name")))
        bid = _rid(); d = self.bdir(bid)
        (d / "input").mkdir(parents=True, exist_ok=True)
        app_names = []
        for a in apps:
            n = _sanitize(a["name"])
            (d / "input" / n).write_bytes(base64.b64decode(a.get("dataB64") or ""))
            app_names.append(n)
        op_names = []
        for o in ops:
            n = _sanitize(o["name"])
            (d / "input" / n).write_bytes(base64.b64decode(o.get("dataB64") or ""))
            op_names.append(n)
        model_id = body.get("model")
        eng = str(body.get("engine") or "")
        if eng and eng != "api" and not model_id:
            model_id = eng
        try:
            prof = resolve_llm(model_id)
        except ValueError:
            prof = {"id": "", "label": ""}
        b = {"id": bid, "dir": str(d), "status": "extracting", "createdAt": now_str(),
             "model": prof.get("id") or "", "modelLabel": prof.get("label") or "",
             "apps": app_names, "opinions": op_names, "log": [], "match": None, "error": None, "taskIds": []}
        self.batches[bid] = b; self.persist(b)
        asyncio.get_event_loop().create_task(self._process(b))
        return b

    async def _process(self, b):
        try:
            txt_dir = Path(b["dir"]) / "txt"; txt_dir.mkdir(parents=True, exist_ok=True)
            app_texts, op_texts = {}, {}
            for n in b["apps"]:
                out = txt_dir / (Path(n).stem + ".txt")
                if await self._ensure_txt(Path(b["dir"]) / "input" / n, out):
                    app_texts[n] = out.read_text(encoding="utf-8")
                else:
                    self.log(b, "⚠️ 提取失败 " + n)
            for n in b["opinions"]:
                out = txt_dir / (Path(n).stem + ".txt")
                if await self._ensure_txt(Path(b["dir"]) / "input" / n, out):
                    op_texts[n] = out.read_text(encoding="utf-8")
                else:
                    self.log(b, "⚠️ 提取失败 " + n)
            # 内容级校验：每本上传的书必须真的像已填写申报书
            for n in b["apps"]:
                tx = app_texts.get(n, "")
                if not M.is_app_content(tx):
                    reason = "无法提取正文，可能不是 Word(.docx) 申报书" if not tx else "不像一份已填写的申报书（正文过短、疑似未填写模板或缺封面关键字段）"
                    b["status"] = "failed"
                    b["error"] = "文件「" + n + "」" + reason + "。请确认没有把意见文档、空白模板或其它文件误选进申报书栏。"
                    self.persist(b); return
            b["status"] = "matching"; self.persist(b)
            profiles = [M.extract_book_profile(n, app_texts[n]) for n in b["apps"] if n in app_texts]
            result = M.match_batch(profiles, [{"name": n, "text": t} for n, t in op_texts.items()])
            self.log(b, "🧮 规则匹配完成")
            cfg = load_config()
            if result["unmatched"] and cfg["configured"]:
                b["status"] = "arbitrating"; self.persist(b)
                self.log(b, "⚖️ LLM 仲裁 " + str(len(result["unmatched"])) + " 个待定块…")
                try:
                    nl = chr(10)
                    book_list = nl.join(str(i2 + 1) + ". " + x["file"] + (("（" + x["name"] + "）") if x["name"] else "") + ((" 编号:" + "/".join(x["nums"])) if x["nums"] else "") for i2, x in enumerate(result["books"]))
                    blk_list = nl.join(str(i2) + ". [" + u["opName"] + "#" + str(u["blockIdx"]) + "] " + u["head"] + " | 标识:" + json.dumps(u["ids"], ensure_ascii=False) for i2, u in enumerate(result["unmatched"]))
                    prompt = ("你是申报书意见分派员。下面列出候选申报书与待归属的意见块，请判断每个块属于哪本申报书；无法确定或属多人共享则 bookFile 填 null。" + nl + nl
                              + "【候选申报书】" + nl + book_list + nl + nl + "【待归属意见块】" + nl + blk_list + nl + nl
                              + '仅输出 JSON：{"assignments":[{"index":0,"bookFile":"xxx.docx"或null,"reason":"简短依据"}]}')
                    resp = await chat([{"role": "user", "content": prompt}], json_mode=True, timeout_s=LLM_TIMEOUT_MATCH, model=b.get("model"))
                    aj = extract_json(resp["content"])
                    moved_idx = set(); moved = 0; bad_idx = 0
                    for asg in (aj.get("assignments") or []):
                        if not isinstance(asg, dict):
                            continue
                        idx = _as_int(asg.get("index")); bf2 = asg.get("bookFile")
                        if idx is None or not (0 <= idx < len(result["unmatched"])):
                            bad_idx += 1
                            continue
                        u = result["unmatched"][idx]
                        book = next((x for x in result["books"] if x["file"] == bf2), None) if (u and bf2) else None
                        if u and book:
                            book["matched"].append({"opName": u["opName"], "blockIdx": u["blockIdx"], "head": u["head"], "excerpt": u["excerpt"], "text": u.get("text") or u.get("excerpt") or "", "score": 75, "evidence": "llm仲裁:" + str(asg.get("reason") or "")[:40]})
                            moved += 1; moved_idx.add(idx)
                    result["unmatched"] = [u for i2, u in enumerate(result["unmatched"]) if i2 not in moved_idx]
                    extra = ("，跳过无效 index " + str(bad_idx)) if bad_idx else ""
                    self.log(b, "⚖️ 仲裁移入 " + str(moved) + " 块，余 " + str(len(result["unmatched"])) + " 块未配对" + extra)
                except Exception as e2:
                    self.log(b, "⚠️ 仲裁跳过：" + str(e2)[:120])
            b["match"] = result
            b["status"] = "ready"
            cnt = sum(1 for x in result["books"] if x["matched"])
            self.log(b, "✅ 匹配就绪：" + str(cnt) + " 本书有配对；未配对 " + str(len(result["unmatched"])) + "；共享 " + str(len(result["shared"])) + "；通用 " + str(len(result["genericPool"])))
        except Exception as e:
            import traceback
            b["status"] = "failed"
            b["error"] = traceback.format_exc()[-800:]
            self.persist(b)

    def start(self, bid: str, opts=None) -> list:
        b = self.batches.get(bid)
        if not b: raise ValueError("批次不存在")
        if b["status"] != "ready": raise ValueError("批次状态为 " + b["status"] + "，不能开始")
        opts = opts or {}
        include_generic = bool(opts.get("includeGeneric", True))
        match = b.get("match") or {}
        books = match.get("books") or []
        by_file = {bk["file"]: list(bk.get("matched") or []) for bk in books}

        def _idx(v):
            n = _as_int(v)
            return n if n is not None else -1

        def _key(op_name, block_idx):
            return (str(op_name or ""), _idx(block_idx))

        picks = opts.get("picks")
        if picks is not None:
            wanted = {}
            for p in picks:
                if not isinstance(p, dict): continue
                bf = str(p.get("bookFile") or "")
                wanted.setdefault(bf, set()).add(_key(p.get("opName"), p.get("blockIdx")))
            for bf in list(by_file):
                by_file[bf] = [m for m in by_file[bf] if _key(m.get("opName"), m.get("blockIdx")) in wanted.get(bf, set())]

        shared_src = {}
        for s in (match.get("shared") or []):
            shared_src[_key(s.get("opName"), s.get("blockIdx"))] = s
        for a in (opts.get("shared") or []):
            if not isinstance(a, dict): continue
            src = shared_src.get(_key(a.get("opName"), a.get("blockIdx")))
            if not src: continue
            for bf in (a.get("books") or []):
                bf = str(bf or "")
                if bf in by_file:
                    by_file[bf].append(src)

        def _seg_body(m3):
            return str(m3.get("text") or m3.get("excerpt") or "")

        created = []
        for book in books:
            items = by_file.get(book["file"]) or []
            if not items:
                continue
            segs = []
            for i3, m3 in enumerate(items):
                segs.append({"name": _sanitize(Path(m3["opName"]).stem) + "__b" + str(m3["blockIdx"]) + "_" + str(i3 + 1) + ".txt",
                             "content": "【来源: " + m3["opName"] + " 片段#" + str(m3["blockIdx"]) + " | 证据: " + str(m3.get("evidence") or "共享") + "】" + chr(10) + _seg_body(m3)})
            if include_generic:
                for g in (match.get("genericPool") or []):
                    segs.append({"name": _sanitize(Path(g["opName"]).stem) + "__g" + str(g["blockIdx"]) + ".txt",
                                 "content": "【通用规范条款 | 来源: " + g["opName"] + "】" + chr(10) + _seg_body(g)})
            payload = {"engine": "api", "model": b.get("model") or "", "batchId": bid,
                       "app": {"name": book["file"], "dataB64": base64.b64encode((Path(b["dir"]) / "input" / book["file"]).read_bytes()).decode()},
                       "opinions": [{"name": s["name"], "dataB64": base64.b64encode(s["content"].encode("utf-8")).decode()} for s in segs]}
            t = self.runner.create(payload)
            self.runner.enqueue(t["id"])
            created.append(t["id"])
        b["status"] = "started"; b["taskIds"] = created; self.persist(b)
        return created

    def list_meta(self) -> list:
        arr = []
        for x in self.batches.values():
            d = {k: v for k, v in x.items() if k not in ("dir", "log", "match")}
            d["logCount"] = len(x.get("log", []))
            arr.append(d)
        return sorted(arr, key=lambda x: created_key(x.get("createdAt")), reverse=True)

    def get(self, bid): return self.batches.get(bid)

