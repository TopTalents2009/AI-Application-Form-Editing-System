"""项目证明补缺：人才库未命中后，CodeBuddy 无头联网检索，再调用生成 API。"""
from __future__ import annotations
import json, os, re, shutil, subprocess
from pathlib import Path
from urllib.parse import urlparse
import httpx
from .config import FILL_MARK, load_config, httpx_trust_env
from . import papers as P

NAME_KEYS = ("项目名称", "课题名称", "grant_title", "project_name", "title")
EXTRA_KEYS = ("项目来源", "项目性质", "起止时间", "开始时间", "结束时间", "完成人排序", "立项时间", "经费总额", "担任角色", "项目编号")
_JUNK_PROJECT_NAME = re.compile(
    r"^(项目来源|项目性质|项目名称|课题名称|起止时间|开始时间|结束时间|完成人排序|"
    r"立项时间|经费总额|担任角色|项目编号|项目证明|项目材料|填表须知)$"
)
_AUTREF_KIND_PREF = (
    "project.certificate",
    "project.assignment",
    "project.acceptance",
    "project.closure",
)
URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
FILE_EXT = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".zip"}
SKIP_HOST = (
    "google.com", "bing.com", "baidu.com", "sogou.com", "duckduckgo.com",
    "arxiv.org", "export.arxiv.org",
    "github.com", "gitlab.com", "gitee.com", "bitbucket.org", "huggingface.co",
    "openaccess.thecvf.com", "thecvf.com",
    "ieeexplore.ieee.org", "dl.acm.org",
    "openreview.net", "paperswithcode.com", "semanticscholar.org",
    "researchgate.net", "academia.edu",
    "sciencedirect.com", "springer.com", "link.springer.com",
    "wiley.com", "mdpi.com", "nature.com", "science.org",
    "neurips.cc", "icml.cc", "openaccess.acm.org",
    "scholar.google.com", "scholar.google.",
    "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov",
    "wikipedia.org", "linkedin.com", "orcid.org",
    "youtube.com", "twitter.com", "x.com", "facebook.com",
)
PAPER_TITLE_RE = re.compile(
    r"\barxiv\b|\bcvpr\b|\biccv\b|\beccv\b|\bneurips\b|\bicml\b|\baacl\b|"
    r"\bgithub\b|\bgitlab\b|\bpreprint\b|预印本|会议论文|期刊论文|"
    r"open\s*access\s*(paper|pdf)|doi:\s*10\.\d+/",
    re.I,
)


def person_name(snap: dict) -> str:
    t = (snap or {}).get("talent") or {}
    n = str(t.get("name") or "").strip()
    payload = t.get("payload") if isinstance(t.get("payload"), dict) else {}
    info = payload.get("申报人基本信息") if isinstance(payload.get("申报人基本信息"), dict) else {}
    for k in ("有效证件姓名", "外籍专家中文姓名"):
        v = str(info.get(k) or "").strip()
        if v and "***" not in v:
            if not n:
                n = v
            elif v.lower() != n.lower() and v not in n:
                n = n + " / " + v
            break
    if n:
        return n
    names = ((snap or {}).get("keys") or {}).get("names") or []
    return str(names[0] or "").strip() if names else ""


def extract_projects(snap: dict, app_text: str = "") -> list:
    acc, seen = [], set()

    def add(name, extra=None):
        name = re.sub(r"\s+", " ", str(name or "")).strip(" ：:|｜")
        if len(name) < 4 or len(name) > 240:
            return
        if _JUNK_PROJECT_NAME.match(name) or name in EXTRA_KEYS or name in NAME_KEYS:
            return
        if re.search(r"填表须知|不得超过|限\s*\d+\s*字", name):
            return
        k = name.lower()
        if k in seen:
            return
        seen.add(k)
        row = {"name": name}
        if extra:
            for ek, ev in extra.items():
                if ev not in (None, "", "***"):
                    row[ek] = ev
        acc.append(row)

    payload = ((snap or {}).get("talent") or {}).get("payload")
    _walk_projects(payload, add)
    text = str(app_text or "")
    for m in re.finditer(
        r"项目\d+\s*[：:]\s*(?P<name>[^。\n]{4,80})"
        r"(?:。[^\n]{0,120}项目来源[：:]\s*(?P<funding>[^。\n]{1,40}))?"
        r"(?:。[^\n]{0,80}起止时间[：:]\s*(?P<span>[^。\n]{1,40}))?"
        r"(?:。[^\n]{0,80}担任角色[：:]\s*(?P<role>[^。\n]{1,40}))?",
        text,
    ):
        extra = {}
        if m.group("funding"):
            extra["项目来源"] = m.group("funding").strip()
        if m.group("span"):
            extra["起止时间"] = m.group("span").strip()
        if m.group("role"):
            extra["担任角色"] = m.group("role").strip()
        add(m.group("name").strip(), extra or None)
    for m in re.finditer(r"项目名称[：:\s|｜]*([^\n。|]{4,80})", text):
        add(m.group(1).strip())
    return acc[:12]


def identity_from_app_text(app_text: str) -> dict:
    """测试申报书等未入库文本：抽出姓名、企业、编号。"""
    text = str(app_text or "")

    def one(pat: str) -> str:
        m = re.search(pat, text)
        return (m.group(1) or "").strip() if m else ""

    return {
        "name": one(r"申报人姓名[：:\s]*([^\n]{2,40})") or one(r"有效证件姓名[：:\s]*([^\n]{2,40})"),
        "company": one(r"申报企业[：:\s]*([^\n]{2,80})") or one(r"引进企业[：:\s]*([^\n]{2,80})"),
        "attach_id": one(r"(?:申报书编号|人才编号)[：:\s]*(\d{4,8})"),
    }


_CN_PLACES = ("中国", "内地", "境内", "香港", "澳门", "台湾", "china", "prc", "hong kong", "macao", "taiwan")
_FUNDING_RE = re.compile(
    r"基金|能源部|国防部|科技部|自然科学|NSF|NIH|DOE|DoD|NASA|DARPA|Horizon|UKRI|NSFC|"
    r"欧盟|NSERC|Mitacs|ARC|EPSRC|国家重点研发|省科技厅",
    re.I,
)
_FUNDING_EN = (
    (re.compile(r"美国能源部|U\.?S\.?\s*Department of Energy|\bDOE\b", re.I), "U.S. Department of Energy"),
    (re.compile(r"美国国防部|Department of Defense|\bDoD\b", re.I), "U.S. Department of Defense"),
    (re.compile(r"国家自然科学基金|NSFC", re.I), "NSFC"),
)
_KNOWN_EN_ORG = (
    (re.compile(r"波音|boeing", re.I), "The Boeing Company"),
    (re.compile(r"微软|microsoft", re.I), "Microsoft"),
    (re.compile(r"\bgoogle\b|谷歌", re.I), "Google"),
    (re.compile(r"apple|苹果公司", re.I), "Apple"),
    (re.compile(r"amazon|亚马逊", re.I), "Amazon"),
    (re.compile(r"tesla|特斯拉", re.I), "Tesla"),
    (re.compile(r"\bibm\b", re.I), "IBM"),
    (re.compile(r"intel|英特尔", re.I), "Intel"),
)
_ORG_ALIAS = (
    (re.compile(r"波音|boeing", re.I), "boeing"),
)
_ZH_DOC_TITLES = (
    "项目证明", "开题报告", "结题报告", "项目验收单", "项目委任书",
    "项目绩效评估", "项目资金结算单", "全部项目总表", "项目任务书",
)
_EN_DOC_TITLES = (
    "Project Certificate", "Project Proposal", "Project Closure",
    "Acceptance Certificate", "Appointment Letter", "Performance Evaluation",
    "Settlement Statement", "All Projects",
)


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text or "")))


def _split_bilingual(text: str) -> tuple[str, str]:
    s = str(text or "").strip()
    if not s:
        return "", ""
    if "/" in s:
        a, b = s.split("/", 1)
        return a.strip(), b.strip()
    m = re.match(r"^(.+?)\s*[（(]([^）)]+)[）)]\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    if _has_cjk(s) and re.search(r"[A-Za-z]", s):
        m = re.match(r"^(.+?)([A-Za-z].+)$", s)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return s, ""


def _norm_org(text: str) -> str:
    t = str(text or "").lower()
    t = re.sub(r"(limited|ltd\.?|inc\.?|corp\.?|co\.|company|llc|科技|有限公司|股份|集团|公司)", "", t, flags=re.I)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", t)


def _org_key(text: str) -> str:
    raw = str(text or "")
    for pat, key in _ORG_ALIAS:
        if pat.search(raw) or pat.search(_norm_org(raw)):
            return key
    return _norm_org(raw)


def _same_org(a: str, b: str) -> bool:
    ka, kb = _org_key(a), _org_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    return (len(ka) >= 4 and ka in kb) or (len(kb) >= 4 and kb in ka)


def _is_cn_place(text: str) -> bool:
    s = str(text or "").strip().lower()
    return bool(s) and any(p in s for p in _CN_PLACES)


def _pretty_en_company(zh: str, en: str) -> str:
    blob = (zh or "") + " " + (en or "")
    for pat, name in _KNOWN_EN_ORG:
        if pat.search(blob):
            return name
    latin = en if (en and not _has_cjk(en) and re.search(r"[A-Za-z]{2}", en)) else ""
    if not latin and zh and not _has_cjk(zh) and re.search(r"[A-Za-z]{2}", zh):
        latin = zh
    if latin:
        if latin.isupper() and 2 <= len(latin) <= 32:
            return latin.title()
        return latin
    return (zh or en or "").strip()


def _pick_person_name(raw: str, language: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    zh, en = _split_bilingual(s)
    if language == "en":
        for part in (en, zh, s):
            lat = re.sub(r"[\u4e00-\u9fff•·].*", "", part or "").strip(" /")
            if re.search(r"[A-Za-z]{2}", lat) and not _has_cjk(lat):
                return lat
        return en or s
    for part in (zh, s):
        if _has_cjk(part):
            return re.split(r"\s*/\s*", part)[0].strip()
    return zh or s


def declaring_company_names(snap: dict, app_text: str = "") -> list:
    out, seen = [], set()
    keys_co = str(((snap or {}).get("keys") or {}).get("company") or "").strip()
    ident_co = str(identity_from_app_text(app_text).get("company") or "").strip()
    for name in (keys_co, ident_co):
        if not name:
            continue
        k = name.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(name)
    return out


def _is_forbidden_org(name: str, forbidden: list) -> bool:
    return any(_same_org(name, f) for f in (forbidden or []) if f)


def _project_for_lang(row: dict, language: str) -> dict:
    out = dict(row or {})
    zh, en = _split_bilingual(str(out.get("name") or ""))
    if language == "en":
        out["name"] = en or (zh if zh and not _has_cjk(zh) else (en or zh or out.get("name") or ""))
    else:
        out["name"] = zh or str(out.get("name") or "")
    out["name"] = str(out.get("name") or "").strip()
    return out


def prior_work_context(snap: dict, app_text: str = "") -> dict:
    """项目证明签发单位 = 回国/来华前工作单位，不用申报/引进企业。"""
    payload = ((snap or {}).get("talent") or {}).get("payload")
    payload = payload if isinstance(payload, dict) else {}
    info = payload.get("申报人基本信息") if isinstance(payload.get("申报人基本信息"), dict) else {}
    jobs = payload.get("工作经历") if isinstance(payload.get("工作经历"), list) else []
    forbidden = declaring_company_names(snap, app_text)
    empty = {
        "ok": False,
        "company": "",
        "company_zh": "",
        "company_en": "",
        "language": "",
        "role": "",
        "startDate": "",
        "endDate": "",
        "person": "",
        "projects": [],
        "forbidden": forbidden,
        "aliases": [],
        "note": "未找到来华前工作单位（且不可用申报企业签发），已跳过项目证明生成",
    }

    picked = None
    for row in jobs:
        if not isinstance(row, dict):
            continue
        flag = str(row.get("是否为回国前最后一段工作经历") or "").strip().lower()
        if flag in ("是", "y", "yes", "1", "true"):
            picked = row
            break
    if picked is None:
        for row in jobs:
            if not isinstance(row, dict):
                continue
            org = str(row.get("工作单位") or "").strip()
            if org and not _is_forbidden_org(org, forbidden):
                picked = row
                break

    zh, en, country, role_zh, role_en, start, end = "", "", "", "", "", "", ""
    if isinstance(picked, dict):
        zh, en = _split_bilingual(str(picked.get("工作单位") or ""))
        country = str(picked.get("所在国家") or "").strip()
        role_zh, role_en = _split_bilingual(str(picked.get("担任职务") or ""))
        start = str(picked.get("开始时间") or "").strip()
        end = str(picked.get("结束时间") or "").strip()
    if not zh:
        zh = str(info.get("回国前单位中文") or "").strip()
    if not en:
        en = str(info.get("回国前单位英文") or "").strip()
    if not zh and not _is_forbidden_org(str(info.get("现工作单位") or ""), forbidden):
        zh = str(info.get("现工作单位") or "").strip()
    if not country:
        country = str(info.get("回国前所在地") or info.get("国籍地区") or "").strip()
    if not role_zh:
        role_zh = str(info.get("回国前职务中文") or "").strip()
    if not role_en:
        role_en = str(info.get("回国前职务英文") or "").strip()
    if end in ("至今", "现在", "今", "Present", "Now"):
        end = "present"
    if not zh and not en:
        return empty
    if _is_forbidden_org(zh, forbidden) or _is_forbidden_org(en, forbidden):
        empty = dict(empty)
        empty["note"] = "来华前工作单位与申报企业相同，已跳过项目证明生成（不可用申报企业签发）"
        return empty

    overseas = (bool(country) and not _is_cn_place(country)) or (not country and bool(en) and not _has_cjk(en))
    language = "en" if overseas else "zh"
    company = _pretty_en_company(zh, en) if language == "en" else (zh or en)
    person = _pick_person_name(person_name(snap) or identity_from_app_text(app_text).get("name") or "", language)
    role = (role_en if language == "en" else role_zh) or (role_zh or role_en)
    projects = []
    for row in extract_projects(snap, ""):
        src = str(row.get("项目来源") or row.get("funding") or "")
        if src and _is_forbidden_org(src, forbidden):
            continue
        if src and not (_same_org(src, zh) or _same_org(src, en) or _same_org(src, company) or _FUNDING_RE.search(src)):
            continue
        item = _project_for_lang(row, language)
        src = str(row.get("项目来源") or "")
        if language == "en":
            if src and (_same_org(src, zh) or _same_org(src, en) or _same_org(src, company)):
                item["项目来源"] = company
            else:
                for pat, en_name in _FUNDING_EN:
                    if pat.search(src):
                        item["项目来源"] = en_name
                        break
        projects.append(item)
    aliases = [x for x in (zh, en) if x and x != company]
    return {
        "ok": True,
        "company": company,
        "company_zh": zh,
        "company_en": en,
        "language": language,
        "role": role,
        "startDate": start,
        "endDate": end or "present",
        "person": person,
        "projects": projects[:12],
        "forbidden": forbidden,
        "aliases": aliases,
        "note": "签发单位=" + company + "（来华前工作单位，非申报企业）；语言=" + language,
    }


def unify_html_language(
    html: str,
    language: str,
    issuer: str = "",
    forbidden_names: list | None = None,
    aliases: list | None = None,
) -> str:
    """生成件只保留一种语言：海外英文、境内中文；申报企业名替换为签发单位。"""
    s = str(html or "")
    if not s:
        return s
    issuer = str(issuer or "").strip()
    for name in list(forbidden_names or []) + list(aliases or []):
        n = str(name or "").strip()
        if not n or (issuer and n == issuer):
            continue
        s = s.replace(n, issuer) if issuer else s.replace(n, "")
    if language == "en":
        s = re.sub(r'(<html[^>]*\slang=")[^"]*"', r'\1en"', s, count=1, flags=re.I)
        for w in _ZH_DOC_TITLES:
            s = re.sub(r"<div([^>]*)>\s*" + re.escape(w) + r"\s*</div>", "", s, flags=re.I)
            s = s.replace(" / " + w, "").replace(w + " / ", "").replace("/ " + w, "")
        s = re.sub(
            r"<div([^>]*)>\s*[\u4e00-\u9fff]{2,16}\s*</div>",
            "",
            s,
        )
    elif language == "zh":
        s = re.sub(r'(<html[^>]*\slang=")[^"]*"', r'\1zh-CN"', s, count=1, flags=re.I)
        for w in _EN_DOC_TITLES:
            s = re.sub(r"<div([^>]*)>\s*" + re.escape(w) + r"\s*</div>", "", s, flags=re.I)
            s = s.replace(" / " + w, "").replace(w + " / ", "")
    return s


def _walk_projects(obj, add, depth=0):
    if depth > 8 or obj is None:
        return
    if isinstance(obj, list):
        for x in obj[:80]:
            _walk_projects(x, add, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    name = ""
    for k in NAME_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    if name:
        extra = {}
        for k in EXTRA_KEYS:
            v = obj.get(k)
            if isinstance(v, (str, int, float)) and str(v).strip() not in ("", "***"):
                extra[k] = v
        used_title = False
        for k in NAME_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.strip() == name:
                used_title = k == "title"
                break
        if used_title and not extra:
            name = ""
        if name:
            add(name, extra)
    for v in obj.values():
        if isinstance(v, (dict, list)):
            _walk_projects(v, add, depth + 1)


def _as_int(val, fallback: int) -> int:
    try:
        n = int(float(val))
        return n if n > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _npm_codebuddy_bin() -> Path:
    return Path(os.environ.get("APPDATA") or "") / "npm" / "node_modules" / "@tencent-ai" / "codebuddy-code" / "bin" / "codebuddy"


def _npm_codebuddy_cmd() -> Path:
    return Path(os.environ.get("APPDATA") or "") / "npm" / "codebuddy.cmd"


def resolve_codebuddy_cmd(raw: str) -> str:
    s = str(raw or "").strip()
    if s and FILL_MARK not in s and Path(s).exists():
        return s
    found = shutil.which("codebuddy.cmd") or shutil.which("codebuddy")
    if found:
        return found
    guess = _npm_codebuddy_cmd()
    if guess.exists():
        return str(guess)
    return s or "codebuddy.cmd"


def codebuddy_argv(raw: str) -> tuple[list, str]:
    """本机 CodeBuddy CLI：优先 node + npm 包入口，避免 .cmd 垫片丢参数。"""
    node = shutil.which("node") or "node"
    bin_js = _npm_codebuddy_bin()
    s = str(raw or "").strip()
    generic = (not s) or FILL_MARK in s or s.lower() in ("codebuddy", "codebuddy.cmd", "cbc")
    if not generic:
        p = Path(s)
        if p.exists():
            if p.name.lower() in ("codebuddy.cmd", "codebuddy") and bin_js.exists():
                return [node, str(bin_js)], str(bin_js)
            if p.suffix.lower() == ".cmd":
                return ["cmd.exe", "/c", str(p)], str(p)
            return [str(p)], str(p)
    if bin_js.exists():
        return [node, str(bin_js)], str(bin_js)
    cmd = resolve_codebuddy_cmd(s)
    if not shutil.which(cmd) and not Path(cmd).exists():
        return [], cmd
    if str(cmd).lower().endswith(".cmd"):
        return ["cmd.exe", "/c", cmd], cmd
    return [cmd], cmd


def build_search_prompt(person: str, attach_id: str, projects: list) -> str:
    lines = [
        "任务：为人才申报补「项目证明」附件。只找能证明「该人主持/参与该资助项目」的官方立项材料，不是论文、不是代码。",
        "必须使用联网搜索。只返回真实公开来源。禁止编造链接、文件名或项目编号。找不到就 found=false。",
        "",
        "申报人：" + (person or "未知"),
        "人才编号：" + (attach_id or "未知"),
        "项目列表：",
    ]
    if projects:
        for i, p in enumerate(projects, 1):
            bits = [str(p.get("name") or "")]
            for k in EXTRA_KEYS:
                if p.get(k) not in (None, ""):
                    bits.append(str(k) + "=" + str(p.get(k)))
            lines.append(str(i) + ". " + "；".join(bits))
    else:
        lines.append("（无结构化项目名。按申报人姓名检索其作为 PI/课题负责人的官方资助记录，不要用论文题目冒充项目。）")
    lines += [
        "",
        "检索优先级：",
        "1. 申报人姓名（含中英文别名）+ 项目编号/资助号 + award/grant/立项/批复/资助公示",
        "2. 申报人姓名 + 项目名称 + 项目来源机构官网",
        "3. 到资助机构官方库检索：NSF Award Search、NSFC/科学基金共享服务网、科技部或省科技厅公示、UKRI Gateway to Research、NIH RePORTER、CORDIS/Horizon、NSERC/SSHRC、ARC",
        "有项目编号时必须带编号搜；有项目来源时必须到对应机构官网搜。不要只搜论文标题。",
        "",
        "可接受（须能看出申报人角色或官方项目名/编号）：",
        "- 立项批文、任务书/合同、结题/验收证书、award notice、grant page、资助公示名单 PDF",
        "- 资助机构官网的 award/grant 详情页（含 PI、项目编号、起止年、金额）",
        "- 政府/基金会官网可下载的批复扫描件",
        "",
        "禁止返回（即使与项目高度相关也算未找到）：",
        "- 论文、预印本、会议/期刊页面：arXiv、IEEE Xplore、CVF/CVPR、ACM DL、Springer、ScienceDirect、OpenReview、PubMed",
        "- 代码仓库：GitHub / GitLab / Gitee / Hugging Face",
        "- 个人主页、实验室介绍、LinkedIn、Wikipedia、Google Scholar、ResearchGate、新闻稿、博客",
        "- 搜索引擎结果页。仅论文题目相似、没有资助编号或官方立项页，一律不算项目证明",
        "",
        "判定：每条必须证明「该申报人 + 该资助项目」。note 写明：机构、项目编号（若有）、申报人角色（PI/Co-PI/课题负责人）、为何不是论文。",
        "最多 8 条。优先可下载 PDF。url 必须是 http(s) 直链或官方页面；若已下载到当前工作目录，filename 写文件名。",
        "",
        "最终只输出一个 JSON 对象，不要 Markdown 围栏，不要解释：",
        '{"found":true,"items":[{"title":"","url":"https://...","filename":"","note":""}]}',
        "全部都是论文/代码或找不到则输出：",
        '{"found":false,"items":[]}',
    ]
    return "\n".join(lines)


def _codebuddy_result_text(data) -> str:
    """从 codebuddy --output-format json 的事件数组里抽出最终 result / 助手正文。"""
    if isinstance(data, dict):
        if data.get("type") == "result" and isinstance(data.get("result"), str):
            return data.get("result") or ""
        if isinstance(data.get("result"), str) and data.get("result").strip():
            return data.get("result")
        return ""
    if not isinstance(data, list):
        return ""
    texts = []
    for ev in data:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result" and isinstance(ev.get("result"), str):
            return ev.get("result") or ""
        if ev.get("type") == "message" and ev.get("role") == "assistant":
            for part in ev.get("content") or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    t = str(part.get("text") or "").strip()
                    if t:
                        texts.append(t)
    return texts[-1] if texts else ""


def _items_from_obj(data) -> list:
    items = []
    if isinstance(data, dict):
        rows = data.get("items") or data.get("files") or data.get("results") or []
        if isinstance(rows, list):
            items.extend(x for x in rows if isinstance(x, dict))
        elif data.get("url"):
            items.append(data)
    elif isinstance(data, list):
        items.extend(x for x in data if isinstance(x, dict) and (x.get("url") or x.get("filename")))
    return items


def _parse_items(text: str) -> list:
    s = str(text or "").strip()
    if not s:
        return []
    s = re.sub(r"^```(?:json)?", "", s, flags=re.I).replace("```", "").strip()
    items = []
    inner = ""
    try:
        data = json.loads(s)
    except Exception:
        data = None
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            try:
                data = json.loads(s[a : b + 1])
            except Exception:
                data = None
    if data is not None:
        inner = _codebuddy_result_text(data)
        if inner and inner.strip() != s:
            nested = _parse_items(inner)
            if nested:
                return nested
        items = _items_from_obj(data)
        if not items and inner:
            items = _items_from_obj(_try_json(inner))
    if not items:
        seen = set()
        for m in URL_RE.finditer(inner or s):
            u = m.group(0).rstrip(").,;]")
            if u in seen:
                continue
            seen.add(u)
            items.append({"url": u, "title": "", "filename": "", "note": "从检索输出抽取链接"})
    return items


def _try_json(text: str):
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t, flags=re.I).replace("```", "").strip()
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        t = t[a : b + 1]
    try:
        return json.loads(t)
    except Exception:
        return None


def _host_blocked(host: str) -> bool:
    h = (host or "").lower()
    if h.startswith("www."):
        h = h[4:]
    for blocked in SKIP_HOST:
        b = blocked.lower().rstrip(".")
        if not b:
            continue
        if h == b or h.endswith("." + b) or h.startswith(b + "."):
            return True
    return False


def _looks_like_paper(url: str, title: str = "", filename: str = "") -> bool:
    blob = " ".join(x for x in (url, title, filename) if x)
    if PAPER_TITLE_RE.search(blob):
        path = (urlparse(url).path or "").lower() if url else ""
        if any(x in path for x in ("/pdf/", "/abs/", "/html/", "/document/", "/doi/")):
            return True
        if PAPER_TITLE_RE.search(title or "") or PAPER_TITLE_RE.search(filename or ""):
            return True
        host = (urlparse(url).hostname or "").lower() if url else ""
        if any(x in host for x in ("arxiv", "thecvf", "ieee", "acm.org", "openreview", "github")):
            return True
    return False


def _usable_url(url: str, title: str = "", filename: str = "") -> str:
    u = str(url or "").strip()
    if not u or FILL_MARK in u:
        return ""
    if u.startswith("file:"):
        if _looks_like_paper(u, title, filename):
            return ""
        return u
    if re.match(r"https?://", u, re.I):
        host = (urlparse(u).hostname or "").lower()
        if _host_blocked(host):
            return ""
        if _looks_like_paper(u, title, filename):
            return ""
        return u
    return ""


def _collect_local_files(cwd: Path) -> list:
    out = []
    if not cwd.exists():
        return out
    for p in cwd.iterdir():
        if not p.is_file() or p.name in ("prompt.txt", "stdout.txt", "stderr.txt"):
            continue
        if p.suffix.lower() in FILE_EXT:
            out.append({
                "url": str(p.resolve()),
                "filename": p.name,
                "title": p.stem,
                "note": "CodeBuddy 工作目录文件",
            })
    return out


def run_codebuddy_search(*, person: str, attach_id: str, projects: list, work_dir: str | Path) -> dict:
    cfg = (load_config().get("projectProof") or {}).get("codebuddy") or {}
    timeout = _as_int(cfg.get("timeoutSec"), 180)
    max_turns = _as_int(cfg.get("maxTurns"), 10)
    prefix, display = codebuddy_argv(cfg.get("cmd") or "")
    model = str(cfg.get("model") or "").strip()
    if model and FILL_MARK in model:
        model = ""
    cwd = Path(work_dir)
    cwd.mkdir(parents=True, exist_ok=True)
    prompt = build_search_prompt(person, attach_id, projects)
    (cwd / "prompt.txt").write_text(prompt, encoding="utf-8")
    if not prefix:
        return {"ok": False, "error": "未找到本机 CodeBuddy CLI（" + (display or "codebuddy") + "）", "items": [], "raw": ""}
    user_prompt = (
        "请读取当前工作目录中的 prompt.txt（UTF-8），按其中要求联网检索项目证明。"
        "只返回立项批文/资助公示/官方 award·grant 页面。"
        "论文、预印本、GitHub、会议期刊页面一律不算；找不到则 found=false。"
        "只输出 JSON，禁止编造链接。"
    )
    args = prefix + [
        "-p", user_prompt,
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--dangerously-skip-permissions",
        "--max-turns", str(max_turns),
        "--add-dir", str(cwd.resolve()),
    ]
    if model:
        args += ["--model", model]
    (cwd / "cmd.txt").write_text("\n".join(args), encoding="utf-8")
    kwargs = {
        "cwd": str(cwd),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": timeout,
        "env": dict(os.environ),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(args, **kwargs)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "CodeBuddy 联网检索超时（" + str(timeout) + "s）", "items": [], "raw": ""}
    except OSError as e:
        return {"ok": False, "error": "无法启动 CodeBuddy：" + str(e)[:160], "items": [], "raw": ""}
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    (cwd / "stdout.txt").write_text(out, encoding="utf-8")
    if err.strip():
        (cwd / "stderr.txt").write_text(err[:8000], encoding="utf-8")
    items = _parse_items(out)
    items.extend(_collect_local_files(cwd))
    cleaned, seen = [], set()
    for it in items:
        title = str(it.get("title") or "")
        fn = str(it.get("filename") or "").strip()
        url = _usable_url(it.get("url") or "", title, fn)
        if not url and fn:
            local = cwd / fn
            if local.exists() and not _looks_like_paper(str(local), title, fn):
                url = str(local.resolve())
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "url": url,
            "filename": fn or Path(urlparse(url).path).name or "project-proof",
            "title": str(it.get("title") or fn or url)[:200],
            "note": str(it.get("note") or "CodeBuddy 联网检索"),
        })
    if proc.returncode not in (0, None) and not cleaned:
        msg = (err or out or "exit " + str(proc.returncode))[:200]
        return {"ok": False, "error": "CodeBuddy 退出码 " + str(proc.returncode) + "：" + msg, "items": [], "raw": out[:4000]}
    return {"ok": True, "error": "" if cleaned else "联网检索未找到可下载项目证明", "items": cleaned, "raw": out[:4000]}


def _render_value(val, ctx: dict):
    if isinstance(val, str):
        token_hit = re.fullmatch(r"\{\{(\w+)\}\}", val.strip())
        if token_hit:
            return ctx.get(token_hit.group(1), val)
        out = val
        for k, v in ctx.items():
            tok = "{{" + k + "}}"
            if tok not in out:
                continue
            if isinstance(v, (dict, list)):
                out = out.replace(tok, json.dumps(v, ensure_ascii=False))
            else:
                out = out.replace(tok, "" if v is None else str(v))
        return out
    if isinstance(val, dict):
        return {k: _render_value(v, ctx) for k, v in val.items()}
    if isinstance(val, list):
        return [_render_value(x, ctx) for x in val]
    return val


def _is_autoref_generate(cfg: dict, path: str) -> bool:
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider == "autoref":
        return True
    p = str(path or "").lower()
    return "/api/external/documents" in p or "/api/external/generate" in p or "/api/external/projects" in p


def find_resume_pdf(task_dir: str | Path | None) -> Path | None:
    """任务目录里的申报书 PDF，供 /api/external/generate 当 resume 上传。"""
    if not task_dir:
        return None
    root = Path(task_dir)
    found = []
    for folder in (root / "input", root / "work" / "input"):
        if not folder.is_dir():
            continue
        for p in folder.glob("*.pdf"):
            if p.is_file() and p.stat().st_size > 80:
                found.append(p)
    if not found:
        return None
    return sorted(found, key=lambda p: p.stat().st_size, reverse=True)[0]


def _build_autoref_letters(
    person: str,
    company: str,
    projects: list,
    attach_id: str,
    *,
    role: str = "",
    start_date: str = "",
    end_date: str = "",
    language: str = "",
) -> list:
    custom = []
    for p in projects or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        row = {"name": name}
        mapping = (
            ("startDate", "startDate"), ("endDate", "endDate"), ("开始时间", "startDate"), ("结束时间", "endDate"),
            ("amount", "amount"), ("经费总额", "amount"), ("funding", "funding"), ("项目来源", "funding"),
            ("role", "role"), ("担任角色", "role"), ("participants", "participants"), ("完成人排序", "participants"),
            ("description", "description"),
        )
        for src, dst in mapping:
            val = p.get(src)
            if val in (None, "", "***"):
                continue
            text = str(val).strip()
            if dst in ("endDate", "startDate") and text in ("至今", "现在", "今"):
                text = "present"
            if dst == "funding" and src == "项目来源" and not text.startswith("("):
                text = "(" + text + ")"
            row[dst] = text[:240]
        custom.append(row)
    default_role = "Researcher" if language == "en" else ("研究人员" if language == "zh" else "Researcher")
    letter = {
        "id": "work-" + re.sub(r"\W+", "", attach_id or "1")[:24] or "1",
        "type": "work",
        "companyName": (company or "Unknown Organization").strip() or "Unknown Organization",
        "candidateName": (person or "Unknown").strip() or "Unknown",
        "role": (role or default_role).strip() or default_role,
        "startDate": (start_date or (custom[0].get("startDate") if custom else "") or "2020-01"),
        "endDate": (end_date or (custom[0].get("endDate") if custom else "") or "present"),
    }
    if language:
        letter["language"] = language
    if custom:
        letter["customProjects"] = custom
    return [letter]


def _build_autoref_body(person: str, company: str, projects: list, attach_id: str, **kwargs) -> dict:
    return {"letters": _build_autoref_letters(person, company, projects, attach_id, **kwargs)}


def _doc_html(doc: dict, language: str = "") -> str:
    html = str(doc.get("html") or "").strip()
    if html:
        return html
    fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
    content = str(fields.get("content") or "").strip()
    if not content:
        return ""
    if language == "en":
        title = str(doc.get("titleEn") or doc.get("titleZh") or doc.get("kind") or "Project Certificate")
        lang = "en"
    else:
        title = str(doc.get("titleZh") or doc.get("titleEn") or doc.get("kind") or "项目证明")
        lang = "zh-CN"
    esc = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!DOCTYPE html><html lang=\"" + lang + "\"><head><meta charset=\"utf-8\"/><title>"
        + title.replace("<", "")
        + "</title></head><body style=\"font-family:serif;line-height:1.6;padding:24px\">"
        + "<h1>" + title.replace("<", "") + "</h1><pre style=\"white-space:pre-wrap\">"
        + esc + "</pre></body></html>"
    )


def _save_word_exports(data: dict, cwd: Path) -> list:
    out = []
    exports = data.get("exports") if isinstance(data.get("exports"), dict) else {}
    rows = exports.get("word") if isinstance(exports.get("word"), list) else []
    for i, it in enumerate(rows):
        if not isinstance(it, dict):
            continue
        b64 = str(it.get("base64") or it.get("data") or "").strip()
        if not b64:
            continue
        import base64
        fn = str(it.get("name") or it.get("filename") or ("project-proof-" + str(i + 1) + ".docx"))
        dest = cwd / Path(fn).name
        try:
            dest.write_bytes(base64.b64decode(b64))
        except Exception:
            continue
        out.append({
            "url": str(dest.resolve()),
            "filename": dest.name,
            "title": str(it.get("title") or dest.stem),
            "note": "AutoRef word",
        })
    return out


def _save_autoref_documents(
    data: dict,
    cwd: Path,
    *,
    language: str = "",
    issuer: str = "",
    forbidden_names: list | None = None,
    aliases: list | None = None,
) -> tuple[list, str]:
    if not isinstance(data, dict):
        return [], "AutoRef 响应不是 JSON 对象"
    if not data.get("ok"):
        return [], str(data.get("error") or "AutoRef 返回失败")
    by_project: dict[str, list] = {}
    summary = None
    for doc in data.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        kind = str(doc.get("kind") or "")
        if not kind.startswith("project."):
            continue
        html = unify_html_language(_doc_html(doc, language), language, issuer, forbidden_names, aliases)
        if not html:
            continue
        fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
        if language == "en":
            title = str(doc.get("titleEn") or doc.get("titleZh") or kind)
        else:
            title = str(doc.get("titleZh") or doc.get("titleEn") or kind)
        pname = str(fields.get("projectName") or doc.get("projectName") or title or "").strip()
        if kind == "project.all":
            summary = doc
            continue
        if _JUNK_PROJECT_NAME.match(pname) or pname in EXTRA_KEYS:
            continue
        key = pname.lower() or kind
        by_project.setdefault(key, []).append(doc)
    picked = []
    if summary:
        picked.append(summary)
    for rows in by_project.values():
        chosen = None
        for want in _AUTREF_KIND_PREF:
            chosen = next((d for d in rows if str(d.get("kind") or "") == want), None)
            if chosen:
                break
        picked.append(chosen or rows[0])
    saved, used = [], set()
    for doc in picked:
        kind = str(doc.get("kind") or "")
        html = unify_html_language(_doc_html(doc, language), language, issuer, forbidden_names, aliases)
        if not html:
            continue
        fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
        if language == "en":
            title = str(doc.get("titleEn") or doc.get("titleZh") or kind)
        else:
            title = str(doc.get("titleZh") or doc.get("titleEn") or kind)
        pname = str(fields.get("projectName") or doc.get("projectName") or "")
        safe = re.sub(r'[<>:"/\\|?*\s]+', "_", (pname or title or kind)).strip("_")[:72]
        fn = kind.replace(".", "-") + (("-" + safe) if safe else "") + ".html"
        if fn in used:
            fn = kind.replace(".", "-") + "-" + str(len(used) + 1) + ".html"
        used.add(fn)
        dest = cwd / fn
        dest.write_text(html, encoding="utf-8")
        saved.append({
            "url": str(dest.resolve()),
            "filename": dest.name,
            "title": (pname or title)[:200],
            "note": "AutoRef " + kind,
        })
    saved.extend(_save_word_exports(data, cwd))
    if not saved:
        return [], "AutoRef 未返回项目证明文档（documents 中无 project.* HTML）"
    return saved, ""


def generate_headers(cfg: dict, rendered: dict | None = None) -> dict:
    raw = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
    if rendered is None:
        rendered = _render_value(raw, {"apiKey": cfg.get("apiKey") or ""})
    src = rendered if isinstance(rendered, dict) else raw
    out = {}
    for k, v in (src or {}).items():
        ks, vs = str(k), str(v or "").strip()
        if not ks or not vs or FILL_MARK in vs or "{{" in vs:
            continue
        out[ks] = vs
    return out


async def _autoref_post(url: str, headers: dict, payload, timeout: int) -> tuple[dict | None, str, str]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0), trust_env=httpx_trust_env(), follow_redirects=True) as client:
            r = await client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as e:
        return None, "生成 API 网络错误：" + str(e)[:160], ""
    text = r.text or ""
    if r.status_code >= 400:
        return None, "生成 API HTTP " + str(r.status_code) + "：" + text[:200], text
    try:
        data = json.loads(text or "{}")
    except Exception:
        return None, "AutoRef 响应不是合法 JSON", text
    if not isinstance(data, dict):
        return None, "AutoRef 响应不是 JSON 对象", text
    if not data.get("ok"):
        return None, str(data.get("error") or "AutoRef 返回失败"), text
    return data, "", text


def _resume_payload(pdf: Path) -> dict:
    import base64
    raw = pdf.read_bytes()
    return {
        "resume": {
            "name": pdf.name or "resume.pdf",
            "mimeType": "application/pdf",
            "data": base64.b64encode(raw).decode("ascii"),
        },
        "include": {"documents": True, "word": False, "txt": False},
    }


async def call_generate_api(
    *,
    person: str,
    attach_id: str,
    company: str,
    projects: list,
    work_dir: str | Path,
    resume_pdf: str | Path | None = None,
    language: str = "",
    role: str = "",
    start_date: str = "",
    end_date: str = "",
    forbidden_names: list | None = None,
    aliases: list | None = None,
) -> dict:
    cfg = (load_config().get("projectProof") or {}).get("generate") or {}
    if not cfg.get("configured"):
        return {"ok": False, "error": "项目证明生成 API 未配置（请填写 config.json 的 projectProof.generate）", "items": []}
    timeout = _as_int(cfg.get("timeoutSec"), 300)
    method = str(cfg.get("method") or "POST").upper()
    if method not in ("GET", "POST", "PUT"):
        method = "POST"
    ctx = {
        "apiKey": cfg.get("apiKey") or "",
        "attach_id": attach_id or "",
        "name": person or "",
        "company": company or "",
        "projects": projects,
        "projects_json": json.dumps(projects, ensure_ascii=False),
        "projects_text": "\n".join(str(p.get("name") or "") for p in projects),
    }
    headers = generate_headers(cfg, _render_value(cfg.get("headers") or {}, ctx))
    path = str(_render_value(cfg.get("path") or "", ctx) or "")
    base = cfg.get("baseUrl") or ""
    url = P.abs_url(base, path)
    if not url:
        return {"ok": False, "error": "生成 API 地址为空", "items": []}
    autoref = _is_autoref_generate(cfg, path)
    cwd = Path(work_dir)
    cwd.mkdir(parents=True, exist_ok=True)
    pdf = Path(resume_pdf) if resume_pdf else None
    if pdf and (not pdf.is_file() or pdf.suffix.lower() != ".pdf"):
        pdf = None

    if autoref:
        letters = _build_autoref_letters(
            person, company, projects, attach_id,
            role=role, start_date=start_date, end_date=end_date, language=language,
        )
        data, err, raw_text = None, "", ""
        used = ""
        if projects:
            used = "documents"
            data, err, raw_text = await _autoref_post(P.abs_url(base, cfg.get("path") or "/api/external/documents"), headers, {"letters": letters}, timeout)
        elif pdf:
            used = "generate"
            data, err, raw_text = await _autoref_post(P.abs_url(base, cfg.get("generatePath") or "/api/external/generate"), headers, _resume_payload(pdf), timeout)
        else:
            used = "projects"
            data, err, raw_text = await _autoref_post(P.abs_url(base, cfg.get("projectsPath") or "/api/external/projects"), headers, {"letters": letters}, timeout)
            if data and not (data.get("documents") or []):
                more_letters = data.get("letters") if isinstance(data.get("letters"), list) and data.get("letters") else letters
                data2, err2, raw2 = await _autoref_post(P.abs_url(base, cfg.get("path") or "/api/external/documents"), headers, {"letters": more_letters}, timeout)
                if data2:
                    data, err, raw_text = data2, err2, raw2
                    used = "projects+documents"
                elif err2:
                    err = err2
                    raw_text = raw2
        if raw_text:
            (cwd / "generate.json").write_text(raw_text[:200000], encoding="utf-8")
        if not data:
            return {"ok": False, "error": err or ("AutoRef " + used + " 未返回数据"), "items": []}
        saved, save_err = _save_autoref_documents(
            data, cwd,
            language=language, issuer=company,
            forbidden_names=forbidden_names, aliases=aliases,
        )
        if not saved:
            return {"ok": False, "error": save_err or "AutoRef 未产出项目证明", "items": []}
        return {"ok": True, "error": "", "items": saved}

    body = cfg.get("body")
    payload = _render_value(body, ctx) if body not in (None, "") else None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0), trust_env=httpx_trust_env(), follow_redirects=True) as client:
            if method == "GET":
                r = await client.get(url, headers=headers, params=payload if isinstance(payload, dict) else None)
            else:
                r = await client.request(method, url, headers=headers, json=payload if not isinstance(payload, str) else None, content=payload if isinstance(payload, str) else None)
    except httpx.HTTPError as e:
        return {"ok": False, "error": "生成 API 网络错误：" + str(e)[:160], "items": []}
    ctype = (r.headers.get("content-type") or "").lower()
    if r.status_code >= 400:
        return {"ok": False, "error": "生成 API HTTP " + str(r.status_code) + "：" + (r.text or "")[:200], "items": []}
    if "pdf" in ctype or "octet-stream" in ctype or "word" in ctype or "zip" in ctype:
        fn = "project-proof.pdf"
        cd = r.headers.get("content-disposition") or ""
        m = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?", cd, re.I)
        if m:
            fn = m.group(1) or m.group(2) or fn
        dest = cwd / Path(fn).name
        dest.write_bytes(r.content)
        return {"ok": True, "error": "", "items": [{"url": str(dest.resolve()), "filename": dest.name, "title": dest.stem, "note": "生成 API 二进制"}]}
    text = r.text or ""
    (cwd / "generate.json").write_text(text[:200000], encoding="utf-8")
    items = _parse_generate_payload(text, cfg.get("baseUrl") or "")
    if not items:
        return {"ok": False, "error": "生成 API 已响应但未给出文件或下载地址", "items": []}
    saved = []
    for it in items:
        url_i = str(it.get("url") or "")
        b64 = str(it.get("content") or it.get("file_base64") or it.get("base64") or "")
        if b64 and not url_i.startswith("http"):
            import base64
            fn = str(it.get("filename") or "project-proof.pdf")
            dest = cwd / Path(fn).name
            try:
                dest.write_bytes(base64.b64decode(b64))
            except Exception:
                continue
            saved.append({"url": str(dest.resolve()), "filename": dest.name, "title": str(it.get("title") or dest.stem), "note": "生成 API"})
            continue
        if url_i:
            saved.append({
                "url": url_i,
                "filename": str(it.get("filename") or Path(urlparse(url_i).path).name or "project-proof"),
                "title": str(it.get("title") or it.get("filename") or "项目证明"),
                "note": str(it.get("note") or "生成 API"),
            })
    if not saved:
        return {"ok": False, "error": "生成 API 已响应但无法解析文件", "items": []}
    return {"ok": True, "error": "", "items": saved}


def _parse_generate_payload(text: str, base: str) -> list:
    try:
        data = json.loads(text or "{}")
    except Exception:
        return _parse_items(text)
    rows = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for k in ("items", "files", "results", "data", "attachments"):
            v = data.get(k)
            if isinstance(v, list):
                rows = v
                break
            if isinstance(v, dict) and (v.get("url") or v.get("filename")):
                rows = [v]
                break
        if not rows and (data.get("url") or data.get("filename") or data.get("content") or data.get("file_base64")):
            rows = [data]
    out = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or it.get("file_url") or it.get("download_url") or it.get("pdf_url") or "")
        if url.startswith("/") and base:
            url = P.abs_url(base, url)
        row = dict(it)
        if url:
            row["url"] = url
        out.append(row)
    return out
