"""v2 匹配规则引擎移植（纯函数）。强配对：编号90/复合码前缀85/全名包含100；其余走仲裁或通用池"""
from __future__ import annotations
import re

NOISE_CODE = re.compile(r"^(iso|qs|ocr|sd|ma|q|a|p|gb|ieee)\d*", re.I)
DATE_CODE = re.compile(r"^2[56]\d{4}$")
MDATE_CODE = re.compile(r"^0\d{3}$")  # 文件名里的月日残留，如 0310
CN_STOP = {"申报", "人才", "项目", "企业", "论文", "专利", "附件", "材料", "意见"}
NAME_STOP = CN_STOP | {"青年", "创新", "团队", "博士", "教授", "总结", "修改", "个人", "基本情况"}
LATIN_NAME = re.compile(r"[A-Za-z][A-Za-z\s.',\-]{3,}$")
CN_NAME = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")
TRUNC_CO = re.compile(r"(有限公|股份有限|有限责|股份有|责任公|有限$|股份$|公司（[^）]*$|（[^）]*$)$")

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

def is_book_num(raw: str) -> bool:
    s = str(raw or "")
    if DATE_CODE.fullmatch(s) or MDATE_CODE.fullmatch(s):
        return False
    if NOISE_CODE.match(s):
        return False
    return bool(s)

def extract_book_nums(fname: str) -> list:
    seen, nums = set(), []
    for raw in re.findall(r"\d{4,6}", str(fname or "")):
        if raw in seen or not is_book_num(raw):
            continue
        seen.add(raw)
        nums.append(raw)
    longs = [n for n in nums if len(n) >= 5]
    return longs or nums

def accept_person_name(raw: str) -> tuple:
    nm = re.sub(r"[()（）+＋]", " ", str(raw or ""))
    nm = re.sub(r"\s+", " ", nm).strip(" ：:\t|｜,，")
    if not nm or nm in NAME_STOP:
        return "", []
    if LATIN_NAME.fullmatch(nm):
        toks = [t.lower() for t in re.split(r"[\s,，]+", nm) if len(t) >= 4]
        return norm(nm), toks
    if CN_NAME.fullmatch(nm) and nm not in NAME_STOP:
        return nm, [nm]
    cn_m = re.search(r"[\u4e00-\u9fa5]{2,4}", nm)
    lat = re.findall(r"[A-Za-z]{3,}", nm)
    if cn_m and lat:
        cn = cn_m.group(0)
        if cn in NAME_STOP:
            cn = ""
        toks = [t.lower() for t in lat if len(t) >= 4]
        if cn:
            toks.append(cn)
        if toks or cn:
            return norm(nm), toks
    return "", []

def looks_truncated_company(s: str) -> bool:
    s = str(s or "").strip()
    if not s:
        return True
    if TRUNC_CO.search(s):
        return True
    if s.endswith("公") and "公司" not in s:
        return True
    if s.count("（") > s.count("）"):
        return True
    return False

def stitch_company(base: str, lines, hit_idx: int = -1) -> str:
    out = re.sub(r"^[|｜\s]+", "", str(base or "").strip())
    if hit_idx < 0:
        return out[:80]
    for j in range(hit_idx + 1, min(hit_idx + 4, len(lines))):
        nxt = str(lines[j] or "").strip(" ：:\t|｜")
        if not nxt:
            continue
        if any(x in nxt for x in ("申报省市", "申报日期", "填表", "有效证件", "申报人", "申报企业", "企业名称")):
            break
        glue = looks_truncated_company(out) or nxt in ("司", "公司", "任公司") or (len(nxt) <= 8 and nxt.startswith("司"))
        if not glue:
            break
        out += nxt
        if len(out) >= 80:
            break
    return out[:80]

def pick_company(*cands) -> str:
    vals = []
    for c in cands:
        s = re.sub(r"^[|｜\s]+", "", str(c or "").strip())
        if s:
            vals.append(s[:80])
    if not vals:
        return ""
    best = vals[0]
    for s in vals[1:]:
        if s.startswith(best) and len(s) > len(best):
            best = s
        elif best.startswith(s) and looks_truncated_company(best) and not looks_truncated_company(s):
            best = s
        elif looks_truncated_company(best) and not looks_truncated_company(s):
            best = s
        elif looks_truncated_company(best) and looks_truncated_company(s) and len(s) > len(best):
            best = s
    return best[:80]

def extract_company(lines) -> str:
    first, idx = "", -1
    for i, raw in enumerate(lines):
        s = str(raw or "").strip()
        if "申报企业" not in s:
            continue
        if re.search(r"必须与|应填写|例如|填写内容", s):
            continue
        idx = i
        first = s.split("申报企业", 1)[-1].strip(" ：:\t|｜")
        break
    return stitch_company(first, lines, idx)

def extract_book_profile(fname: str, txt: str) -> dict:
    p = {"file": fname, "nums": extract_book_nums(fname), "nameFull": "", "tokens": [], "ent": ""}
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
        full, toks = accept_person_name(nm)
        if full:
            p["nameFull"], p["tokens"] = full, toks
    if not p["nameFull"]:
        fnm = re.sub(r"^有企业\+?", "", fname).split("申报书")[0]
        fnm = re.split(r"[+＋]", fnm)[0].strip()
        fnm = re.sub(r"\.\w+$", "", fnm)
        full, toks = accept_person_name(fnm)
        if full:
            p["nameFull"], p["tokens"] = full, toks
    p["ent"] = extract_company(lines)
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
    return {"nBlk": n_blk, "nums": nums, "names": names, "cnAlias": cn_alias, "hasId": bool(nums or names or cn_alias)}

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
    nf = str(profile.get("nameFull") or "")
    nblk = ids.get("nBlk") or ""
    if nf:
        cn_hit = any(a and (a == nf or a in nf or nf in a) for a in (ids.get("cnAlias") or []))
        if nf in nblk or cn_hit or (len(nf) >= 8 and len(nblk) >= 6 and nblk in nf):
            return 100, ["全名"]
    toks = [t for t in (profile.get("tokens") or []) if t and len(t) >= 2 and t not in NAME_STOP]
    if len(toks) >= 2:
        hit_n = sum(1 for t in toks if t in nblk)
        if hit_n >= 2 and score < 85:
            score = 85
            why = ["姓名词"]
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
