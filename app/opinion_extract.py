"""修改意见文件提取：Word / 文本 / Excel / 图片（Gemini 识字）/ 录音（Gemini 转写）。"""
from __future__ import annotations
import asyncio, base64, csv, io, os, re, shutil, tempfile
from pathlib import Path

from .config import (
    SCRIPTS_DIR, PYEXE, LLM_CONNECT_TIMEOUT, compare_model_profiles,
    llm_api_base, httpx_trust_env,
)
from .llm import chat, LlmError

PYENV = dict(os.environ, PYTHONIOENCODING="utf-8")

WORD_EXT = {".docx", ".docm", ".wps"}
TEXT_EXT = {".txt", ".md"}
EXCEL_EXT = {".xlsx", ".xlsm", ".xls", ".csv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp"}
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac", ".amr", ".wma", ".webm"}
ALLOWED_OPINION_EXT = WORD_EXT | TEXT_EXT | EXCEL_EXT | IMAGE_EXT | AUDIO_EXT

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 50 * 1024 * 1024
AUDIO_INLINE_SOFT = 12 * 1024 * 1024
OCR_TIMEOUT_S = 300.0
AUDIO_TIMEOUT_S = 900.0
OCR_PAGE_RETRIES = 3

_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
}

_AUDIO_MIME = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".amr": "audio/amr",
    ".wma": "audio/x-ms-wma",
    ".webm": "audio/webm",
}

OCR_PROMPT = (
    "这是申报书修改意见的图片（打印、扫描或手写均可）。请识别图中全部可见文字，原样抄录。"
    "不要翻译，不要总结，不要分析图片，不要输出思考过程。"
    "若完全没有文字，则 OCR 段只写：（图片中未识别到文字）"
    "必须按下述格式输出，OCR 与 END 标记各占一行：\n"
    "<<<OCR>>>\n"
    "（此处只放从图中抄下的原文）\n"
    "<<<END>>>"
)

APP_PDF_OCR_PROMPT = (
    "这是申报书 PDF 的一页扫描图（可能含表格、印章、手写）。请识别图中全部可见文字，按阅读顺序原样抄录。"
    "表格内容用制表符分隔列、换行分隔行；保留标题、栏位名与填写内容。"
    "不要翻译，不要总结，不要分析图片，不要输出思考过程。"
    "若该页完全没有文字，则 OCR 段只写：（本页未识别到文字）"
    "必须按下述格式输出，OCR 与 END 标记各占一行：\n"
    "<<<OCR>>>\n"
    "（此处只放从图中抄下的原文）\n"
    "<<<END>>>"
)

AUDIO_PROMPT = (
    "这是申报书专家辅导会/审核会的录音，请转写成中文「申报书修改意见」。\n"
    "录音文件名：{name}\n"
    "要求：\n"
    "1. 只保留与申报书修改、补材料、改写法、格式、证明材料有关的意见；寒暄、点名签到、与申报无关的闲聊可省略。\n"
    "2. 按条列出，每条单独一行，用「1.」「2.」「3.」编号，便于后续逐条修改。\n"
    "3. 若发言点名了申报人、单位或申报书编号，写在该条开头，例如「1. 本杰明：论文需补充影响因子」。\n"
    "4. 一场会若点评了多人，按申报人分段，先写姓名再写其意见。\n"
    "5. 不要翻译成英文，不要写成会议纪要标题，不要输出思考过程或英文草稿。\n"
    "必须按下述格式输出，TEXT 与 END 标记各占一行：\n"
    "<<<TEXT>>>\n"
    "（此处只放转写后的修改意见正文）\n"
    "<<<END>>>"
)


def resolve_ocr_timeout(explicit: float | None = None) -> float:
    """OCR 单页超时：显式参数 > Gemini 模型配置 timeoutSec > 默认值。"""
    if explicit is not None:
        try:
            v = float(explicit)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    gem = (compare_model_profiles() or {}).get("gemini") or {}
    t = gem.get("timeoutSec")
    if t not in (None, ""):
        try:
            v = float(t)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return OCR_TIMEOUT_S


def resolve_audio_timeout(explicit: float | None = None) -> float:
    """录音转写超时：显式参数 > Gemini timeoutSec 与默认 900s 取较大值。"""
    gem_t = resolve_ocr_timeout(explicit)
    return max(AUDIO_TIMEOUT_S, gem_t)


def ext_of(name: str) -> str:
    s = str(name or "")
    i = s.rfind(".")
    return s[i:].lower() if i >= 0 else ""


def is_opinion_ext(name: str) -> bool:
    return ext_of(name) in ALLOWED_OPINION_EXT


_SKIP_CELL = re.compile(r"^(True|False|#REF!|#VALUE!|#N/A|#DIV/0!|#NAME\?)$", re.I)


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    s = str(v).strip()
    if not s or _SKIP_CELL.match(s):
        return ""
    return s


def _rows_to_text(title: str, rows) -> str:
    lines = ["【工作表：" + str(title or "Sheet") + "】"]
    n = 0
    for row in rows or []:
        cells = [_cell_str(c) for c in (row or [])]
        while cells and not cells[0]:
            cells.pop(0)
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            continue
        lines.append("\t".join(cells))
        n += 1
    if n == 0:
        return ""
    return "\n".join(lines)


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def excel_to_text(path: Path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".csv":
        return _csv_to_text(p)
    head = p.read_bytes()[:8] if p.exists() else b""
    raw_head = p.read_bytes()[:4096] if p.exists() else b""
    encrypted = b"E\x00n\x00c\x00r\x00y\x00p\x00t\x00e\x00d\x00P\x00a\x00c\x00k\x00a\x00g\x00e\x00" in raw_head or b"EncryptedPackage" in raw_head
    if encrypted:
        raise ValueError("该 Excel 已加密（EncryptedPackage），无法提取文字，请另存为未加密的 .xlsx / .xls")
    if head.startswith(b"PK"):
        return _xlsx_to_text(p)
    if head.startswith(_OLE_MAGIC):
        return _xls_to_text(p)
    if ext in (".xlsx", ".xlsm"):
        return _xlsx_to_text(p)
    if ext == ".xls":
        return _xls_to_text(p)
    raise ValueError("不是 Excel 文件：" + p.name)


def _csv_to_text(path: Path) -> str:
    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    out = _rows_to_text(path.stem, rows)
    if not out.strip():
        raise ValueError("CSV 中没有可提取的文字")
    return out


def _xlsx_to_text(path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise ValueError("服务器未安装 openpyxl，无法读取 .xlsx") from e
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        raise ValueError("无法打开 Excel：" + str(e)[:180]) from e
    parts = []
    try:
        for ws in wb.worksheets:
            rows = list(ws.iter_rows(values_only=True))
            chunk = _rows_to_text(ws.title, rows)
            if chunk:
                parts.append(chunk)
    finally:
        wb.close()
    if not parts:
        raise ValueError("Excel 中没有可提取的文字")
    return "\n\n".join(parts)


def _xls_to_text(path: Path) -> str:
    try:
        import xlrd
    except ImportError as e:
        raise ValueError("服务器未安装 xlrd，无法读取旧版 .xls") from e
    try:
        book = xlrd.open_workbook(str(path))
    except Exception as e:
        raise ValueError("无法打开 .xls：" + str(e)[:180]) from e
    parts = []
    for sheet in book.sheets():
        rows = []
        for r in range(sheet.nrows):
            rows.append([sheet.cell_value(r, c) for c in range(sheet.ncols)])
        chunk = _rows_to_text(sheet.name, rows)
        if chunk:
            parts.append(chunk)
    if not parts:
        raise ValueError("Excel 中没有可提取的文字")
    return "\n\n".join(parts)


def _sniff_image(data: bytes, name: str) -> tuple[str, str]:
    ext = ext_of(name)
    mime = _MIME.get(ext) or ""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif", "image/gif"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if head.startswith(b"BM"):
        return ".bmp", "image/bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif", "image/tiff"
    if mime:
        return ext, mime
    raise ValueError("不是支持的图片格式（jpg / png / webp / gif / tif / bmp）：" + (name or "file"))


def _prepare_image_bytes(data: bytes, mime: str, *, for_ocr: bool = False) -> tuple[bytes, str]:
    """必要时转成 JPEG，避免 TIFF/BMP 不被网关接受。"""
    need = mime not in ("image/jpeg", "image/png", "image/webp", "image/gif") or len(data) > 4 * 1024 * 1024
    if for_ocr and mime in ("image/jpeg", "image/png", "image/webp"):
        need = True
    if not need:
        return data, mime
    try:
        from PIL import Image
    except ImportError:
        return data, mime
    im = Image.open(io.BytesIO(data))
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    elif im.mode == "L":
        im = im.convert("RGB")
    w, h = im.size
    mx = 2800 if for_ocr else 4096
    if max(w, h) > mx:
        im.thumbnail((mx, mx))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82 if for_ocr else 85)
    return buf.getvalue(), "image/jpeg"


async def ocr_image_bytes(
    data: bytes,
    mime: str,
    name: str = "image",
    *,
    prompt: str | None = None,
    timeout_s: float | None = None,
) -> str:
    """Gemini 视觉 OCR：单张图片字节 → 纯文本。"""
    if not data:
        raise ValueError("图片为空：" + name)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片超过 12MB，请压缩后再上传：" + name)
    data, mime = _prepare_image_bytes(data, mime, for_ocr=True)
    gem = (compare_model_profiles() or {}).get("gemini") or {}
    if not gem.get("ready"):
        raise ValueError("图片识别需要 Gemini。请先在模型配置中填好 Gemini 的地址和密钥。")
    b64 = base64.b64encode(data).decode("ascii")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt or OCR_PROMPT},
            {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + b64}},
        ],
    }]
    effective_timeout = resolve_ocr_timeout(timeout_s)
    last_err = None
    r = None
    for attempt in range(1, OCR_PAGE_RETRIES + 1):
        try:
            r = await chat(
                messages,
                json_mode=False,
                timeout_s=effective_timeout,
                model=gem.get("id"),
                retries=1,
                apply_profile_timeout=False,
            )
            last_err = None
            break
        except LlmError as e:
            last_err = e
            msg = str(e)
            if attempt < OCR_PAGE_RETRIES and ("超时" in msg or "timeout" in msg.lower() or "连接" in msg):
                await asyncio.sleep(3 * attempt)
                continue
            raise ValueError("Gemini 识字失败（" + name + "）：" + msg[:240]) from e
    if last_err:
        raise ValueError("Gemini 识字失败（" + name + "）：" + str(last_err)[:240]) from last_err
    text = _strip_ocr(str((r or {}).get("content") or ""))
    if not text:
        raise ValueError("Gemini 未从图片中提取到文字：" + name)
    return text


async def image_to_text(path: Path) -> str:
    p = Path(path)
    data = p.read_bytes()
    _ext, mime = _sniff_image(data, p.name)
    return await ocr_image_bytes(data, mime, p.name)


def _sniff_audio(data: bytes, name: str) -> tuple[str, str]:
    ext = ext_of(name)
    mime = _AUDIO_MIME.get(ext) or ""
    head = data[:16]
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return (ext if ext in AUDIO_EXT else ".m4a"), "audio/mp4"
    if head.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return ".mp3", "audio/mpeg"
    if head.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav", "audio/wav"
    if head.startswith(b"OggS"):
        return ".ogg", "audio/ogg"
    if head.startswith(b"fLaC"):
        return ".flac", "audio/flac"
    if head.startswith(b"#!AMR"):
        return ".amr", "audio/amr"
    if mime:
        return ext, mime
    raise ValueError("不是支持的录音格式（m4a / mp3 / wav / aac / ogg / flac）：" + (name or "file"))


def _audio_format(mime: str, ext: str) -> str:
    mime = str(mime or "").lower()
    ext = str(ext or "").lower().lstrip(".")
    if "mpeg" in mime or ext == "mp3":
        return "mp3"
    if "wav" in mime or ext == "wav":
        return "wav"
    if "aac" in mime or ext == "aac":
        return "aac"
    if "ogg" in mime or ext == "ogg":
        return "ogg"
    if "flac" in mime or ext == "flac":
        return "flac"
    if "webm" in mime or ext == "webm":
        return "webm"
    if ext == "m4a" or "m4a" in mime:
        return "m4a"
    if "mp4" in mime or ext in ("m4a", "mp4"):
        return "mp4"
    return ext or "mp3"


def _find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe and Path(exe).is_file():
        try:
            if Path(exe).stat().st_size > 1024 * 1024:
                return exe
        except OSError:
            pass
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and Path(p).is_file() and Path(p).stat().st_size > 1024 * 1024:
            return p
    except Exception:
        pass
    return ""


def _ffmpeg_to_mp3(data: bytes, src_name: str) -> tuple[bytes, str, str] | None:
    ff = _find_ffmpeg()
    if not ff:
        return None
    suffix = ext_of(src_name) or ".m4a"
    try:
        with tempfile.TemporaryDirectory(prefix="sb-audio-") as td:
            src = Path(td) / ("in" + suffix)
            dst = Path(td) / "out.mp3"
            src.write_bytes(data)
            import subprocess
            r = subprocess.run(
                [ff, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(dst)],
                capture_output=True,
                timeout=180,
            )
            if (r.returncode or 0) != 0 or not dst.is_file() or dst.stat().st_size < 64:
                return None
            out = dst.read_bytes()
            if not out:
                return None
            return out, "audio/mpeg", (Path(src_name).stem or "audio") + ".mp3"
    except Exception:
        return None


def _gateway_send_spec(mime: str, name: str) -> tuple[str, str]:
    """12ai Gemini 白名单：audio/mpeg、audio/mp3、audio/wav、video/mp4。"""
    ext = ext_of(name)
    mime = str(mime or "").lower()
    if ext in (".mp3",) or "mpeg" in mime or mime == "audio/mp3":
        return "audio/mpeg", name if name.lower().endswith(".mp3") else (Path(name).stem or "audio") + ".mp3"
    if ext in (".wav",) or "wav" in mime:
        return "audio/wav", name
    if ext in (".m4a", ".mp4", ".aac") or "mp4" in mime or "m4a" in mime or "aac" in mime:
        return "video/mp4", (Path(name).stem or "audio") + ".mp4"
    return mime or "audio/mpeg", name


def _audio_content_variants(prompt: str, b64: str, mime: str, name: str, fmt: str) -> list:
    send_mime, send_name = _gateway_send_spec(mime, name)
    data_url = "data:" + send_mime + ";base64," + b64
    mpeg_url = "data:audio/mpeg;base64," + b64
    variants = [
        [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": send_name, "file_data": data_url}},
        ],
        [
            {"type": "text", "text": prompt},
            {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3" if fmt != "wav" else "wav"}},
        ],
    ]
    if send_mime != "audio/mpeg":
        variants.insert(1, [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": (Path(name).stem or "audio") + ".mp3", "file_data": mpeg_url}},
        ])
    return variants


def _payload_shape_retryable(msg: str) -> bool:
    s = str(msg or "").lower()
    keys = (
        "http 400", "http 415", "http 500", "http 413", "invalid", "unsupported",
        "unknown", "format", "mime", "audio", "m4a", "mp4", "file_data",
        "input_audio", "audio_url", "unexpected", "未识别", "不支持", "无法解析",
        "too large", "too long", "payload", "entity", "maximum",
    )
    return any(k in s for k in keys)


def _looks_like_no_audio(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return True
    low = s.lower()
    keys = (
        "no_audio", "未检测", "未听到", "听不到", "没有音频", "没有声音",
        "请提供录音", "无法播放", "不是音频", "没有听到", "未能识别语音",
    )
    if any(k in low or k in s for k in keys) and len(s) < 240:
        return True
    return False


def _clean_transcript(text: str) -> str:
    s = _strip_ocr(text)
    if not s:
        return ""
    lines = []
    for raw in s.replace("\r\n", "\n").split("\n"):
        t = raw.strip()
        if not t:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if t.startswith("**") and t.endswith("**") and len(t) < 80:
            continue
        ascii_only = bool(re.fullmatch(r"[\x00-\x7f]+", t))
        if ascii_only and not re.search(r"\d+\.\s", t):
            if re.match(r"^(I['m\s]|The user|Let |I'm |I've |I've|Begin |Analyzing|Initiating)", t):
                continue
            if len(t) > 50:
                continue
        lines.append(raw.rstrip())
    return "\n".join(lines).strip()


def _transcript_usable(text: str) -> bool:
    s = _clean_transcript(text)
    if not s or _looks_like_no_audio(s):
        return False
    return len(re.findall(r"[\u4e00-\u9fa5]", s)) >= 20


async def _chat_transcribe_variants(
    prompt: str,
    data: bytes,
    mime: str,
    name: str,
    gem: dict,
    timeout_s: float,
) -> tuple[str, Exception | None]:
    fmt = _audio_format(mime, ext_of(name))
    b64 = base64.b64encode(data).decode("ascii")
    last_err = None
    for content in _audio_content_variants(prompt, b64, mime, name, fmt):
        try:
            r = await chat(
                [{"role": "user", "content": content}],
                json_mode=False,
                timeout_s=timeout_s,
                model=gem.get("id"),
                retries=1,
                apply_profile_timeout=False,
            )
            text = _clean_transcript(str((r or {}).get("content") or ""))
            if _transcript_usable(text):
                return text, None
            last_err = ValueError("Gemini 未从录音中提取到文字：" + name)
        except LlmError as e:
            last_err = e
            if _payload_shape_retryable(str(e)):
                continue
            raise
    return "", last_err


async def _transcribe_whisper_endpoint(data: bytes, mime: str, name: str, gem: dict, timeout_s: float) -> str:
    import httpx
    url = llm_api_base(gem.get("baseUrl") or "") + "/audio/transcriptions"
    headers = {"Authorization": "Bearer " + str(gem.get("apiKey") or "")}
    timeout = httpx.Timeout(timeout_s, connect=LLM_CONNECT_TIMEOUT)
    trust = httpx_trust_env()
    files = {"file": (name or "audio.m4a", data, mime or "application/octet-stream")}
    form = {"model": str(gem.get("id") or "gemini-3.7-flash"), "language": "zh"}
    async with httpx.AsyncClient(timeout=timeout, trust_env=trust) as client:
        r = await client.post(url, headers=headers, files=files, data=form)
    if r.status_code >= 400:
        raise LlmError("HTTP " + str(r.status_code) + ": " + (r.text or "")[:240])
    try:
        obj = r.json()
    except Exception:
        return (r.text or "").strip()
    if isinstance(obj, dict):
        return str(obj.get("text") or obj.get("content") or "").strip()
    return str(obj or "").strip()


async def transcribe_audio_bytes(
    data: bytes,
    mime: str,
    name: str = "audio.m4a",
    *,
    timeout_s: float | None = None,
) -> str:
    """Gemini 多模态转写：录音字节 → 修改意见文本。"""
    if not data:
        raise ValueError("录音为空：" + name)
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("录音超过 50MB，请压缩后再上传：" + name)
    gem = (compare_model_profiles() or {}).get("gemini") or {}
    if not gem.get("ready"):
        raise ValueError("录音转写需要 Gemini。请先在模型配置中填好 Gemini 的地址和密钥。")
    _ext, mime = _sniff_audio(data, name)
    prompt = AUDIO_PROMPT.replace("{name}", name)
    effective_timeout = resolve_audio_timeout(timeout_s)
    last_err = None
    queue: list[tuple[bytes, str, str]] = []
    packed = None
    if _ext not in (".mp3", ".wav") or len(data) > AUDIO_INLINE_SOFT:
        packed = _ffmpeg_to_mp3(data, name)
    if packed:
        queue.append(packed)
    else:
        send_mime, send_name = _gateway_send_spec(mime, name)
        queue.append((data, send_mime, send_name))
    for send_data, send_mime, send_name in queue:
        try:
            text, err = await _chat_transcribe_variants(
                prompt, send_data, send_mime, send_name, gem, effective_timeout,
            )
            if text:
                return text
            last_err = err
        except LlmError as e:
            last_err = e
            if not _payload_shape_retryable(str(e)):
                raise ValueError("Gemini 转写失败（" + name + "）：" + str(e)[:240]) from e
    if all(m != "audio/mpeg" for _d, m, _n in queue):
        packed = _ffmpeg_to_mp3(data, name)
        if packed:
            send_data, send_mime, send_name = packed
            try:
                text, err = await _chat_transcribe_variants(
                    prompt, send_data, send_mime, send_name, gem, effective_timeout,
                )
                if text:
                    return text
                last_err = err or last_err
                send_for_whisper = packed
            except LlmError as e:
                last_err = e
                send_for_whisper = packed
        else:
            send_for_whisper = (data, mime, name)
    else:
        send_for_whisper = queue[0]
    try:
        text = await _transcribe_whisper_endpoint(
            send_for_whisper[0], send_for_whisper[1], send_for_whisper[2], gem, effective_timeout,
        )
        text = _clean_transcript(text)
        if _transcript_usable(text):
            return text
    except Exception as e:
        if last_err is None:
            last_err = e
    msg = str(last_err or "未得到转写结果")[:240]
    raise ValueError("Gemini 转写失败（" + name + "）：" + msg)


async def audio_to_text(path: Path) -> str:
    p = Path(path)
    data = p.read_bytes()
    _ext, mime = _sniff_audio(data, p.name)
    return await transcribe_audio_bytes(data, mime, p.name)


def _strip_ocr(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return ""
    for start, end in (("<<<OCR>>>", "<<<END>>>"), ("<<<TEXT>>>", "<<<END>>>")):
        i = s.find(start)
        if i < 0:
            continue
        rest = s[i + len(start):]
        j = rest.find(end)
        body = rest[:j] if j >= 0 else rest
        body = body.strip()
        if body:
            return body
    fence = chr(96) * 3
    if s.startswith(fence):
        s = re.sub(r"^```(?:text|txt|markdown)?\s*", "", s, count=1)
        if s.endswith(fence):
            s = s[: -len(fence)]
        s = s.strip()
    return s


async def _word_to_txt(src: Path, dst: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        PYEXE, str(SCRIPTS_DIR / "sb_extract.py"), str(src), str(dst),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=PYENV,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        raise ValueError("Word 提取超时：" + src.name)
    if (proc.returncode or 0) != 0:
        msg = (err or out or b"").decode("utf-8", "replace")[:200]
        raise ValueError("Word 提取失败：" + src.name + (" " + msg if msg else ""))
    if not dst.exists() or dst.stat().st_size == 0:
        raise ValueError("Word 提取结果为空：" + src.name)


async def ensure_txt(src: Path, dst: Path) -> None:
    """把意见/申报书源文件提取为 utf-8 txt。失败抛 ValueError。"""
    src, dst = Path(src), Path(dst)
    ext = src.suffix.lower()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if ext in TEXT_EXT:
        shutil.copyfile(src, dst)
        return
    if ext in EXCEL_EXT:
        text = excel_to_text(src)
        dst.write_text(text, encoding="utf-8")
        return
    if ext in IMAGE_EXT:
        text = await image_to_text(src)
        dst.write_text(text, encoding="utf-8")
        return
    if ext in AUDIO_EXT:
        text = await audio_to_text(src)
        dst.write_text(text, encoding="utf-8")
        return
    if ext in WORD_EXT:
        await _word_to_txt(src, dst)
        return
    raise ValueError("不支持的文件类型：" + src.name)
