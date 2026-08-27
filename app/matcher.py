"""v2 匹配规则引擎移植（纯函数）。强配对：编号90/复合码前缀85/全名包含100；其余走仲裁或通用池"""
from __future__ import annotations
import re

NOISE_CODE = re.compile(r"^(iso|qs|ocr|sd|ma|q|a|p|gb|ieee)\d*", re.I)
DATE_CODE = re.compile(r"^2[56]\d{4}$")
CN_STOP = {"申报", "人才", "项目", "企业", "论文", "专利", "附件", "材料", "意见"}

_HW = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}

def norm(s) -> str:
    s = str(s or "").lower()
    s = "".join(_HW.get(ch, ch) for ch in s)
    s = s.replace("·", "").replace("･", "")
    return "".join(s.split())

def num_norm(s) -> str:
    return "".join(ch for ch in norm(s) if ch.isalnum() or ch == "-")

def stem_of(name) -> str:
    i = name.rfind(".")
    return name[:i] if i > 0 else name

def ext_of(name) -> str:
    i = name.rfind(".")
    return name[i:].lower() if i >= 0 else ""

def is_app_content(text) -> bool:
    t = str(text or "")
    if len(t) < 3000:
        return False
    if "{{" in t:
        return False
    return ("有效证件姓名" in t) or ("申报企业" in t) or ("代表性论文" in t)

def extract_book_profile(fname: str, txt: str) -> dict:
    p = {"file": fname, "nums": [], "nameFull": "", "tokens": [], "ent": ""}
    for raw in re.findall(r"\d{4,6}", fname):
        if DATE_CODE.fullmatch(raw):
            continue
        if not NOISE_CODE.match(raw):
            p["nums"].append(raw)
    lines = String_splitlines(txt)
    nl = next((l for l in lines if "申报人" in l and "有效证件姓名" in l and not re.search(r"填写|一致|护照|身份证", l)), None)
    if nl:
        m = re.search(r"申报人\s*(.*?)\s*有效证件姓名", nl)
        if m:
            nm = m.group(1)
        else:
            tmp = re.sub(r"有效证件姓名.*", "", nl)
            m2 = re.search(r"申报人\s*(.*)", tmp)
            nm = m2.group(1) if m2 else ""
        nm = re.sub(r"[()（）+＋]", " ", nm).strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z\s.'\-]{3,}", nm):
            p["nameFull"] = norm(nm)
            p["tokens"] = [t.lower() for t in nm.split() if len(t) >= 4]
    if not p["nameFull"]:
        fnm = re.sub(r"^有企业\+?", "", fname).split("申报书")[0]
        fnm = re.split(r"[+＋]", fnm)[0].strip()
        if re.fullmatch(r"[A-Za-z][A-Za-z\s.'\-]{3,}", fnm):
            p["nameFull"] = norm(fnm)
            p["tokens"] = [t.lower() for t in fnm.split() if len(t) >= 4]
    el = next((l for l in lines if "申报企业" in l), "")
    if el:
        p["ent"] = el.split("申报企业", 1)[1].strip()[:40]
    return p

def String_splitlines(txt):
    return str(txt or "").replace("\r\n", "\n").split("\n")

def split_opinion_blocks(text) -> list:
    blocks = []
    cur = []
    def push():
        nonlocal cur
        if cur and "".join(cur).strip():
            blocks.append("\n".join(cur).strip())
        cur = []
    for raw in String_splitlines(text):
        l = raw.strip()
        is_new = (
            re.match(r"\d{4,6}\s*(个?人才|的?修改意见|[:：]?)", l)
            or re.match(r"[A-Za-z][A-Za-z .'\-]{3,40}[：:]", l)
            or re.fullmatch(r"[A-Z][A-Z .'\-]{4,40}", l)
            or (re.match(r"[A-Za-z][A-Za-z'\- ]{2,30}(（[^）]{2,40}）)?\s*$", l) and any(c.isupper() for c in l) and not re.search(r"[，。；,.]$", l))
            or (re.match(r"（?[一二三四五六七八九十]+）", l) and len(l) < 60)
        )
        if is_new and cur:
            push()
        cur.append(raw)
    push()
    merged = []
    for blk in blocks:
        if len(blk) < 12 and merged:
            merged[-1] += "\n" + blk
        else:
            merged.append(blk)
    return merged

def extract_block_ids(block) -> dict:
    n_blk = norm(block)
    nums = []
    for m in re.findall(r"\d{4,6}", block):
        if re.fullmatch(r"(19|20)\d\d", m):
            continue
        if m not in nums:
            nums.append(m)
    for m in re.findall(r"[A-Za-z]{1,6}-?\d{3,}", block):
        nn = num_norm(m)
        if not NOISE_CODE.match(nn) and nn not in nums:
            nums.append(nn)
    names = []
    for m in re.findall(r"\b[A-Z][A-Z'.\- ]{5,60}\b", block):
        v = m.strip()
        if v not in names:
            names.append(v)
    cn_alias = []
    for m in re.findall(r"[\u4e00-\u9fa5]{2,6}(?=Vishaal|（|[A-Za-z]{2,})", block):
        if m not in CN_STOP and m not in cn_alias:
            cn_alias.append(m)
    return {"nBlk": n_blk, "nums": nums, "names": names, "cnAlias": cn_alias, "hasId": bool(nums or names)}

def score_block(ids: dict, profile: dict):
    score = 0
    why = []
    for bn in profile["nums"]:
        hit_prefix = False
        for xn in ids["nums"]:
            if xn == bn:
                return 90, ["编号=" + bn]
            if len(bn) >= 5 and len(xn) >= 5 and (xn.startswith(bn) or bn.startswith(xn)):
                hit_prefix = True
        if hit_prefix and score < 85:
            score = 85
            why = ["前缀码=" + bn]
    nf = profile.get("nameFull", "")
    if nf and len(nf) >= 6 and (nf in ids["nBlk"] or ids["nBlk"] in nf):
        return 100, ["全名"]
    return score, why

def match_batch(book_profiles, opinion_files):
    books = [{"profile": p, "matched": []} for p in book_profiles]
    unmatched, generic_pool, shared = [], [], []
    for opf in opinion_files:
        blocks = split_opinion_blocks(opf["text"])
        for bi, blk in enumerate(blocks):
            ids = extract_block_ids(blk)
            scored = []
            for b in books:
                sc, why = score_block(ids, b["profile"])
                if sc >= 85:
                    scored.append({"book": b, "sc": sc, "why": why})
            head = blk.split("\n")[0][:50]
            excerpt = " ".join(blk.split())[:180]
            base = {"opName": opf["name"], "blockIdx": bi, "head": head, "excerpt": excerpt, "text": blk}
            if len(scored) >= 2:
                shared.append(dict(base, books=[x["book"]["profile"]["file"] for x in scored], scores=[x["sc"] for x in scored]))
            elif len(scored) == 1:
                x = scored[0]
                x["book"]["matched"].append(dict(base, score=x["sc"], evidence=",".join(x["why"])))
            elif ids["hasId"]:
                unmatched.append(dict(base, ids={"nums": ids["nums"][:4], "names": ids["names"][:2]}))
            else:
                generic_pool.append(dict(base))
    return {
        "books": [{"file": b["profile"]["file"], "name": b["profile"]["nameFull"], "ent": b["profile"]["ent"], "nums": b["profile"]["nums"], "matched": b["matched"]} for b in books],
        "unmatched": unmatched, "shared": shared, "genericPool": generic_pool,
    }
