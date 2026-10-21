"""Decode WeCom message content from protobuf/binary blobs."""
import re

MSG_TYPES = {
    0: "文本/混合",
    2: "文本",
    4: "图片",
    6: "位置",
    7: "语音",
    8: "文件",
    10: "邮件",
    13: "在线文档",
    14: "文件",
    15: "图片/文件",
    16: "文件",
    20: "富文本",
    21: "视频号/视频",
    22: "视频",
    23: "视频",
    29: "动画表情",
    31: "图文链接",
    34: "文件",
    35: "应用推荐",
    36: "聊天记录",
    38: "应用消息",
    40: "通话/音视频",
    78: "小程序",
    80: "Markdown",
    82: "使用小结",
    101: "系统消息",
    102: "系统消息",
    103: "图片链接",
    104: "动画表情",
    105: "一周小结",
    123: "图文混排",
    145: "应用卡片",
    503: "状态",
    515: "应用消息",
    561: "审批卡片",
    565: "微盘通知",
    570: "微盘文件",
    573: "打卡通知",
    579: "会议",
    587: "汇报提醒",
    659: "文档通知",
    999: "文件夹",
    1001: "群名片",
    1002: "名片",
    1003: "名片",
    1004: "系统消息",
    1005: "系统消息",
    1006: "邀请入群",
    1011: "会议通知",
    1022: "群系统通知",
    1023: "群系统通知",
    1029: "群系统通知",
    1043: "系统消息",
    1051: "群通知",
    1052: "置顶消息",
    1073: "会议",
}

_FILE_EXT = r"png|jpe?g|gif|bmp|webp|pdf|xlsx?|docx?|pptx?|zip|rar|7z|mp4|mov|txt|csv|md"
_FILE_EXT_RE = re.compile(
    rf"((?:[A-Za-z0-9_\u4e00-\u9fff+\-][\w\u4e00-\u9fff.\- ()（）【】+\-]{{0,160}})\.(?:{_FILE_EXT}))",
    re.IGNORECASE,
)
_FILE_TYPES = {8, 14, 15, 16, 20, 34}
_CONTACT_TYPES = {1001, 1002, 1003, 1006}
_TEXTISH_TYPES = {
    0, 2, 10, 13, 29, 31, 36, 78, 80, 82, 101, 102, 104, 105, 123, 145,
    503, 515, 561, 565, 570, 573, 579, 587, 659, 1001, 1022, 1052, 1011, 1073,
}
_URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#@!$&'()*+,;=%\-]+")
_USER_ID_LIST_RE = re.compile(r"^\d{10,}(?:;\d{10,})*$")
_UNKNOWN_PLACEHOLDER_RE = re.compile(r"^\[未知\((\d+)\)\]$")
_UNKNOWN_PREFIX_RE = re.compile(r"^\[未知\((\d+)\)\]\s*")
_IMAGE_HOSTS = (
    "wework.qpic.cn",
    "wx.qlogo.cn",
    "pic.weixin.qq.com",
    "mmbiz.qpic.cn",
    "p.qpic.cn",
)
_IMAGE_ICON_NOISE = (
    "node/wework/images/",
    "sheet_tsp",
    "icon_tsp",
    "@2x.png",
    "@3x.png",
)
_LABEL_ONLY_RE = re.compile(r"^\[(?:图片|图片/文件|文件|名片|群名片|邀请入群|动画表情|视频|位置)\]\s*$")


def _read_varint(data, pos):
    value = 0
    shift = 0
    while pos < len(data) and shift < 64:
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, pos
        shift += 7
    raise ValueError("bad varint")


def _clean_text(text):
    text = "".join(
        ch if ch in "\n\t" or (ch.isprintable() and ch not in "\x0b\x0c") else " "
        for ch in text
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_normal_char(ch):
    if ch in "\n\t ":
        return True
    if "\u4e00" <= ch <= "\u9fff":
        return True
    if ch in ".,!?;:()[]【】（）\"'、。！？；：""''…—-_%/@#&*+=<>\\/":
        return True
    if ch.isascii() and ch.isalnum():
        return True
    code = ord(ch)
    if 0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or code in (0xFE0F, 0x200D):
        return True
    return False


_SHORT_TEXT_RE = re.compile(
    r"^(?:"
    r"[\u4e00-\u9fff]{1,2}"
    r"|[？！。、，?]"
    r"|[Oo][Kk]|[Hh][Ii]|[Yy]es|[Nn]o"
    r"|[+\-]?\d{1,2}"
    r")$"
)


def _is_readable_short(text):
    """True for real chat replies that are only 1-2 characters."""
    value = (text or "").strip()
    if _SHORT_TEXT_RE.fullmatch(value):
        return True
    return bool(value) and all(_is_normal_char(ch) and not ch.isascii() for ch in value) and len(value) <= 4


def _is_garbage_text(text):
    if not text:
        return True
    text = text.strip()
    if not text:
        return True
    if len(text) < 3:
        return not _is_readable_short(text)

    if text.count("\ufffd") > 1:
        return True
    if text.startswith("㾢") or "혊" in text[:4] or "〲" in text[:30]:
        return True

    fw_digits = sum(1 for ch in text if "\uff10" <= ch <= "\uff19")
    if fw_digits >= 6 and fw_digits / len(text) > 0.2:
        return True

    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if "A" <= ch <= "Z" or "a" <= ch <= "z")
    hangul = sum(1 for ch in text if "\uac00" <= ch <= "\ud7af" or "\u1100" <= ch <= "\u11ff")
    weird = sum(
        1 for ch in text
        if "\u3200" <= ch <= "\u32ff"
        or "\u3130" <= ch <= "\u318f"
        or "\u0100" <= ch <= "\u024f"
        or ch in "ƾꨀ≦≧Ⴢ"
    )
    normal = sum(1 for ch in text if _is_normal_char(ch))

    digits = sum(1 for ch in text if ch.isdigit())
    if hangul >= 2 and hangul > cjk:
        return True
    if weird >= 2 and cjk < 8:
        return True
    if len(text) > 20 and normal / len(text) < 0.45:
        return True
    if len(text) > 30 and cjk + latin + digits < len(text) * 0.08:
        return True
    if len(text) > 40:
        alnum = sum(1 for ch in text if ch.isalnum())
        if (
            alnum / len(text) > 0.9
            and cjk < 3
            and " " not in text[:30]
            and not re.search(r"[、，。；：]|https?://", text)
        ):
            return True
    return False


def _text_quality(text):
    if not text or _is_garbage_text(text) or _is_noise_text(text):
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    digits = sum(1 for ch in text if ch.isdigit())
    punct = sum(1 for ch in text if ch in "\n，。！？、；：")
    weird = sum(1 for ch in text if not _is_normal_char(ch))
    score = cjk * 4 + latin + digits * 0.5 + punct * 2 + min(len(text), 200) * 0.1 - weird * 6
    if cjk >= 4:
        score += 30
    if _FILE_EXT_RE.search(text):
        score += 15
    return score


def _looks_like_plain_text(data, text):
    if not text or _is_garbage_text(text):
        return False
    control = sum(1 for b in data if b < 32 and b not in (9, 10, 13))
    if control / max(len(data), 1) > 0.08:
        return False
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\t")
    return printable / max(len(text), 1) > 0.9


def _decode_text_segment(segment):
    if not segment or b"\x00" in segment:
        return None
    try:
        text = segment.decode("utf-8")
    except UnicodeDecodeError:
        return None
    text = _clean_text(text)
    if not text:
        return None
    if _is_id_token(text) or re.fullmatch(r"[0-9a-fA-F]{32,}", text):
        return None
    if _is_garbage_text(text) or _is_noise_text(text):
        return None
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\t")
    if printable / max(len(text), 1) < 0.9:
        return None
    return text


def _parse_protobuf_strings(data, depth=0):
    if depth > 4 or not data:
        return []
    pos = 0
    out = []
    fields = 0
    try:
        while pos < len(data):
            tag, pos = _read_varint(data, pos)
            if tag == 0:
                return []
            wire = tag & 7
            fields += 1
            if wire == 0:
                _, pos = _read_varint(data, pos)
            elif wire == 1:
                pos += 8
            elif wire == 5:
                pos += 4
            elif wire == 2:
                length, pos = _read_varint(data, pos)
                if length < 0 or pos + length > len(data):
                    return []
                segment = data[pos:pos + length]
                pos += length
                nested = _parse_protobuf_strings(segment, depth + 1)
                if nested:
                    out.extend(nested)
                else:
                    text = _decode_text_segment(segment)
                    if text:
                        out.append(text)
            else:
                return []
            if pos > len(data):
                return []
    except Exception:
        return []
    return out if fields else []


def _is_id_token(text):
    t = (text or "").strip()
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", t):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,24}", t):
        return False
    has_upper = any("A" <= ch <= "Z" for ch in t)
    has_lower = any("a" <= ch <= "z" for ch in t)
    has_digit = any(ch.isdigit() for ch in t)
    if t.isdigit() or t.replace("-", "").isdigit():
        return False
    return (has_upper and has_lower) or (has_digit and (has_upper or has_lower))


_APP_VERSION_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){1,2}$")


def _is_app_version(text):
    return bool(_APP_VERSION_RE.fullmatch((text or "").strip()))


def _strip_app_version(text):
    if not text:
        return ""
    text = re.sub(r"(?:^|\n)\d{1,3}(?:\.\d{1,3}){1,2}(?=\n|$)", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _is_noise_text(text):
    if not text:
        return True
    if _is_app_version(text) or _is_id_token(text):
        return True
    if re.fullmatch(r"[A-Za-z0-9+/=]{16,}", text):
        return True
    if re.match(r"^[A-Za-z]:\\", text) or text.startswith("\\\\"):
        lower = text.lower().replace("/", "\\")
        if any(part in lower for part in ("\\appdata\\", "\\wxwork", "\\wedrive", "\\windows\\temp")):
            return True
        return False
    if _URL_RE.search(text):
        return False
    if text.startswith("/") and "/" in text[1:] and not re.search(r"[\u4e00-\u9fff]", text):
        return True
    return False


def _strip_proto_prefix(text):
    """Drop protobuf tag leftovers stuck to the front of readable text."""
    if not text:
        return ""
    prev = None
    while prev != text:
        prev = text
        text = text.strip()
        text = re.sub(r"^[\s/+,.*)]+(?=https?://)", "", text, flags=re.I)
        text = re.sub(r"^['\"`|!@$&*^~\\({]+", "", text)
        text = re.sub(r"^[A-Za-z](?: [A-Za-z])?\n[A-Za-z]", "", text)
        text = re.sub(r"^[A-Za-z] [A-Za-z]\n", "", text)
        text = re.sub(r"^[A-Za-z]\n(?=[\u4e00-\u9fff【])", "", text)
        text = re.sub(r"^[A-Za-z]#\s+", "", text)
        text = re.sub(r"^[0-9A-Za-z/#](?=【)", "", text)
        text = re.sub(r"^[0-9A-Za-z](?=[\u4e00-\u9fff])", "", text)
        text = re.sub(r"^[0-9A-Za-z] (?=[\u4e00-\u9fff【\d])", "", text)
        text = re.sub(r"^\[\s*[A-Za-z]\s*\n[A-Za-z]", "", text)
    return _strip_app_version(text.strip())


def _dedupe_texts(values):
    seen = set()
    out = []
    for value in values:
        value = _strip_proto_prefix(_clean_text(value))
        if not value or value in seen or _is_garbage_text(value) or _is_noise_text(value):
            continue
        seen.add(value)
        out.append(value)
    return out


def _to_bytes(raw):
    if raw is None:
        return b""
    if isinstance(raw, str):
        return raw.encode("utf-8", errors="ignore")
    return bytes(raw)


def _looks_like_filename(name):
    if not name or len(name) > 180:
        return False
    if "://" in name or "文件]" in name or "图片]" in name:
        return False
    if not _FILE_EXT_RE.search(name):
        return False
    stem = name.rsplit(".", 1)[0]
    if re.fullmatch(r"[0-9a-fA-F]{20,}", stem):
        return False
    return True


def normalize_attachment_name(name):
    name = str(name or "").replace("\\", "/").strip()
    name = name.split("/")[-1].strip()
    name = re.sub(r"^(?:\[(?:图片/文件|图片|文件)\]\s*)+", "", name)
    name = name.strip(" []")
    cjk = re.search(rf"[\u4e00-\u9fff][\w\u4e00-\u9fff.\- ()（）【】\-]{{0,120}}\.(?:{_FILE_EXT})$", name, re.I)
    if cjk and re.match(r"^[0-9a-fA-F]{8,}", name):
        name = cjk.group(0)
    return name if _looks_like_filename(name) else ""


def _extract_filename_hints(*blobs):
    hints = []
    seen = set()
    for raw in blobs:
        if not raw:
            continue
        data = _to_bytes(raw)
        text = data.decode("utf-8", errors="ignore")
        for match in _FILE_EXT_RE.finditer(text):
            name = normalize_attachment_name(match.group(1))
            if name and name not in seen:
                seen.add(name)
                hints.append(name)
        for match in re.finditer(rb"[\x20-\x7e]{3,200}", data):
            s = match.group().decode("ascii", errors="ignore").strip()
            if re.fullmatch(r"[0-9a-fA-F]{32,}", s):
                continue
            found = _FILE_EXT_RE.search(s.replace("\\", "/").split("/")[-1])
            if not found:
                continue
            name = normalize_attachment_name(found.group(1))
            if name and name not in seen:
                seen.add(name)
                hints.append(name)
    return hints


def extract_attachment_name(*blobs):
    names = _extract_filename_hints(*blobs)
    if not names:
        return ""

    def score(name):
        value = min(len(name), 80)
        if re.search(r"[\u4e00-\u9fff]", name):
            value += 40
        if re.search(rf"\.(?:zip|rar|7z|pdf|xlsx?|docx?|pptx?)$", name, re.I):
            value += 20
        if name.lower().startswith("http"):
            value -= 50
        return value

    return max(names, key=score)


def decode_content(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        recovered = _recover_naive_utf8_dump(raw)
        if recovered:
            return _strip_proto_prefix(recovered)
        text = _strip_proto_prefix(_clean_text(raw))
        return "" if _is_garbage_text(text) or _is_noise_text(text) else text
    data = bytes(raw)
    if not data:
        return ""

    try:
        plain = data.decode("utf-8")
        if _looks_like_plain_text(data, plain):
            return _clean_text(plain)
    except UnicodeDecodeError:
        pass

    texts = _protobuf_strings_loose(data)
    if texts:
        combined = _combine_text_parts(texts)
        if combined:
            return combined
        texts.sort(key=_text_quality, reverse=True)
        return texts[0]

    filenames = _extract_filename_hints(data)
    if filenames:
        return filenames[0]

    return ""


def _protobuf_strings_loose(data):
    texts = _dedupe_texts(_parse_protobuf_strings(data))
    if texts:
        return texts
    for start in range(1, min(32, len(data))):
        texts = _dedupe_texts(_parse_protobuf_strings(data[start:]))
        if texts:
            return texts
    return []


def _recover_naive_utf8_dump(text):
    """Recover text from a protobuf blob that was decoded with utf-8 errors=ignore."""
    if not text:
        return ""
    has_ctrl = any(ord(ch) < 32 and ch not in "\n\t" for ch in text)
    if not has_ctrl:
        return ""
    data = text.encode("utf-8", errors="ignore")
    texts = _protobuf_strings_loose(data)
    if texts:
        texts.sort(key=_text_quality, reverse=True)
        if _text_quality(texts[0]) > 0:
            return texts[0]
    cleaned = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else "" for ch in text)
    cleaned = re.sub(r"^[\s/+,.*)]+", "", cleaned)
    cleaned = re.sub(r"^[A-Za-z](?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = _clean_text(cleaned)
    if cleaned and not _is_garbage_text(cleaned) and _text_quality(cleaned) > 0:
        return cleaned
    return ""


def normalize_image_url(url):
    if not url:
        return ""
    url = str(url).strip().rstrip(".,;)]}'\"")
    url = re.sub(r"#.*$", "", url)
    url = re.sub(r"(/(?:0|64|96|132|640))[A-Za-z]+$", r"\1", url)
    if url.startswith("http://") and any(host in url for host in _IMAGE_HOSTS):
        url = "https://" + url[len("http://"):]
    return url


def is_image_url(url):
    if not url:
        return False
    lower = url.lower()
    if any(noise in lower for noise in _IMAGE_ICON_NOISE):
        return False
    if any(host in lower for host in _IMAGE_HOSTS):
        return True
    if re.search(r"\.(?:png|jpe?g|gif|webp|bmp)(?:\?|$)", lower):
        return "doc.weixin.qq.com" not in lower
    return False


def extract_image_urls(*blobs):
    found = []
    seen = set()
    for raw in blobs:
        if not raw:
            continue
        if isinstance(raw, str):
            text = raw
            data = raw.encode("utf-8", errors="ignore")
        else:
            data = _to_bytes(raw)
            text = data.decode("utf-8", errors="ignore")
        candidates = list(_URL_RE.findall(text))
        for segment in _protobuf_strings_loose(data):
            candidates.extend(_URL_RE.findall(segment))
        for url in candidates:
            url = normalize_image_url(url)
            if is_image_url(url) and url not in seen:
                seen.add(url)
                found.append(url)
    return found


def pick_best_image_url(urls, content_type=0):
    if not urls:
        return ""
    content_type = int(content_type or 0)

    def score(url):
        value = 0
        if "wwpic" in url:
            value += 50
        if "wework.qpic.cn" in url:
            value += 20
        if "wwhead" in url or "mmhead" in url:
            value += 5
        if content_type in (4, 15):
            value += 10
        return value

    return max(urls, key=score)


def strip_media_from_text(text, media_url=""):
    if not text:
        return ""
    cleaned = text
    if media_url:
        cleaned = cleaned.replace(media_url, "")
        cleaned = cleaned.replace(media_url.replace("https://", "http://"), "")
    cleaned = re.sub(
        r"https?://(?:wework\.qpic\.cn|wx\.qlogo\.cn|mmbiz\.qpic\.cn)\S+",
        "",
        cleaned,
    )
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip(" \t\n-/")
    if not cleaned or _LABEL_ONLY_RE.match(cleaned):
        return "[图片]" if media_url else cleaned
    return cleaned


def recover_display_fields(content_type, raw, existing_media_url="", existing_name=""):
    """Return (text, media_url, attachment_name) for UI rendering."""
    content_type = int(content_type or 0)
    text = recover_display_text(content_type, raw)
    urls = extract_image_urls(raw, text, existing_media_url)
    media_url = normalize_image_url(existing_media_url) if is_image_url(existing_media_url) else ""
    if not media_url:
        media_url = pick_best_image_url(urls, content_type)
    name = ""
    if content_type not in _TEXTISH_TYPES:
        name = normalize_attachment_name(existing_name) or extract_attachment_name(raw, text)
    if media_url:
        text = strip_media_from_text(text, media_url)
    if name and content_type in _FILE_TYPES:
        text = f"[{message_type_name(content_type)}] {name}"
    elif name and content_type == 4 and not media_url:
        text = f"[{message_type_name(content_type)}] {name}"
    return text, media_url, name


def build_display_fields(content_type, content, extra_content="", local_extra_content=""):
    content_type = int(content_type or 0)
    text = display_message_content(content_type, content, extra_content, local_extra_content)
    urls = extract_image_urls(content, extra_content, local_extra_content, text)
    media_url = pick_best_image_url(urls, content_type)
    name = ""
    if content_type not in _TEXTISH_TYPES:
        name = extract_attachment_name(content, extra_content, local_extra_content, text)
    if media_url:
        text = strip_media_from_text(text, media_url)
    if name and content_type in _FILE_TYPES:
        text = f"[{message_type_name(content_type)}] {name}"
    elif name and content_type == 4 and not media_url:
        text = f"[{message_type_name(content_type)}] {name}"
    return text, media_url, name


def _is_user_id_list(text):
    return bool(_USER_ID_LIST_RE.fullmatch((text or "").strip()))


def _rewrite_unknown_placeholder(content_type, text):
    """Replace legacy [未知(N)] placeholders once the type map knows N."""
    value = (text or "").strip()
    matched = _UNKNOWN_PLACEHOLDER_RE.fullmatch(value)
    prefix = None if matched else _UNKNOWN_PREFIX_RE.match(value)
    if not matched and not prefix:
        return ""
    code = int((matched or prefix).group(1))
    preferred = int(content_type or 0) or code
    if preferred not in MSG_TYPES and code not in MSG_TYPES:
        return ""
    label = message_type_name(preferred if preferred in MSG_TYPES else code)
    if matched:
        return f"[{label}]"
    rest = value[prefix.end():].strip()
    return f"[{label}] {rest}" if rest else f"[{label}]"


def recover_display_text(content_type, raw):
    """Best-effort readable text for UI, including already-synced garbage strings."""
    content_type = int(content_type or 0)
    if raw is None or raw == "":
        return f"[{message_type_name(content_type)}]"
    if isinstance(raw, str):
        rewritten = _rewrite_unknown_placeholder(content_type, raw)
        if rewritten:
            return rewritten
    if not isinstance(raw, str):
        return display_message_content(content_type, raw)

    urls = _URL_RE.findall(str(raw))
    names = _extract_filename_hints(raw)
    if content_type in _FILE_TYPES or content_type == 4:
        if names:
            return f"[{message_type_name(content_type)}] {names[0]}"
        image_urls = extract_image_urls(raw)
        if image_urls:
            return f"[{message_type_name(content_type)}] {pick_best_image_url(image_urls, content_type)}"
        if urls:
            return f"[{message_type_name(content_type)}] {urls[0]}"

    text = decode_content(raw)
    text = _strip_proto_prefix(text)
    if _is_id_token(text) or (content_type in _CONTACT_TYPES and _is_user_id_list(text)):
        text = ""
    if text and _text_quality(text) >= 12:
        text = re.sub(r"^[)A-Za-z](?=[\u4e00-\u9fff])", "", text)
        text = text.lstrip(") ").strip()
        return text
    image_urls = extract_image_urls(raw)
    if image_urls:
        return pick_best_image_url(image_urls, content_type)
    if urls:
        return urls[0]
    if names:
        return f"[{message_type_name(content_type)}] {names[0]}"
    if text and _text_quality(text) > 0 and not _is_garbage_text(text):
        return text
    return f"[{message_type_name(content_type)}]"


def message_type_name(content_type):
    return MSG_TYPES.get(int(content_type or 0), f"未知({content_type})")


def _combine_text_parts(parts):
    cleaned = _dedupe_texts([p for p in parts if p])
    if not cleaned:
        return ""
    kept = []
    for item in cleaned:
        if any(item != other and item in other for other in cleaned):
            continue
        kept.append(item)
    return "\n".join(kept)


def _pick_best_text(parts, filenames):
    combined = _combine_text_parts(parts)
    cjk = sum(1 for ch in combined if "\u4e00" <= ch <= "\u9fff")
    if combined and (cjk >= 4 or _text_quality(combined) >= 12 or _is_readable_short(combined)):
        return combined
    if filenames:
        return filenames[0]
    return combined if combined and _text_quality(combined) > 0 else ""


def display_message_content(content_type, content, extra_content="", local_extra_content=""):
    content_type = int(content_type or 0)
    filenames = []
    if content_type not in _TEXTISH_TYPES:
        filenames = _extract_filename_hints(content, extra_content, local_extra_content)

    # Prefer primary content field, then extras with quality scoring.
    parts = [
        decode_content(content),
        decode_content(extra_content),
        decode_content(local_extra_content),
    ]
    best = _pick_best_text(parts, filenames)
    if best:
        if content_type in _CONTACT_TYPES and _is_user_id_list(best):
            return f"[{message_type_name(content_type)}]"
        if content_type in _FILE_TYPES or content_type == 4:
            if _FILE_EXT_RE.search(best):
                return f"[{message_type_name(content_type)}] {best}"
        return best

    return f"[{message_type_name(content_type)}]"
