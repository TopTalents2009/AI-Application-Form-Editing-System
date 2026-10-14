"""从群聊消息里抽出正文、附件名、人才编号等，供 Agent 使用。"""
from __future__ import annotations

import re
from pathlib import Path

TALENT_RE = re.compile(r"(?<!\d)(\d{5,6})(?!\d)")
DATE_RE = re.compile(
    r"(20\d{2}[-./年]\d{1,2}[-./月]\d{1,2}日?"
    r"|\d{1,2}月\d{1,2}日"
    r"|周[一二三四五六日天]"
    r"|今晚|明天|后天|今天|尽快|截止|之前)"
)
COMPANY_RE = re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{2,40}(?:有限公司|股份有限公司|实验室|科技园|高新区))")
ACK_RE = re.compile(
    r"^((收到|好的|好滴|谢谢|辛苦[啦了哈]?|嗯嗯?|ok|好)[。.!！~，,、\s]*)+$",
    re.I,
)
QUESTION_RE = re.compile(r"[？?]|(吗$)|(能否|能不能|可不可以|有没有|是否|预计|什么时候|啥时候)")
SYSTEM_RE = re.compile(r"^(该消息已被撤回|\[会议通知\]|\[群通知\]|\[名片\]|\[群系统通知\]|\[置顶消息\])$")

FILE_KINDS = [
    ("opinion", "修改意见", re.compile(r"质检反馈|修改意见|意见总结|反馈\.doc", re.I)),
    ("form", "申报书", re.compile(r"申报书|初稿|新稿|修改稿|二稿|一稿|\bV\d\b|\.zip|\.rar|\.7z", re.I)),
    ("company", "企业材料", re.compile(r"企业|基本情况|基本信息|需提供的材料", re.I)),
    ("passport", "护照", re.compile(r"护照|passport", re.I)),
    ("id_doc", "身份证明", re.compile(r"身份证|永居|身份证明", re.I)),
    ("work", "工作证明", re.compile(r"工作证明|在职证明|工作日期", re.I)),
    ("edu", "学历证明", re.compile(r"学历|学位证|毕业证|diploma", re.I)),
    ("report", "推荐报告", re.compile(r"推荐报告|人才信息表", re.I)),
    ("image", "截图", re.compile(r"截图|\.png|\.jpg|\.jpeg", re.I)),
]


def text_of(message: dict) -> str:
    return str(message.get("text") or message.get("snippet") or "").strip()


def filename_of(message: dict) -> str:
    name = str(message.get("attachment_name") or message.get("filename") or "").strip()
    if name:
        return name
    text = text_of(message)
    m = re.search(r"([\w\u4e00-\u9fff.+\-（）()]+\.(?:zip|rar|7z|docx?|xlsx?|pdf|png|jpe?g))", text, re.I)
    return m.group(1) if m else ""


def talent_ids(*parts: str) -> list[str]:
    found: list[str] = []
    for part in parts:
        for hit in TALENT_RE.findall(part or ""):
            if hit not in found:
                found.append(hit)
    return found[:8]


def file_kind(filename: str, text: str = "") -> tuple[str, str]:
    blob = f"{filename} {text}"
    for kid, label, pat in FILE_KINDS:
        if pat.search(blob):
            return kid, label
    if filename:
        ext = Path(filename).suffix.lower()
        if ext:
            return "other", "附件"
    return "", ""


def is_system(message: dict) -> bool:
    text = text_of(message)
    label = str(message.get("msg_type_label") or "")
    typ = str(message.get("msg_type") or "")
    if "会议" in label or "系统" in label or "群通知" in label:
        return True
    if typ in ("10000", "system"):
        return True
    if SYSTEM_RE.match(text):
        return True
    return False


def is_ack_only(text: str) -> bool:
    t = re.sub(r"[\s\[\]\"“”]+", "", text or "")
    return bool(ACK_RE.match(t))


def dates_in(text: str) -> list[str]:
    return [m.group(0) for m in DATE_RE.finditer(text or "")][:6]


def companies_in(text: str) -> list[str]:
    return [m.group(1) for m in COMPANY_RE.finditer(text or "")][:4]


def summary(text: str, filename: str = "", limit: int = 80) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if filename and filename not in t:
        t = (filename + " " + t).strip()
    if len(t) > limit:
        return t[:limit] + "…"
    return t


def is_delivery_name(name: str) -> bool:
    n = str(name or "")
    return "交付" in n
