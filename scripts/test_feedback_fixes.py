# -*- coding: utf-8 -*-
"""未读反馈修复的回归：批注锚点、编号切分、企业名脱敏、整列覆盖。"""
from pathlib import Path

from app.inline_opinions import extract_comment_items, is_column_wide_opinion, count_project_rows
from app.runner import split_source_units, _split_inline_numbered
from app.edit_validate import (
    is_question_only_opinion, sanitize_declaring_company_edits, _employer_support_span,
)
from app.hj_form import remap_qm_section

ROOT = Path(__file__).resolve().parent.parent


def test_word_point_comment_anchors():
    p = ROOT / "tasks" / "1a0bd6ec56f-d75c3a" / "input" / "冶金园-BAL MUKUND DHAR-修订.docx"
    if not p.exists():
        print("skip word anchors: file missing")
        return
    items = extract_comment_items(p)
    assert len(items) >= 9, items
    with_anchor = [it for it in items if it.get("anchor")]
    assert len(with_anchor) >= 8, [it.get("text") for it in items]
    col = [it for it in items if it.get("scope") == "column" or is_column_wide_opinion(it.get("text") or "", it.get("anchor") or "")]
    assert col, "column-wide comment not detected"
    print("word anchors ok", len(items), "column", [c.get("text")[:20] for c in col])


def test_split_wecom_screenshot_ocr():
    raw = (
        "文秀，鲁汉吉兹博士的材料，我们找了省专家看了，提了几个意见："
        "1.个人贡献部分披露产业化转化情况，增加经济效益方面、量产。。收入。。的表述（加分项，qm比较重视这个）；"
        "2.年薪调高一点，150万；"
        "3.专家表示一直在高校工作，论文不会这么少，咱们再核一下，看看有没有论文补充；"
        "4.三年计划，可行性那里写了申报人研发基础的表述，但缺少论据（实现了什么产业化目标），"
        "5.申报人支持条件：建议学习什维塔那份，写的细一点，增加人员配置，设备安排，产业线供给方面表述；"
        "6.推荐理由方面：增加企业与海外的关联度，提升对外籍人才引进的可能性；"
        "7. “终身副教授”改为“副教授”因为会影响人才入职企业的真实性。"
    )
    units = split_source_units(raw)
    assert len(units) >= 7, units
    blob = "\n".join(units)
    assert "年薪调高一点" in blob
    assert "终身副教授" in blob
    assert "申报人支持条件" in blob
    print("split numbered ok", len(units))


def test_question_unit_and_remap():
    assert is_question_only_opinion("3. 单位是？") is False
    assert is_question_only_opinion("国外助理教授相当于国内副教授？") is True
    sec = remap_qm_section("其他", "个人贡献", "1.个人贡献部分披露产业化转化情况，增加经济效益")
    assert sec == "项目", sec
    sec2 = remap_qm_section("其他", "年薪", "2.年薪调高一点，150万")
    assert sec2 == "其他", sec2
    sec3 = remap_qm_section("其他", "职务", "7. “终身副教授”改为“副教授”")
    assert sec3 == "工作", sec3
    print("question/remap ok")


def test_company_sanitize_and_support_span():
    app = (
        "申报企业：江苏晶华新材料科技有限公司\n"
        "拟提供申报人支持条件\n(300字以内)\n①工作环境 xxx\n"
        "7-2企业荣誉和资质\n"
        "申报人推荐理由\n双方前期在欧洲已有深入对接。\n"
    )
    edits = [
        {
            "find": "该成果以产业化应用落地",
            "replace": "该成果依托江苏晶华新材料科技有限公司推进转化。目前已完成中试并实现工业级批量稳定量产。双方前期已有深入对接，具备良好的合作互信基础。",
            "section": "项目",
        }
    ]
    out, issues = sanitize_declaring_company_edits(edits, app, ["江苏晶华新材料科技有限公司"])
    rep = out[0]["replace"]
    assert "江苏晶华" not in rep, rep
    assert "合作互信基础" not in rep, rep
    assert "已完成中试" in rep
    span = _employer_support_span(app)
    assert "工作环境" in span
    assert "推荐理由" not in span
    print("sanitize/span ok", issues)


def test_project_row_count():
    p = ROOT / "tasks" / "1a0bd6ec56f-d75c3a" / "work" / "txt" / "冶金园-BAL MUKUND DHAR-修订.txt"
    if not p.exists():
        print("skip row count")
        return
    n = count_project_rows(p.read_text(encoding="utf-8"))
    assert n >= 3, n
    print("project rows", n)


def test_short_comment_expands_to_paragraph():
    p = ROOT / "tasks" / "1a0bd82a7a4-1b74eb" / "input" / "冶金园-KATERINA GENSILA MALOLLARI.docx"
    if not p.exists():
        print("skip short comment: file missing")
        return
    items = extract_comment_items(p)
    conv = next(it for it in items if "依托哪些公司转化" in (it.get("text") or ""))
    assert "量产" in (conv.get("anchor") or ""), conv.get("anchor")
    assert (conv.get("anchor") or "") != "产"
    prod = next(it for it in items if "产品名称和应用推广" in (it.get("text") or ""))
    assert "16MPa" in (prod.get("anchor") or "")
    assert len(prod.get("anchor") or "") > 8
    print("short comment expand ok", conv.get("anchor")[:24], prod.get("anchor")[:24])


def test_locate_conversion_not_tech_field():
    from app.manual_fill import locate_anchor, extract_anchor, promote_unknown_to_manual_edits

    txt = (ROOT / "tasks" / "1a0bd82a7a4-1b74eb" / "work" / "txt" / "冶金园-KATERINA GENSILA MALOLLARI.txt").read_text(encoding="utf-8")
    op = "3. 建议给出具体的产品信息，依托哪些公司转化等信息\n标注原文：产"
    find, hint = locate_anchor(txt, extract_anchor(op), section="项目", opinion=op)
    assert "所属技术领域" not in find, find
    assert "量产" in find or "成果转化" in hint, (find, hint)
    find2, hint2 = locate_anchor(txt, "16MPa", section="项目", opinion="建议给出具体产品名称和应用推广情况")
    assert "16MPa" in find2
    assert len(find2) > 8, find2
    clauses = [
        {"cid": "S2", "section": "项目", "clause": "补充产品", "opinion": "建议给出具体产品名称和应用推广情况\n标注原文：16MPa"},
        {"cid": "S3", "section": "项目", "clause": "转化公司", "opinion": op},
    ]
    leftovers = [
        "【Gemini·项目】[S3] 建议给出具体的产品信息：人才库及申报材料中未提供具体合作转化单位名称，需由申报人核实",
        "【Gemini·项目】[S2] 未提供该高强度结构胶粘剂的具体商业产品型号",
    ]
    edits, lo, n = promote_unknown_to_manual_edits(clauses, [], leftovers, txt)
    assert n == 0, (n, edits)
    assert not any(e.get("manualFill") for e in edits)
    print("locate conversion ok", find[:40], "hint", hint)


if __name__ == "__main__":
    test_word_point_comment_anchors()
    test_split_wecom_screenshot_ocr()
    test_question_unit_and_remap()
    test_company_sanitize_and_support_span()
    test_project_row_count()
    test_short_comment_expands_to_paragraph()
    test_locate_conversion_not_tech_field()
    print("ALL OK")
