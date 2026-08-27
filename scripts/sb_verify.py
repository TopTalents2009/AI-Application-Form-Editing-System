# -*- coding: utf-8 -*-
"""docx 完整性深度校验：python-docx 能否真实打开并统计内容。
用法: python sb_verify.py <文件.docx>
输出: OK paras=<N> tables=<M> ；失败非零退出。
"""
import sys

def main(argv):
    if len(argv) < 2:
        print('usage: sb_verify.py <docx>', file=sys.stderr)
        return 2
    import docx
    try:
        d = docx.Document(argv[1])
        paras = sum(1 for p in d.paragraphs if p.text.strip())
        tables = len(d.tables)
    except Exception as e:
        print('BROKEN %s' % e, file=sys.stderr)
        return 1
    print('OK paras=%d tables=%d' % (paras, tables))
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
