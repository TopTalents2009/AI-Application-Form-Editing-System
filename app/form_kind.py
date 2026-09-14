# -*- coding: utf-8 -*-
"""对照项目根目录 QM.docx / HJ.docx 模板，判断已填申报书属于哪一类。"""
from __future__ import annotations
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
ROOT_DIR = Path(__file__).resolve().parent.parent
MODES = ("QM", "HJ")

# 模板独有的结构词（即使模板文件暂不可读也能分）
_STRONG = {
    "QM": (
        "申报企业",
        "填表须知",
        "申报人基本情况",
        "引进企业基本情况",
        "申报省市",
        "创新/青年人才",
        "是否首次申报本计划",
        "博士后不属于学历",
        "全日制学历",
    ),
    "HJ": (
        "Application Form",
        "实验室名称",
        "实验室类别",
        "个人信息部分",
        "Name of Applicant",
        "申报单位(用人单位)",
        "Name of Laboratory",
        "所属二级学科",
        "所属前沿领域",
        "申报人姓名",
        "国家火炬计划申报书",
        "关键申报信息",
        "专长及代表性成果",
        "工作设想",
    ),
}

_cache: dict = {"sig": None, "markers": None}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def _para_text(p) -> str:
    parts = []
    for node in p.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag in (W + "br", W + "cr"):
            parts.append("\n")
        elif node.tag == W + "tab":
            parts.append("\t")
    return "".join(parts)


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    seen, lines = set(), []
    for p in root.iter(W + "p"):
        t = _para_text(p).replace("\x00", "").strip()
        if not t:
            continue
        key = re.sub(r"\s+", "", t)
        if key in seen:
            continue
        seen.add(key)
        lines.append(t)
    return "\n".join(lines)


def _label_keys(text: str) -> set[str]:
    out = set()
    for ln in str(text or "").splitlines():
        n = _norm(ln)
        if not (4 <= len(n) <= 36):
            continue
        if "。" in n or "；" in n:
            continue
        if re.search(r"\d{4,}", n):
            continue
        out.add(n)
    return out


def _template_paths() -> dict[str, Path]:
    return {"QM": ROOT_DIR / "QM.docx", "HJ": ROOT_DIR / "HJ.docx"}


def _fingerprint() -> dict[str, list[str]]:
    paths = _template_paths()
    sig = tuple((str(p), p.stat().st_mtime_ns if p.is_file() else 0) for p in (paths["QM"], paths["HJ"]))
    if _cache.get("sig") == sig and _cache.get("markers"):
        return _cache["markers"]
    keys = {}
    for mode, p in paths.items():
        if p.is_file():
            try:
                keys[mode] = _label_keys(extract_docx_text(p))
            except Exception:
                keys[mode] = set()
        else:
            keys[mode] = set()
    markers = {}
    for mode in MODES:
        other = set()
        for m in MODES:
            if m != mode:
                other |= keys.get(m) or set()
        own = keys.get(mode) or set()
        uniq = [k for k in own if k not in other]
        uniq.sort(key=lambda s: (-len(re.findall(r"[\u4e00-\u9fff]", s)), len(s)))
        markers[mode] = uniq[:90]
    _cache["sig"] = sig
    _cache["markers"] = markers
    return markers


def _hits(blob: str, markers: list[str], strong: tuple[str, ...]) -> int:
    n = 0
    for m in markers:
        if m and m in blob:
            n += 1
    for s in strong:
        k = _norm(s)
        if k and k in blob:
            n += 3
    return n


def classify_name(fname: str) -> str:
    s = str(fname or "").upper()
    if re.search(r"(^|[_\-\s\(\[（])QM([_\-\s\)\]）]|$)", s):
        return "QM"
    if re.search(r"(^|[_\-\s\(\[（])HJ([_\-\s\)\]）]|$)", s):
        return "HJ"
    return ""


def classify(app_text: str, fname: str = "") -> str:
    """返回 'QM' / 'HJ'；无法判断时为空字符串。"""
    blob = _norm(app_text)
    if len(blob) < 80:
        return classify_name(fname)
    fp = _fingerprint()
    scores = {m: _hits(blob, fp.get(m) or [], _STRONG[m]) for m in MODES}
    qm, hj = scores["QM"], scores["HJ"]
    if qm == 0 and hj == 0:
        return classify_name(fname)
    if qm > hj and qm >= 3:
        return "QM"
    if hj > qm and hj >= 3:
        return "HJ"
    by_name = classify_name(fname)
    if by_name:
        return by_name
    if qm > hj:
        return "QM"
    if hj > qm:
        return "HJ"
    return ""
