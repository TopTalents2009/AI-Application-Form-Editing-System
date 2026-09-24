# -*- coding: utf-8 -*-
"""结构化编辑执行器：按 plan JSON 对 Excel 做单元格文本重写，保持源文件格式。
用法: python apply_excel.py <src.xlsx|xlsm|xls> <out> <backup> <plan.json>
策略: 先单格精确/宽松命中，再按提取时的制表符行拼接跨格替换。
.xlsx/.xlsm 用 openpyxl（xlsm 保留 VBA）；.xls 需本机 Excel COM（FileFormat=56）。
"""
from __future__ import annotations
import json
import os
import re
import shutil
import sys

MAX_SPAN = 8
SEP_ROW = "\t"


def cell_str(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip() if isinstance(v, str) else str(v)


def loose_regex(find):
    compact = re.sub(r"\s+", "", str(find or ""))
    if not compact:
        return None
    return re.compile(r"\s*".join(re.escape(c) for c in compact))


def _trim_row(cells):
    out = list(cells)
    while out and not out[-1]["text"]:
        out.pop()
    return out


def _join_row(cells):
    return SEP_ROW.join(c["text"] for c in cells)


def _set_cell(cell, text):
    cell["text"] = text
    cell["dirty"] = True


_LABEL_FILL = re.compile(r"关键词|key\s*words|不超过\s*\d+\s*个", re.I)
_EN_SUBTITLE = re.compile(r"key\s*words|no more than", re.I)


def apply_edits_to_sheets(sheets, edits):
    """sheets: [{name, rows:[{r, cells:[{c,text,dirty,orig}]}]}]. 行列均为 1-based。"""
    results = []
    for e in edits or []:
        find = str(e.get("find", "")).replace("\r\n", "\n").replace("\r", "\n").strip()
        rep = str(e.get("replace", "")).replace("\r\n", "\n").replace("\r", "\n")
        if not find:
            results.append({"find": "", "status": "skip"})
            continue
        status = "miss"
        if _hit_cells(sheets, find, rep, loose=False):
            status = "hit"
        elif _hit_cells(sheets, find, rep, loose=True):
            status = "hit-loose"
        elif _hit_rows(sheets, find, rep, loose=False):
            status = "hit-span"
        elif _hit_rows(sheets, find, rep, loose=True):
            status = "hit-span-loose"
        elif _hit_nearby(sheets, find, rep, loose=False):
            status = "hit-span"
        elif _hit_nearby(sheets, find, rep, loose=True):
            status = "hit-span-loose"
        results.append({"find": find[:100], "status": status})
    return results


def _is_label_fill(find, rep, cell_text):
    """标题格整格被当成 find、replace 却是空白栏正文时，应写入下方空格而不是覆盖标题。"""
    ft = str(find or "").strip()
    ct = str(cell_text or "").strip()
    rp = str(rep or "")
    if not ft or ft != ct:
        return False
    if ft and ft in rp.replace("\n", "").replace("\r", ""):
        return False
    if len(ft) > 80:
        return False
    return bool(_LABEL_FILL.search(ft))


def _row_cell_text(sh, r, c):
    for row in sh.get("rows") or []:
        if row["r"] != r:
            continue
        for cell in row["cells"]:
            if cell["c"] == c:
                return cell.get("text") or ""
    return ""


def _ensure_sheet_cell(sh, r, c, text):
    rows = sh.setdefault("rows", [])
    for row in rows:
        if row["r"] != r:
            continue
        for cell in row["cells"]:
            if cell["c"] == c:
                _set_cell(cell, text)
                return True
        row["cells"].append({"c": c, "text": text, "dirty": True, "orig": None})
        row["cells"].sort(key=lambda x: x["c"])
        return True
    i = 0
    while i < len(rows) and rows[i]["r"] < r:
        i += 1
    rows.insert(i, {"r": r, "cells": [{"c": c, "text": text, "dirty": True, "orig": None}]})
    return True


def _merge_anchor_below(ws, start_row, col):
    if ws is None:
        return None
    best = None
    try:
        ranges = list(ws.merged_cells.ranges)
    except Exception:
        return None
    for rng in ranges:
        if rng.min_col > col or rng.max_col < col:
            continue
        if rng.min_row <= start_row or rng.min_row > start_row + 12:
            continue
        val = cell_str(ws.cell(rng.min_row, rng.min_col).value)
        if val:
            continue
        if best is None or rng.min_row < best[0]:
            best = (rng.min_row, rng.min_col)
    return best


def _fill_empty_below(sh, title_row_idx, col, rep):
    rows = sh.get("rows") or []
    if title_row_idx < 0 or title_row_idx >= len(rows):
        return False
    title_r = rows[title_row_idx]["r"]
    scan_from = title_r
    for row in rows:
        if row["r"] <= title_r:
            continue
        if row["r"] > title_r + 4:
            break
        t = ""
        for cell in row["cells"]:
            if cell["c"] == col:
                t = cell.get("text") or ""
                break
        if t and _EN_SUBTITLE.search(t) and len(t) < 80:
            scan_from = row["r"]
            continue
        break
    target = _merge_anchor_below(sh.get("ws"), scan_from, col)
    if target:
        return _ensure_sheet_cell(sh, target[0], target[1], rep)
    occupied = {row["r"] for row in rows}
    for r in range(scan_from + 1, scan_from + 10):
        t = _row_cell_text(sh, r, col)
        if t and not (_EN_SUBTITLE.search(t) and len(t) < 80):
            return False
        if r not in occupied or not t:
            return _ensure_sheet_cell(sh, r, col, rep)
    return False


def _hit_cells(sheets, find, rep, loose):
    rx = loose_regex(find) if loose else None
    if loose and rx is None:
        return False
    for sh in sheets:
        for ri, row in enumerate(sh["rows"]):
            for cell in row["cells"]:
                t = cell["text"]
                if not t:
                    continue
                if not loose:
                    p = t.find(find)
                    if p >= 0:
                        if p == 0 and t.strip() == find.strip() and _is_label_fill(find, rep, t):
                            if _fill_empty_below(sh, ri, cell["c"], rep):
                                return True
                            continue
                        _set_cell(cell, t[:p] + rep + t[p + len(find):])
                        return True
                else:
                    m = rx.search(t)
                    if m:
                        if m.start() == 0 and t.strip() == find.strip() and _is_label_fill(find, rep, t):
                            if _fill_empty_below(sh, ri, cell["c"], rep):
                                return True
                            continue
                        _set_cell(cell, t[: m.start()] + rep + t[m.end():])
                        return True
    return False


def _apply_joined(cells, joined, start, end, rep):
    """把拼接串 [start,end) 换成 rep，按原格切回。"""
    spans = []
    pos = 0
    for i, cell in enumerate(cells):
        a = pos
        b = pos + len(cell["text"])
        spans.append((i, a, b))
        pos = b
        if i < len(cells) - 1:
            pos += len(SEP_ROW)
    first = last = None
    for i, a, b in spans:
        if b <= start or a >= end:
            continue
        if first is None:
            first = (i, a, b)
        last = (i, a, b)
    if first is None:
        return False
    fi, fa, fb = first
    li, la, lb = last
    prefix = cells[fi]["text"][: max(0, start - fa)]
    suffix = cells[li]["text"][max(0, end - la):]
    if fi == li:
        _set_cell(cells[fi], prefix + rep + suffix)
        return True
    parts = rep.split(SEP_ROW) if SEP_ROW in rep else [rep]
    span_len = li - fi + 1
    if len(parts) == span_len:
        for j, part in enumerate(parts):
            idx = fi + j
            if j == 0:
                _set_cell(cells[idx], prefix + part)
            elif j == span_len - 1:
                _set_cell(cells[idx], part + suffix)
            else:
                _set_cell(cells[idx], part)
        return True
    if len(parts) > 1 and len(parts) < span_len:
        for j, part in enumerate(parts):
            idx = fi + j
            _set_cell(cells[idx], (prefix + part) if j == 0 else part)
        for idx in range(fi + len(parts), li):
            _set_cell(cells[idx], "")
        _set_cell(cells[li], suffix)
        return True
    _set_cell(cells[fi], prefix + rep)
    for i in range(fi + 1, li):
        if cells[i]["text"]:
            _set_cell(cells[i], "")
    _set_cell(cells[li], suffix)
    return True


def _hit_rows(sheets, find, rep, loose):
    rx = loose_regex(find) if loose else None
    if loose and rx is None:
        return False
    for sh in sheets:
        for row in sh["rows"]:
            cells = row["cells"]
            if not cells:
                continue
            joined = _join_row(cells)
            if not loose:
                p = joined.find(find)
                if p >= 0:
                    return _apply_joined(cells, joined, p, p + len(find), rep)
            else:
                m = rx.search(joined)
                if m:
                    return _apply_joined(cells, joined, m.start(), m.end(), rep)
    return False


def _nearby_groups(row_cells, max_w):
    filled = [c for c in row_cells if c["text"]]
    n = len(filled)
    max_w = min(max_w, n)
    for width in range(2, max_w + 1):
        for s in range(0, n - width + 1):
            yield filled[s : s + width]


def _hit_nearby(sheets, find, rep, loose):
    rx = loose_regex(find) if loose else None
    if loose and rx is None:
        return False
    for sh in sheets:
        for row in sh["rows"]:
            for group in _nearby_groups(row["cells"], MAX_SPAN):
                joined = SEP_ROW.join(c["text"] for c in group)
                if not loose:
                    p = joined.find(find)
                    if p >= 0:
                        return _apply_joined(group, joined, p, p + len(find), rep)
                else:
                    m = rx.search(joined)
                    if m:
                        return _apply_joined(group, joined, m.start(), m.end(), rep)
    return False


def load_openpyxl_sheets(path, keep_vba=False):
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=False, keep_vba=keep_vba)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows():
            raw = []
            any_text = False
            r = None
            for cell in row:
                r = cell.row
                val = cell.value
                t = cell_str(val)
                raw.append({"c": cell.column, "text": t, "dirty": False, "orig": val})
                if t:
                    any_text = True
            if not any_text or r is None:
                continue
            cells = _trim_row(raw)
            if cells:
                rows.append({"r": r, "cells": cells})
        sheets.append({"name": ws.title, "rows": rows, "ws": ws})
    return wb, sheets


def _write_target(ws, row, col, new):
    """合并单元格只有左上角可写。从格清空时跳过，避免 MergedCell.value 只读报错。"""
    cell = ws.cell(row, col)
    if type(cell).__name__ != "MergedCell":
        return cell
    anchor = None
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= row <= rng.max_row and rng.min_col <= col <= rng.max_col:
            anchor = ws.cell(rng.min_row, rng.min_col)
            if row == rng.min_row and col == rng.min_col:
                return anchor
            break
    if anchor is None or new == "":
        return None
    if anchor.value not in (None, ""):
        return None
    return anchor


def write_openpyxl(wb, sheets, out):
    for sh in sheets:
        ws = sh["ws"]
        for row in sh["rows"]:
            for cell in row["cells"]:
                if not cell.get("dirty"):
                    continue
                new = cell["text"]
                target = _write_target(ws, row["r"], cell["c"], new)
                if target is None:
                    continue
                orig = cell.get("orig")
                if type(target).__name__ != "MergedCell" and target.row == row["r"] and target.column == cell["c"]:
                    orig = cell.get("orig")
                else:
                    orig = target.value
                if new == "":
                    target.value = None
                elif isinstance(orig, (int, float)) and not isinstance(orig, bool):
                    try:
                        if "." in new:
                            target.value = float(new)
                        else:
                            target.value = int(new)
                    except ValueError:
                        target.value = new
                else:
                    target.value = new
    wb.save(out)
    wb.close()


def load_xls_sheets(path):
    import xlrd

    book = xlrd.open_workbook(str(path))
    sheets = []
    for si, sheet in enumerate(book.sheets()):
        rows = []
        for r in range(sheet.nrows):
            raw = []
            any_text = False
            for c in range(sheet.ncols):
                t = cell_str(sheet.cell_value(r, c))
                raw.append({"c": c + 1, "text": t, "dirty": False, "orig": sheet.cell_value(r, c)})
                if t:
                    any_text = True
            if not any_text:
                continue
            cells = _trim_row(raw)
            if cells:
                rows.append({"r": r + 1, "cells": cells})
        sheets.append({"name": sheet.name, "rows": rows, "index": si})
    return sheets


def write_xls_com(src, out, sheets):
    try:
        import pythoncom
        import win32com.client
    except ImportError as e:
        raise RuntimeError(
            "写入旧版 .xls 需要本机安装 Microsoft Excel，以及 Python 包 pywin32。"
            "请在当前解释器执行：pip install pywin32"
        ) from e

    mutations = []
    for sh in sheets:
        for row in sh["rows"]:
            for cell in row["cells"]:
                if cell.get("dirty"):
                    mutations.append((sh["index"] + 1, row["r"], cell["c"], cell["text"]))
    if not mutations:
        if os.path.abspath(src) != os.path.abspath(out):
            shutil.copyfile(src, out)
        return

    pythoncom.CoInitialize()
    excel = None
    wb = None
    src_abs = str(os.path.abspath(out if os.path.exists(out) else src))
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Open(src_abs, UpdateLinks=0, ReadOnly=False, AddToMru=False)
        for si, r, c, text in mutations:
            ws = wb.Worksheets(si)
            ws.Cells(r, c).Value = text if text != "" else None
        # 56 = xlExcel8 (.xls)
        out_abs = str(os.path.abspath(out))
        if os.path.abspath(src_abs) == os.path.abspath(out_abs):
            wb.Save()
        else:
            if os.path.exists(out_abs):
                os.remove(out_abs)
            wb.SaveAs(out_abs, FileFormat=56)
        wb.Close(False)
        wb = None
    except Exception as e:
        raise RuntimeError("Excel COM 写入 .xls 失败：" + str(e)[:220]) from e
    finally:
        if wb is not None:
            try:
                wb.Close(False)
            except Exception:
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sniff_excel_kind(path):
    """按文件头判断真实格式。聊天里常把 xlsx 存成 .xls。"""
    ext = os.path.splitext(str(path or ""))[1].lower()
    head = b""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        head = b""
    if head.startswith(b"PK"):
        if ext == ".xlsm":
            return "xlsm"
        try:
            import zipfile
            with zipfile.ZipFile(path) as z:
                names = [str(n or "").replace("\\", "/").lower() for n in z.namelist()]
            if any(n.endswith("xl/vbaProject.bin".lower()) or n.endswith("vbaproject.bin") for n in names):
                return "xlsm"
        except Exception:
            pass
        return "xlsx"
    if head.startswith(_OLE_MAGIC):
        return "xls"
    if ext in (".xlsx", ".xlsm", ".xls"):
        return ext.lstrip(".")
    return ""


def apply_file(src, out, backup, edits):
    src, out, backup = str(src), str(out), str(backup)
    kind = sniff_excel_kind(src)
    shutil.copyfile(src, backup)
    if os.path.abspath(src) != os.path.abspath(out):
        shutil.copyfile(src, out)

    if kind in ("xlsx", "xlsm"):
        wb, sheets = load_openpyxl_sheets(out, keep_vba=(kind == "xlsm"))
        try:
            results = apply_edits_to_sheets(sheets, edits)
            write_openpyxl(wb, sheets, out)
        except Exception:
            try:
                wb.close()
            except Exception:
                pass
            raise
        return results

    if kind == "xls":
        sheets = load_xls_sheets(src)
        results = apply_edits_to_sheets(sheets, edits)
        write_xls_com(src, out, sheets)
        return results

    raise ValueError("不是支持的 Excel 申报书：" + os.path.basename(src))


def main(argv):
    if len(argv) < 5:
        print("usage: apply_excel.py <src.xlsx|xlsm|xls> <out> <backup> <plan.json>", file=sys.stderr)
        return 2
    src, out, backup, plan_path = argv[1], argv[2], argv[3], argv[4]
    plan = json.load(open(plan_path, encoding="utf-8"))
    results = apply_file(src, out, backup, plan.get("edits") or [])
    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
