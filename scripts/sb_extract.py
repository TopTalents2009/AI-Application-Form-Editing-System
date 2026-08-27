# -*- coding: utf-8 -*-
"""docx/wps(zip) 全文提取：正文段落 + 表格 + 文本框，自动去除 mc 双副本重复。
用法: python sb_extract.py <输入.docx> <输出.txt> [--no-dedup]
"""
import sys, re, zipfile
import xml.etree.ElementTree as ET

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

def main(argv):
    if len(argv) < 3:
        print('usage: sb_extract.py <in.docx|zip-wps> <out.txt> [--no-dedup]', file=sys.stderr)
        return 2
    src, dst = argv[1], argv[2]
    dedup = '--no-dedup' not in argv
    z = zipfile.ZipFile(src)
    names = z.namelist()
    doc = None
    for cand in ('word/document.xml',):
        if cand in names:
            doc = cand
            break
    if not doc:
        raise SystemExit('ERROR: word/document.xml 不存在——文件可能不是 OOXML 格式')
    root = ET.fromstring(z.read(doc))
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
    text = '\n'.join(lines)
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(text)
    print('paras=%d chars=%d dedup=%s' % (len(lines), len(text), dedup), file=sys.stderr)
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
