# -*- coding: utf-8 -*-
"""docx / OOXML 的 wps 走压缩包；老式 OLE 的 .wps/.doc 交给本机 Word 读取。
用法: python sb_extract.py <输入.docx|wps> <输出.txt> [--no-dedup]
"""
import sys, re, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def para_text(p):
    parts = []
    for node in p.iter():
        if node.tag == W + 't':
            parts.append(node.text or '')
        elif node.tag in (W + 'br', W + 'cr'):
            parts.append('\n')
        elif node.tag == W + 'tab':
            parts.append('\t')
    return ''.join(parts)

def extract_ooxml(src, dedup):
    if not zipfile.is_zipfile(src):
        return None
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile:
        return None
    names = z.namelist()
    if 'word/document.xml' not in names:
        return None
    root = ET.fromstring(z.read('word/document.xml'))
    seen, lines = set(), []
    for p in root.iter(W + 'p'):
        t = para_text(p).replace('\x00', '').strip()
        if not t:
            continue
        if dedup:
            key = re.sub(r'\s+', '', t)
            if key in seen:
                continue
            seen.add(key)
        lines.append(t)
    return '\n'.join(lines)


def extract_via_word(src):
    """OLE 复合文档（常见于另存的 .wps / .doc），用本机 Word 读正文。"""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx('Word.Application')
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(
            str(Path(src).resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        text = doc.Content.Text or ''
        return text.replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '').strip()
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def main(argv):
    if len(argv) < 3:
        print('usage: sb_extract.py <in.docx|wps> <out.txt> [--no-dedup]', file=sys.stderr)
        return 2
    src, dst = argv[1], argv[2]
    dedup = '--no-dedup' not in argv
    text = extract_ooxml(src, dedup)
    if text is None:
        try:
            text = extract_via_word(src)
        except Exception as exc:
            print('ERROR: 无法用 Word 读取该文件：' + str(exc), file=sys.stderr)
            return 1
    if not str(text or '').strip():
        print('ERROR: 未提取到文字', file=sys.stderr)
        return 1
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(text)
    print('chars=%d' % len(text), file=sys.stderr)
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
