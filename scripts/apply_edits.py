# -*- coding: utf-8 -*-
"""结构化编辑执行器：按 plan JSON 对 docx 做"段落级文本重写"。
用法: python apply_edits.py <src.docx> <out.docx> <backup.docx> <plan.json>
plan.json: {"edits":[{"find":"...","replace":"..."}, ...]}   replace 为空串表示删除该片段。
策略: 节点不动，仅重写段落内 w:t 文本（首 run 承载新文本，其余清空）；
     命中段落的所有完全同名孪生副本（mc 双副本结构）一并重写。
输出: stdout JSON {"results":[{"find","status"}...]}
"""
import sys, json, re, shutil
from docx import Document
from docx.oxml.ns import qn

def para_text(p):
    return ''.join(t.text or '' for t in p.iter(qn('w:t')))

def rewrite(p, new_text):
    ts = list(p.iter(qn('w:t')))
    if not ts:
        # 段落无 run：新建一个 w:r/w:t
        r = p.makeelement(qn('w:r'), {})
        t = r.makeelement(qn('w:t'), {})
        t.text = new_text
        r.append(t)
        p.append(r)
        return
    ts[0].text = new_text
    for t in ts[1:]:
        t.text = ''

def loose_regex(find):
    compact = re.sub(r'\s+', '', find)
    return re.compile(r'\s*'.join(re.escape(c) for c in compact))

def main(argv):
    if len(argv) < 5:
        print('usage: apply_edits.py <src> <out> <backup> <plan.json>', file=sys.stderr)
        return 2
    src, out, backup, plan_path = argv[1], argv[2], argv[3], argv[4]
    plan = json.load(open(plan_path, encoding='utf-8'))
    edits = plan.get('edits') or []
    shutil.copyfile(src, backup)

    doc = Document(src)
    paras = list(doc.element.body.iter(qn('w:p')))
    texts = [para_text(p) for p in paras]

    results = []
    for e in edits:
        find = str(e.get('find', '')).strip()
        rep = str(e.get('replace', ''))
        if not find:
            continue
        status = 'miss'
        # ① 精确包含
        idx = next((i for i, tx in enumerate(texts) if find in tx), -1)
        if idx >= 0:
            T = texts[idx]
            new_T = T.replace(find, rep, 1)
            for i, tx in enumerate(texts):          # 孪生副本同步
                if tx == T:
                    rewrite(paras[i], new_T)
                    texts[i] = new_T
            status = 'hit'
        else:
            # ② 宽松匹配（允许任意空白差异）
            rx = loose_regex(find)
            idx = next((i for i, tx in enumerate(texts) if rx.search(tx)), -1)
            if idx >= 0:
                T = texts[idx]
                m = rx.search(T)
                new_T = T[:m.start()] + rep + T[m.end():]
                for i, tx in enumerate(texts):
                    if tx == T:
                        rewrite(paras[i], new_T)
                        texts[i] = new_T
                status = 'hit-loose'
        results.append({'find': find[:100], 'status': status})

    doc.save(out)
    print(json.dumps({'results': results}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
