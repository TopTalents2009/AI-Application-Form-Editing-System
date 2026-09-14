"""批次处理：提取 → 匹配 → LLM仲裁 → ready；start 时逐书派生任务"""
from __future__ import annotations
import asyncio, base64, json, os, re, secrets, time
from pathlib import Path
from .config import BATCHES_DIR, load_config, LLM_TIMEOUT_MATCH, atomic_replace, resolve_gemini
from .llm import chat, extract_json, now_str, created_key
from . import matcher as M
from .form_kind import classify as classify_form
from .opinion_extract import ALLOWED_OPINION_EXT, ensure_txt as extract_to_txt
from .pdf_app import ALLOWED_APP_EXT, APP_EXT_HINT, ensure_app_docx, sniff_pdf, work_docx_name
from .inline_opinions import NO_OPINION_MSG, extract_inline_opinion_text, split_inline_units

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

    async def create(self, body, owner: str = None) -> dict:
        apps = body.get("apps") or []; ops = body.get("opinions") or []
        if not apps: raise ValueError("至少上传一份申报书")
        for a in apps:
            if _ext(_sanitize(a.get("name", ""))) not in ALLOWED_APP_EXT:
                raise ValueError("申报书必须为 " + APP_EXT_HINT + ": " + str(a.get("name")))
        for o in ops:
            if _ext(_sanitize(o.get("name", ""))) not in ALLOWED_OPINION_EXT:
                raise ValueError("意见类型不支持（Word / Excel / 图片 / txt / md）: " + str(o.get("name")))
        bid = _rid(); d = self.bdir(bid)
        (d / "input").mkdir(parents=True, exist_ok=True)
        app_names = []
        for a in apps:
            n = _sanitize(a["name"])
            raw = base64.b64decode(a.get("dataB64") or "")
            if _ext(n) == ".pdf":
                sniff_pdf(raw, n)
            (d / "input" / n).write_bytes(raw)
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
            prof = resolve_gemini(model_id)
        except ValueError:
            prof = {"id": "", "label": ""}
        b = {"id": bid, "dir": str(d), "status": "extracting", "createdAt": now_str(),
             "model": prof.get("id") or "", "modelLabel": prof.get("label") or "",
             "apps": app_names, "opinions": op_names, "log": [], "match": None, "error": None, "taskIds": []}
        if owner:
            b["owner"] = str(owner)
        self.batches[bid] = b; self.persist(b)
        asyncio.get_event_loop().create_task(self._process(b))
        return b

    async def _process(self, b):
        try:
            txt_dir = Path(b["dir"]) / "txt"; txt_dir.mkdir(parents=True, exist_ok=True)
            app_texts, op_texts = {}, {}
            conv_dir = Path(b["dir"]) / "converted"
            conv_dir.mkdir(parents=True, exist_ok=True)
            for n in b["apps"]:
                src = Path(b["dir"]) / "input" / n
                out = txt_dir / (Path(n).stem + ".txt")
                try:
                    if _ext(n) == ".pdf":
                        docx = conv_dir / work_docx_name(n)
                        engine = await ensure_app_docx(src, docx)
                        self.log(b, "数字 PDF 已转为 Word " + n + " → " + docx.name + "（" + str(engine) + "）")
                        await extract_to_txt(docx, out)
                    else:
                        await extract_to_txt(src, out)
                    app_texts[n] = out.read_text(encoding="utf-8")
                except Exception as e:
                    b["status"] = "failed"
                    b["error"] = "申报书「" + n + "」提取失败：" + str(e)[:240]
                    self.log(b, b["error"])
                    self.persist(b)
                    return
            for n in b["opinions"]:
                out = txt_dir / (Path(n).stem + ".txt")
                try:
                    await extract_to_txt(Path(b["dir"]) / "input" / n, out)
                    op_texts[n] = out.read_text(encoding="utf-8")
                except Exception as e:
                    self.log(b, "提取失败 " + n + ": " + str(e)[:200])
            if not b["opinions"]:
                inline_ops = {}
                for n in b["apps"]:
                    src = Path(b["dir"]) / "input" / n
                    if _ext(n) == ".pdf":
                        cand = conv_dir / work_docx_name(n)
                        if cand.exists():
                            src = cand
                    text, n_cmt = extract_inline_opinion_text(src)
                    if not text:
                        self.log(b, "申报书「" + n + "」" + NO_OPINION_MSG)
                        continue
                    op_name = Path(n).stem + "（标注意见）.txt"
                    (txt_dir / op_name).write_text(text, encoding="utf-8")
                    op_texts[op_name] = text
                    inline_ops[n] = (op_name, text, n_cmt)
                    self.log(b, "申报书「" + n + "」标注栏 " + str(n_cmt) + " 条")
                if not inline_ops:
                    b["status"] = "failed"
                    b["error"] = NO_OPINION_MSG
                    self.persist(b)
                    return
                app_kinds = {}
                for n in b["apps"]:
                    tx = app_texts.get(n, "")
                    if not M.is_app_content(tx):
                        reason = "无法提取正文，可能不是 Word / Excel / 数字PDF 申报书" if not tx else "不像一份已填写的申报书（正文过短、疑似未填写模板或缺封面关键字段）"
                        b["status"] = "failed"
                        b["error"] = "文件「" + n + "」" + reason + "。请确认没有把意见文档、空白模板或其它文件误选进申报书栏。"
                        self.persist(b)
                        return
                    kind = classify_form(tx, n)
                    if kind:
                        app_kinds[n] = kind
                b["appKinds"] = app_kinds
                profiles = [M.extract_book_profile(n, app_texts[n]) for n in b["apps"] if n in app_texts]
                books = []
                for p in profiles:
                    n = p["file"]
                    matched = []
                    if n in inline_ops:
                        op_name, text, _n_cmt = inline_ops[n]
                        units = split_inline_units(text) or ([text] if text.strip() else [])
                        for bi, blk in enumerate(units):
                            matched.append({
                                "opName": op_name, "blockIdx": bi,
                                "head": (blk.split("\n")[0] if blk else "")[:50],
                                "excerpt": " ".join(blk.split())[:180],
                                "text": blk, "score": 100, "evidence": "申报书标注栏",
                            })
                    books.append({
                        "file": p["file"], "name": p["nameFull"], "ent": p["ent"],
                        "nums": p["nums"], "matched": matched,
                    })
                b["match"] = {"books": books, "unmatched": [], "shared": [], "genericPool": []}
                b["status"] = "ready"
                hit = sum(1 for x in books if x["matched"])
                self.log(b, "未上传意见文档，已按各书标注栏配对 " + str(hit) + " 本")
                self.persist(b)
                return
            if not op_texts:
                b["status"] = "failed"
                b["error"] = "意见文档全部提取失败（Excel 无法解析，或图片需 Gemini 识字失败）"
                self.persist(b)
                return
            # 内容级校验：每本上传的书必须真的像已填写申报书
            app_kinds = {}
            for n in b["apps"]:
                tx = app_texts.get(n, "")
                if not M.is_app_content(tx):
                    reason = "无法提取正文，可能不是 Word / Excel / 数字PDF 申报书" if not tx else "不像一份已填写的申报书（正文过短、疑似未填写模板或缺封面关键字段）"
                    b["status"] = "failed"
                    b["error"] = "文件「" + n + "」" + reason + "。请确认没有把意见文档、空白模板或其它文件误选进申报书栏。"
                    self.persist(b); return
                kind = classify_form(tx, n)
                if kind:
                    app_kinds[n] = kind
            b["appKinds"] = app_kinds
            if app_kinds:
                self.log(b, "申报书模板：" + "；".join(n + "=" + k for n, k in app_kinds.items()))
            b["status"] = "matching"; self.persist(b)
            profiles = [M.extract_book_profile(n, app_texts[n]) for n in b["apps"] if n in app_texts]
            result = M.match_batch(profiles, [{"name": n, "text": t} for n, t in op_texts.items()])
            self.log(b, "规则匹配完成")
            cfg = load_config()
            if result["unmatched"] and cfg["configured"]:
                b["status"] = "arbitrating"; self.persist(b)
                self.log(b, "LLM 仲裁 " + str(len(result["unmatched"])) + " 个待定块…")
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
                    self.log(b, "仲裁移入 " + str(moved) + " 块，余 " + str(len(result["unmatched"])) + " 块未配对" + extra)
                except Exception as e2:
                    self.log(b, "仲裁跳过：" + str(e2)[:120])
            b["match"] = result
            b["status"] = "ready"
            cnt = sum(1 for x in result["books"] if x["matched"])
            self.log(b, "匹配就绪：" + str(cnt) + " 本书有配对；未配对 " + str(len(result["unmatched"])) + "；共享 " + str(len(result["shared"])) + "；通用 " + str(len(result["genericPool"])))
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
            app_payload = {"name": book["file"], "dataB64": base64.b64encode((Path(b["dir"]) / "input" / book["file"]).read_bytes()).decode()}
            kind = str((b.get("appKinds") or {}).get(book["file"]) or "")
            if kind:
                app_payload["mode"] = kind
            payload = {"engine": "api", "model": b.get("model") or "", "batchId": bid,
                       "app": app_payload,
                       "opinions": [{"name": s["name"], "dataB64": base64.b64encode(s["content"].encode("utf-8")).decode()} for s in segs]}
            t = self.runner.create(payload, owner=b.get("owner"))
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

