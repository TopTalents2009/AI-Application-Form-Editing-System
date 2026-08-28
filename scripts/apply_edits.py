# -*- coding: utf-8 -*-
"""结构化编辑执行器：按 plan JSON 对 docx 做"段落级文本重写"。
用法: python apply_edits.py <src.docx> <out.docx> <backup.docx> <plan.json>
plan.json: {"edits":[{"find":"...","replace":"..."}, ...]}   replace 为空串表示删除该片段。
策略: 节点不动，仅重写段落内 w:t 文本（首 run 承载新文本，其余清空）；
     命中段落的所有完全同名孪生副本（mc 双副本结构）一并重写。
     单段未命中时，在连续 2～8 个非空段落窗口内做精确/宽松匹配（跨段锚点）。
输出: stdout JSON {"results":[{"find","status"}...]}  与 edits 等长（空锚点 status=skip）。
"""
import sys, json, re, shutil
from docx import Document
from docx.oxml.ns import qn

MAX_SPAN = 8
SEP = '\n'

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
    if not compact:
        return None
    return re.compile(r'\s*'.join(re.escape(c) for c in compact))

def rewrite_and_twins(paras, texts, idx, new_T):
    old = texts[idx]
    if old == new_T:
        return
    rewrite(paras[idx], new_T)
    texts[idx] = new_T
    if not (old or '').strip():
        return
    for i, tx in enumerate(texts):
        if i != idx and tx == old:
            rewrite(paras[i], new_T)
            texts[i] = new_T

def apply_single(paras, texts, idx, old_T, new_T):
    for i, tx in enumerate(texts):
        if tx == old_T:
            rewrite(paras[i], new_T)
            texts[i] = new_T

def filled_indices(texts):
    return [i for i, t in enumerate(texts) if (t or '').strip()]

def search_span(texts, find, loose):
    filled = filled_indices(texts)
    rx = loose_regex(find) if loose else None
    if loose and rx is None:
        return None
    max_w = min(MAX_SPAN, len(filled))
    for width in range(2, max_w + 1):
        for s in range(0, len(filled) - width + 1):
            idxs = filled[s:s + width]
            joined = SEP.join(texts[i] for i in idxs)
            if not loose:
                p = joined.find(find)
                if p >= 0:
                    return idxs, joined, p, p + len(find)
            else:
                m = rx.search(joined)
                if m:
                    return idxs, joined, m.start(), m.end()
    return None

def apply_span(paras, texts, idxs, a, b, rep):
    """idxs: 非空段落下标；[a,b) 为 SEP 拼接串上的命中区间。"""
    spans = []
    pos = 0
    for k, oi in enumerate(idxs):
        t = texts[oi]
        spans.append((oi, pos, pos + len(t)))
        pos += len(t)
        if k < len(idxs) - 1:
            pos += len(SEP)
    involved = [s for s in spans if s[2] > a and s[1] < b]
    if not involved:
        return
    first_i = involved[0][0]
    last_i = involved[-1][0]
    by_oi = {s[0]: s for s in spans}
    for oi in range(first_i, last_i + 1):
        old = texts[oi]
        sp = by_oi.get(oi)
        if sp is None:
            new_T = ''
        else:
            _, j0, _ = sp
            n = len(old)
            local_a = min(max(a - j0, 0), n)
            local_b = min(max(b - j0, 0), n)
            if oi == first_i and oi == last_i:
                new_T = old[:local_a] + rep + old[local_b:]
            elif oi == first_i:
                new_T = old[:local_a] + rep
            elif oi == last_i:
                new_T = old[local_b:]
            else:
                new_T = ''
        rewrite_and_twins(paras, texts, oi, new_T)

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
        find = str(e.get('find', '')).replace('\r\n', '\n').replace('\r', '\n').strip()
        rep = str(e.get('replace', ''))
        if not find:
            results.append({'find': '', 'status': 'skip'})
            continue
        status = 'miss'
        # ① 精确包含（单段）
        idx = next((i for i, tx in enumerate(texts) if find in tx), -1)
        if idx >= 0:
            T = texts[idx]
            new_T = T.replace(find, rep, 1)
            apply_single(paras, texts, idx, T, new_T)
            status = 'hit'
        else:
            # ② 宽松匹配（单段，允许任意空白差异）
            rx = loose_regex(find)
            idx = next((i for i, tx in enumerate(texts) if rx and rx.search(tx)), -1)
            if idx >= 0:
                T = texts[idx]
                m = rx.search(T)
                new_T = T[:m.start()] + rep + T[m.end():]
                apply_single(paras, texts, idx, T, new_T)
                status = 'hit-loose'
            else:
                # ③ 跨段：连续非空段落窗口
                hit = search_span(texts, find, False)
                if hit:
                    idxs, _joined, a, b = hit
                    apply_span(paras, texts, idxs, a, b, rep)
                    status = 'hit-span'
                else:
                    hit = search_span(texts, find, True)
                    if hit:
                        idxs, _joined, a, b = hit
                        apply_span(paras, texts, idxs, a, b, rep)
                        status = 'hit-span-loose'
        results.append({'find': find[:100], 'status': status})

    doc.save(out)
    print(json.dumps({'results': results}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
