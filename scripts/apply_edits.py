# -*- coding: utf-8 -*-
"""结构化编辑执行器：按 plan JSON 对 docx 做段落级文本重写，保持源文件版式。
用法: python apply_edits.py <src.docx> <out.docx> <backup.docx> <plan.json>
plan.json: {"edits":[{"find":"...","replace":"..."}, ...]}   replace 为空串表示删除该片段。
策略: 复制源包后只改 w:t / 必要的 w:br；跨段锚点按原段落/单元格回写，不把邻栏合并进一格。
写入后若单元格/文本框内容高于原框，缩小该格字号与行距，避免撑破栏位。
输出: stdout JSON {"results":[{"find","status"}...]}  与 edits 等长（空锚点 status=skip）。
"""
import sys, json, re, shutil
from docx import Document
from docx.oxml.ns import qn

MAX_SPAN = 8
SEP = '\n'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
MIN_SZ = 16  # 8pt，再小申报书难以阅读
_A_EXT = '{http://schemas.openxmlformats.org/drawingml/2006/main}ext'
_ROW_EXTRA = re.compile(
    r'^\d{4}[./\-年]\d{1,2}'
    r'|^\d+\s*/\s*\d+\s*$'
    r'|^\d{4}\.\d{2}\.\d{2}'
)
_LABEL_EXACT = {
    '重要经历', '主要技术能力', '标志性成果', '业务领域', '核心技术', '发展情况',
    '引进必要性', '岗位匹配', '工作基础',
}
_LABEL_PREFIX = re.compile(
    r'^(论文|项目|教育|工作|专利|获奖|成果|著作|报告)\s*\d{1,2}$'
)


def para_text(p):
    return ''.join(t.text or '' for t in p.iter(qn('w:t')))


def _child_index(parent, child):
    for i, sib in enumerate(parent):
        if sib is child:
            return i
    return -1


def cell_path(p):
    """单元格稳定路径（不能用 id()，lxml 代理对象会复用）。"""
    n = p
    while n is not None:
        if n.tag == qn('w:tc'):
            parts = []
            x = n
            while x is not None:
                parent = x.getparent()
                if parent is None:
                    parts.append(x.tag.split('}')[-1])
                    break
                parts.append(x.tag.split('}')[-1] + str(_child_index(parent, x)))
                x = parent
            return tuple(reversed(parts))
        n = n.getparent()
    return None


def is_label(text):
    raw = str(text or '').strip()
    t = re.sub(r'\s+', '', raw)
    if not t:
        return False
    if t in _LABEL_EXACT:
        return True
    if _LABEL_PREFIX.match(t):
        return True
    if '。' in raw or '；' in raw or ';' in raw:
        return False
    if '限' in t and '字' in t and len(t) <= 40:
        return True
    if re.fullmatch(r'[\u4e00-\u9fff（）()]{2,16}', t) is None:
        return False
    return t.endswith((
        '品质', '水平', '重要性', '性分析', '必要性', '匹配', '基础',
        '领域', '技术', '情况', '经历', '能力', '成果', '理由', '支持',
    ))


def split_lines(s):
    return str(s or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')


def _set_t(t_el, text):
    t_el.text = text or ''
    if text and (text[:1].isspace() or text[-1:].isspace()):
        t_el.set(XML_SPACE, 'preserve')
    elif XML_SPACE in t_el.attrib:
        del t_el.attrib[XML_SPACE]


def rewrite(p, new_text):
    """重写段落文字：保留原 run 样式；多余换行写成 w:br，不新增段落。"""
    lines = split_lines(new_text)
    if not lines:
        lines = ['']
    ts = list(p.iter(qn('w:t')))
    if not ts:
        r = p.makeelement(qn('w:r'), {})
        t = r.makeelement(qn('w:t'), {})
        _set_t(t, lines[0])
        r.append(t)
        for line in lines[1:]:
            r.append(r.makeelement(qn('w:br'), {}))
            t2 = r.makeelement(qn('w:t'), {})
            _set_t(t2, line)
            r.append(t2)
        p.append(r)
        return
    first_t = ts[0]
    first_r = first_t.getparent()
    _set_t(first_t, lines[0])
    # 清掉该 run 里旧的 br / 多余 t，只留第一个 t
    for child in list(first_r):
        if child is first_t:
            continue
        if child.tag in (qn('w:br'), qn('w:cr'), qn('w:t'), qn('w:tab')):
            first_r.remove(child)
    for line in lines[1:]:
        first_r.append(first_r.makeelement(qn('w:br'), {}))
        t2 = first_r.makeelement(qn('w:t'), {})
        _set_t(t2, line)
        first_r.append(t2)
    for t in ts[1:]:
        if t.getparent() is first_r:
            continue
        _set_t(t, '')


def _attr(el, name):
    if el is None:
        return None
    return el.get(qn(name)) or el.get(name.split(':', 1)[-1])


def _para_sz(p, default=21):
    for el in p.iter(qn('w:sz')):
        v = _attr(el, 'w:val')
        if v:
            try:
                return int(float(v))
            except ValueError:
                continue
    return default


def _em_units(text):
    n = 0.0
    for ch in str(text or ''):
        if ch in '\n\r':
            continue
        o = ord(ch)
        if o < 128 or 0xFF61 <= o <= 0xFF9F:
            n += 0.5
        else:
            n += 1.0
    return n


def _line_twips(p, sz_hp):
    base = max(int(sz_hp) or 21, 1) * 10
    pPr = p.find(qn('w:pPr'))
    sp = pPr.find(qn('w:spacing')) if pPr is not None else None
    if sp is None:
        return base, 0, 0
    def _int(name):
        v = _attr(sp, name)
        try:
            return int(float(v)) if v else 0
        except ValueError:
            return 0
    before, after = _int('w:before'), _int('w:after')
    line, rule = _int('w:line'), (_attr(sp, 'w:lineRule') or 'auto')
    if line <= 0:
        line_h = base
    elif rule in ('exact', 'atLeast'):
        line_h = line
    else:
        line_h = max(1, int(base * line / 240))
    return line_h, before, after


def _para_height(p, text, width_twips, sz_hp=None):
    sz_hp = int(sz_hp or _para_sz(p))
    line_h, before, after = _line_twips(p, sz_hp)
    char_w = max(sz_hp, 1) * 10
    inner = max(int(width_twips or 0), char_w)
    cpl = max(1.0, inner / float(char_w))
    lines = 0
    parts = split_lines(text) if str(text or '') else ['']
    if not parts:
        parts = ['']
    for part in parts:
        em = _em_units(part)
        if em <= 0:
            lines += 1
        else:
            lines += max(1, int((em + cpl - 0.01) // cpl))
    return before + after + lines * line_h


def _tc_width(tc):
    tcPr = tc.find(qn('w:tcPr')) if tc is not None else None
    tcw = tcPr.find(qn('w:tcW')) if tcPr is not None else None
    if tcw is None:
        return None
    typ = _attr(tcw, 'w:type') or 'dxa'
    try:
        w = int(float(_attr(tcw, 'w:w') or 0))
    except ValueError:
        return None
    if typ in ('dxa', 'nil', ''):
        return w if w > 0 else None
    return None


def _tr_height(tr):
    trPr = tr.find(qn('w:trPr')) if tr is not None else None
    trh = trPr.find(qn('w:trHeight')) if trPr is not None else None
    if trh is None:
        return 0
    try:
        return int(float(_attr(trh, 'w:val') or 0))
    except ValueError:
        return 0


def _txbx_size(txbx):
    n = txbx
    while n is not None:
        if n.tag in (qn('w:drawing'), qn('w:pict')) or n.tag.endswith('}wsp') or n.tag.endswith('}anchor') or n.tag.endswith('}inline'):
            for el in n.iter(_A_EXT):
                cx, cy = el.get('cx'), el.get('cy')
                if cx and cy:
                    try:
                        return int(cx) // 635, int(cy) // 635
                    except ValueError:
                        continue
            break
        n = n.getparent()
    return None, None


def _box_info(p):
    n = p
    while n is not None:
        if n.tag == qn('w:txbxContent'):
            w, h = _txbx_size(n)
            return {'kind': 'txbx', 'key': ('txbx', id(n)), 'width': w, 'height': h}
        if n.tag == qn('w:tc'):
            w = _tc_width(n)
            tr = n.getparent()
            h = _tr_height(tr) if tr is not None and tr.tag == qn('w:tr') else 0
            return {'kind': 'tc', 'key': cell_path(p), 'width': w, 'height': h}
        n = n.getparent()
    return None


def snapshot_boxes(paras, texts):
    out = []
    for i, p in enumerate(paras):
        info = _box_info(p)
        if not info or not info.get('width'):
            out.append(None)
            continue
        out.append({
            'kind': info['kind'],
            'key': info['key'],
            'width': info['width'],
            'height': info['height'] or 0,
            'text': texts[i],
            'sz': _para_sz(p),
        })
    return out


def _set_para_sz(p, sz_hp):
    val = str(max(MIN_SZ, int(sz_hp)))
    rprs = list(p.iter(qn('w:rPr')))
    if not rprs:
        r = p.find(qn('w:r'))
        if r is None:
            r = p.makeelement(qn('w:r'), {})
            p.append(r)
        rpr = r.makeelement(qn('w:rPr'), {})
        r.insert(0, rpr)
        rprs = [rpr]
    for rPr in rprs:
        for tag in (qn('w:sz'), qn('w:szCs')):
            el = rPr.find(tag)
            if el is None:
                el = rPr.makeelement(tag, {})
                rPr.append(el)
            el.set(qn('w:val'), val)


def _tighten_spacing(p):
    pPr = p.find(qn('w:pPr'))
    if pPr is None:
        return
    sp = pPr.find(qn('w:spacing'))
    if sp is None:
        return
    line = _attr(sp, 'w:line')
    rule = _attr(sp, 'w:lineRule') or 'auto'
    if line and (rule is None or rule == 'auto'):
        try:
            lv = int(float(line))
        except ValueError:
            return
        if lv > 200:
            sp.set(qn('w:line'), str(max(200, int(lv * 0.85))))


def _fit_group(paras, texts, snap, key, idxs):
    s0 = snap[idxs[0]]
    width = s0['width']
    if not width:
        return
    same = [i for i, s in enumerate(snap) if s and s['key'] == key]
    if not same:
        same = list(idxs)
    orig_need = 0
    for i in same:
        orig_need += _para_height(paras[i], snap[i]['text'], width, snap[i]['sz'])
    box_h = max(s0['height'] or 0, orig_need)

    def needed(sz_map):
        total = 0
        for i in same:
            sz = sz_map.get(i) or _para_sz(paras[i])
            total += _para_height(paras[i], texts[i], width, sz)
        return total

    if needed({}) <= int(box_h * 1.04) + 20:
        return
    targets = [i for i in same if not is_label(texts[i])]
    if not targets:
        targets = list(idxs)
    orig_sz = min(_para_sz(paras[i]) for i in targets)
    chosen = None
    for sz in range(orig_sz - 1, MIN_SZ - 1, -1):
        if needed({i: sz for i in targets}) <= box_h:
            chosen = sz
            break
    if chosen is None:
        chosen = MIN_SZ
        for i in targets:
            _tighten_spacing(paras[i])
    if chosen < orig_sz:
        for i in targets:
            _set_para_sz(paras[i], chosen)


def fit_overflow(paras, texts, snap):
    """把变长的单元格/文本框缩回原框高度（缩小字号，不改表格结构）。"""
    groups = {}
    for i, s in enumerate(snap):
        if not s:
            continue
        if (texts[i] or '') == (s['text'] or ''):
            continue
        groups.setdefault(s['key'], []).append(i)
    for key, idxs in groups.items():
        _fit_group(paras, texts, snap, key, idxs)


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


def loose_regex(find):
    compact = re.sub(r'\s+', '', find)
    if not compact:
        return None
    return re.compile(r'\s*'.join(re.escape(c) for c in compact))


def nearby_plain(texts, idx, radius=3):
    out = set()
    for j in range(max(0, idx - radius), min(len(texts), idx + radius + 1)):
        if j == idx:
            continue
        s = str(texts[j] or '').strip()
        if s:
            out.add(s)
    return out


def nearby_labels(texts, idx, radius=3):
    return {s for s in nearby_plain(texts, idx, radius) if is_label(s)}


def _strip_leading_label(text, labels):
    first = str(text or '').strip()
    for lab in sorted(labels, key=len, reverse=True):
        if not lab:
            continue
        if first == lab:
            return ''
        if first.startswith(lab) and len(first) > len(lab):
            rest = first[len(lab):]
            if rest[:1] in '：: \t':
                return rest.lstrip('：: \t')
    return first


def sanitize_replace(find, rep, texts, idx):
    """单段落锚点：去掉相邻小标题，禁止把整块基本情况塞进一格。"""
    lines = split_lines(rep)
    labels = nearby_labels(texts, idx)
    if is_label(texts[idx]):
        labels.add(str(texts[idx] or '').strip())
    lines = [ln for ln in lines if ln.strip() not in labels]
    if SEP not in find:
        content = [ln for ln in lines if ln.strip()]
        if not content:
            return split_lines(rep)[0] if split_lines(rep) else ''
        return _strip_leading_label(content[0], labels) or content[0]
    return '\n'.join(lines)


def explode_inline_headings(lines, originals):
    """原文是「小标题」单独一段时，把 replace 里的「小标题：正文」拆回两段。"""
    labels = [str(o or '').strip() for o in originals if is_label(o)]
    out = []
    for ln in lines:
        s = str(ln or '')
        st = s.strip()
        split_ok = False
        for lab in labels:
            if not lab:
                continue
            if st.startswith(lab) and len(st) > len(lab) and st[len(lab):len(lab) + 1] in '：:':
                body = st[len(lab) + 1:].strip()
                out.append(lab)
                if body:
                    out.append(body)
                split_ok = True
                break
        if not split_ok:
            m = re.match(r'^([\u4e00-\u9fff（）()]{2,16})[：:](.+)$', st)
            if m and is_label(m.group(1)) and m.group(2).strip():
                out.append(m.group(1))
                out.append(m.group(2).strip())
                split_ok = True
        if not split_ok:
            out.append(s)
    return out


def drop_echoed_labels(lines, originals, extras=None):
    skip = {str(o or '').strip() for o in originals if is_label(o)}
    if extras:
        skip.update(x for x in extras if is_label(x))
    skip.discard('')
    return [ln for ln in lines if str(ln or '').strip() not in skip]


def _consume_matching_label(remaining, orig):
    o = str(orig or '').strip()
    if not remaining or not o:
        return
    st = remaining[0].strip()
    if st == o:
        remaining.pop(0)
        return
    if st.startswith(o) and len(st) > len(o) and st[len(o):len(o) + 1] in '：:':
        remaining.pop(0)


def _is_foreign_heading(ln, original_labels):
    st = str(ln or '').strip()
    if not st:
        return False
    if is_label(st) or st in original_labels:
        return True
    m = re.match(r'^([\u4e00-\u9fff（）()]{2,16})[：:](.+)$', st)
    return bool(m)


def align_parts(lines, originals, same_cell):
    """按原段落槽位回写：栏目标题格只保留原标题；正文格填新正文。"""
    n = len(originals)
    lines = explode_inline_headings(list(lines), originals)
    if n <= 0:
        return []
    flags = [is_label(o) for o in originals]
    orig_labels = {str(o or '').strip() for o, f in zip(originals, flags) if f}

    if not any(flags):
        content = list(lines) if lines else ['']
        if same_cell:
            parts = [''] * n
            parts[0] = '\n'.join(content)
            return parts
        if len(content) == n:
            return content
        if len(content) < n:
            return content + list(originals[len(content):])
        return content[:n - 1] + ['\n'.join(content[n - 1:])]

    remaining = list(lines)
    parts = [''] * n
    last_body = None
    for i, orig in enumerate(originals):
        if flags[i]:
            while remaining and is_label(remaining[0]) and remaining[0].strip() != str(orig or '').strip():
                remaining.pop(0)
            _consume_matching_label(remaining, orig)
            parts[i] = orig
            continue
        while remaining and is_label(remaining[0]):
            remaining.pop(0)
        if remaining:
            parts[i] = remaining.pop(0)
        else:
            parts[i] = orig
        last_body = i
    extra = []
    for ln in remaining:
        if not str(ln).strip():
            continue
        if _is_foreign_heading(ln, orig_labels):
            continue
        if _ROW_EXTRA.match(str(ln).strip()):
            continue
        extra.append(ln)
    if extra and last_body is not None:
        cur = parts[last_body]
        add = '\n'.join(extra)
        parts[last_body] = (cur + '\n' + add).strip() if (cur or '').strip() else add
    return parts


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
    """idxs: 非空段落下标；[a,b) 为 SEP 拼接串上的命中区间。按原段落回写。"""
    spans = []
    pos = 0
    for k, oi in enumerate(idxs):
        t = texts[oi]
        spans.append((oi, pos, pos + len(t)))
        pos += len(t)
        if k < len(idxs) - 1:
            pos += len(SEP)
    prefix = SEP.join(texts[i] for i in idxs)[:a]
    suffix = SEP.join(texts[i] for i in idxs)[b:]
    originals = [texts[i] for i in idxs]
    extras = set()
    if idxs:
        extras = nearby_labels(texts, idxs[0])
        extras.update(nearby_labels(texts, idxs[-1]))
        extras -= {str(originals[0] or '').strip(), str(originals[-1] or '').strip()}
    lines = drop_echoed_labels(split_lines(rep), originals, extras)
    cells = [cell_path(paras[i]) for i in idxs]
    same_cell = len(set(cells)) == 1
    parts = align_parts(lines, originals, same_cell)
    if prefix:
        parts[0] = prefix.split(SEP)[0] + parts[0]
    if suffix:
        parts[-1] = parts[-1] + suffix.split(SEP)[-1]
    for k, oi in enumerate(idxs):
        new_T = parts[k]
        if not (new_T or '').strip() and not same_cell and (originals[k] or '').strip():
            new_T = originals[k]
        rewrite_and_twins(paras, texts, oi, new_T)


def apply_file(src, out, backup, edits):
    """按 edits 改 src.docx，写出 out，并复制 backup。返回与 edits 等长的 results。"""
    src, out, backup = str(src), str(out), str(backup)
    shutil.copyfile(src, backup)
    shutil.copyfile(src, out)
    doc = Document(out)
    paras = list(doc.element.body.iter(qn('w:p')))
    texts = [para_text(p) for p in paras]
    snap = snapshot_boxes(paras, texts)
    results = []
    for e in edits or []:
        find = str(e.get('find', '')).replace('\r\n', '\n').replace('\r', '\n').strip()
        rep = str(e.get('replace', '')).replace('\r\n', '\n').replace('\r', '\n')
        if not find:
            results.append({'find': '', 'status': 'skip'})
            continue
        status = 'miss'
        idx = next((i for i, tx in enumerate(texts) if find in tx), -1)
        if idx >= 0:
            T = texts[idx]
            use = sanitize_replace(find, rep, texts, idx)
            new_T = T.replace(find, use, 1)
            apply_single(paras, texts, idx, T, new_T)
            status = 'hit'
        else:
            rx = loose_regex(find)
            idx = next((i for i, tx in enumerate(texts) if rx and rx.search(tx)), -1)
            if idx >= 0:
                T = texts[idx]
                m = rx.search(T)
                use = sanitize_replace(find, rep, texts, idx)
                new_T = T[:m.start()] + use + T[m.end():]
                apply_single(paras, texts, idx, T, new_T)
                status = 'hit-loose'
            else:
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
    fit_overflow(paras, texts, snap)
    doc.save(out)
    return results


def main(argv):
    if len(argv) < 5:
        print('usage: apply_edits.py <src> <out> <backup> <plan.json>', file=sys.stderr)
        return 2
    src, out, backup, plan_path = argv[1], argv[2], argv[3], argv[4]
    plan = json.load(open(plan_path, encoding='utf-8'))
    results = apply_file(src, out, backup, plan.get('edits') or [])
    print(json.dumps({'results': results}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
