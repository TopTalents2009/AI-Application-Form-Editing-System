# -*- coding: utf-8 -*-
"""未读反馈修复的回归：批注锚点、编号切分、企业名脱敏、整列覆盖。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.inline_opinions import extract_comment_items, is_column_wide_opinion, count_project_rows
from app.runner import split_source_units, _split_inline_numbered
from app.matcher import split_opinion_blocks, keep_blocks_for_app
from app.edit_validate import (
    is_question_only_opinion, sanitize_declaring_company_edits, _employer_support_span,
    sanitize_paper_author_edits, sanitize_editorial_board_edits,
    is_identity_field_edit, sanitize_identity_edits, retarget_pre_return_title_edits,
)
from app.opinion_sanitize import (
    is_year_template_meta, is_identity_meta_text, strip_year_template_header,
    sanitize_opinion_blocks,
)
from app.hj_form import remap_qm_section


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


def test_keep_corresponding_author_on_same_paper():
    find = (
        "2019-10-08 00:00:00\t免疫情绪素：一种由易引发自身免疫反应的 T 细胞所产生的新的焦虑诱发因子"
        "/Immuno-moodulin: a new anxiogenic factor produced by autoimmune-prone T cells\tbioRxiv\t12*/12\n通讯作者"
    )
    bad = (
        "2020-05-18 00:00:00\tImmuno-moodulin：一种由膜联蛋白-A1转基因自身免疫倾向T细胞产生的新型致焦虑因子"
        "/Immuno-moodulin: a new anxiogenic factor produced by Annexin-A1 transgenic autoimmune-prone T cells"
        "\tBrain, Behavior, and Immunity\t1/1\n第一作者兼通讯作者"
    )
    other = (
        "2008-12-01 00:00:00\t膜联蛋白 A1 和糖皮质激素作为炎症消退的效应因子"
        "/Annexin A1 and glucocorticoids as effectors of the resolution of inflammation"
        "\tNature Reviews Immunology\t2/2\n共同第一作者"
    )
    edits = [
        {"find": find, "replace": bad, "opinionGemini": bad, "section": "专长成果"},
        {"find": "2015-06-05\tMassage-like stroking boosts the immune system in mice\tScientific reports\t6/6", "replace": other},
        {"find": find, "replace": bad, "opinion": "改为第一作者", "clause": "作者位次"},
    ]
    out, issues = sanitize_paper_author_edits(edits)
    rep = out[0]["replace"]
    assert "12*/12" in rep, rep
    assert "第一作者" not in rep, rep
    assert "通讯作者" in rep, rep
    assert "Brain, Behavior, and Immunity" in rep
    assert "12*/12" in out[0]["opinionGemini"]
    assert "第一作者" not in out[0]["opinionGemini"]
    assert "2/2" in out[1]["replace"]
    assert "共同第一作者" in out[1]["replace"]
    assert "1/1" in out[2]["replace"]
    assert issues and "作者位次" in issues[0]
    print("author rank ok", issues[0])


def test_editorial_board_is_stable():
    app = (
        "4.其他（包括在国际学术会议做重要报告等情况）\n"
        "Others(including lectures delivered at international academic conferences)\n"
    )
    pool = "申报人策划并主编神经元与免疫细胞双向交流研究专题，系统梳理其作用。"
    clauses = [{
        "cid": "S2",
        "section": "专长成果",
        "clause": "补充列出核心期刊编委任职",
        "opinion": "2.科研情况及代表性成果中--核心期刊编委要列出;",
    }]
    invented = (
        "4.其他（包括在国际学术会议做重要报告等情况）\n"
        "Others(including lectures delivered at international academic conferences)\n"
        "学术兼职与核心期刊编委：担任《Frontiers in Immunology》专刊主编，长期担任特约审稿专家。"
    )
    edits = [{
        "find": app.strip(),
        "replace": invented,
        "opinionGemini": invented,
        "clauseId": "S2",
        "opinion": clauses[0]["opinion"],
        "clause": clauses[0]["clause"],
    }]
    leftovers = [
        "【Gemini·专长成果】[S2] 补充列出核心期刊编委任职：请人工核实",
        "【Gemini·专长成果】【意见未覆盖】S2 补充列出核心期刊编委任职：2.科研情况及代表性成果中--核心期刊编委要列出;",
        "【Gemini·其他】[S4] 补充提供承担项目的证明材料",
    ]
    out, issues, lo = sanitize_editorial_board_edits(edits, clauses, app, pool, leftovers)
    assert len(out) == 1, out
    assert "策划并主编神经元与免疫细胞双向交流研究专题" in out[0]["replace"]
    assert "审稿" not in out[0]["replace"]
    assert "Frontiers" not in out[0]["replace"]
    assert out[0]["clauseId"] == "S2"
    assert all("S2" not in str(x) for x in lo)
    assert any("承担项目" in str(x) for x in lo)
    assert issues
    again, _, _ = sanitize_editorial_board_edits(out, clauses, app, pool, lo)
    assert again[0]["replace"] == out[0]["replace"]
    print("editorial board ok")


def test_split_multi_person_summary_by_id():
    raw = (
        "40302\n"
        "突出高水平期刊与影响因子：“申报人累计发表论文14篇”处，需重点突出知名顶刊名称及高影响因子（IF）等量化表现。\n"
        "强化推荐理由的人企契合度：结合企业的具体技术瓶颈或战略发展方向，深入阐述申报人技术服务于企业解决难题的匹配度。\n"
        "补充近五年项目成果：补充提供近五年内产出的关键科研/工程项目成果。\n"
        "30708\n"
        "强化推荐理由的人企契合度：紧密结合企业技术瓶颈与战略方向，突出申报人技术与企业攻坚方向的契合性。\n"
        "补齐关键算法佐证材料：针对个人基本情况中提及的“响应曲面优化置信区域算法”，补充包含该技术的项目、论文等直接佐证材料。\n"
        "补充近五年第一负责人项目：补充近五年内申报人作为第一责任人主持/负责的重大项目。\n"
        "49547\n"
        "重写个人基本情况（300字）：彻底放弃简历式成就罗列，采用故事性、价值导向的方式重新撰写。\n"
        "共性优化要求\n"
        "人企匹配导向：推荐理由及正文描述需全面贴近申报企业的业务方向与战略需求。\n"
        "强化产业化落地：充分挖掘工作经历与业绩材料中的转化效益与应用实效。"
    )
    blocks = split_opinion_blocks(raw)
    heads = [blk.split("\n", 1)[0].strip() for blk in blocks]
    assert "30708" in heads, heads
    assert "40302" in heads, heads
    assert any("共性" in h for h in heads), heads
    kept, notes = keep_blocks_for_app(blocks, "30708")
    blob = "\n".join(kept)
    assert "响应曲面优化置信区域算法" in blob
    assert "第一责任人" in blob
    assert "累计发表论文14篇" not in blob
    assert "彻底放弃简历式" not in blob
    assert "人企匹配导向" in blob
    assert notes
    units = split_source_units(raw, app_no="30708")
    assert len(units) >= 3, units
    joined = "\n".join(units)
    assert "响应曲面优化置信区域算法" in joined
    assert "第一责任人" in joined
    assert "累计发表论文14篇" not in joined
    print("multi-person summary ok", len(units), notes)


def test_project_paper_opinion_split():
    from app.attachments import extract_needed_kinds, format_attach_prompt, catalog_from_attach_items
    from app.runner import expand_qm_paper_project_clauses

    op = "修改意见：请补充这个人才的项目论文材料，并补充这两个模块的的申报书文字内容"
    labels = [k["label"] for k in extract_needed_kinds([op])]
    assert "论文全文" in labels, labels
    assert "项目证明" in labels, labels
    clauses = [{
        "cid": "S1", "sourceId": "S1", "section": "项目",
        "clause": "补充项目模块", "opinion": op,
    }]
    out = expand_qm_paper_project_clauses(clauses)
    secs = {c["section"] for c in out}
    assert "论文" in secs and "项目" in secs, out
    prompt = format_attach_prompt({
        "needed": ["论文全文"],
        "items": [{"kind": "论文全文", "title": "Demo", "filename": "p001.pdf", "download": "/x"}],
        "paperRecords": [{"title": "Demo Paper", "journal": "Nature", "year": "2016", "doi": "10.1/x"}],
        "notes": [],
    })
    assert "论文系统题录" in prompt and "Demo Paper" in prompt
    recs = catalog_from_attach_items([
        {"kind": "论文全文", "title": "复数躁狂游戏增强知识", "filename": "p001.pdf"},
        {"kind": "论文全文", "title": "p001.pdf", "filename": "p001.pdf"},
        {"kind": "论文装订附件", "title": "装订附件 48884.pdf", "filename": "x.pdf"},
    ])
    assert recs and recs[0]["title"] == "复数躁狂游戏增强知识", recs
    print("project+paper opinion split ok", labels)


def test_year_template_header_not_identity_edit():
    header = (
        "【26年 QM → 27年模板修改意见】\n"
        "申报人：PHAM BAO TRUNG\n"
        "申报 ID：42413\n"
        "库内 id：126"
    )
    birth = (
        "出生日期：1986-02-13\n"
        "模板：QM\n"
        "来源：26年\n"
        "请按下列条款修改申报书原文；「修改前」用于定位，「修改后」为写入内容。"
    )
    real = "1. 【申报类型】\n   修改前：青年人才\n   修改后：创新人才"
    proof = "2. 出生年月在1987年1月1日以前的，同步将职位修改为副高级以上职位（与申报书「回国前职务」一致）。"
    assert is_year_template_meta(header)
    assert is_year_template_meta(birth)
    assert is_identity_meta_text("申报人出生日期（1986-02-13）及模板修改指引")
    assert is_identity_meta_text("更正封面申报人姓名")
    assert not is_year_template_meta(real)
    assert not is_year_template_meta(proof)
    assert not is_identity_meta_text(real)
    mixed = header + "\n" + real
    assert "创新人才" in strip_year_template_header(mixed)
    assert "PHAM BAO TRUNG" not in strip_year_template_header(mixed)
    blocks, notes = sanitize_opinion_blocks([
        {"id": "S1", "name": "42413_修改意见.txt", "text": header},
        {"id": "S2", "name": "42413_修改意见.txt", "text": birth},
        {"id": "S3", "name": "42413_修改意见.txt", "text": real},
        {"id": "S4", "name": "42413_修改意见.txt", "text": proof},
    ])
    texts = [b["text"] for b in blocks]
    assert real in texts
    assert proof in texts
    assert all("PHAM BAO TRUNG" not in t or "修改前" in t for t in texts)
    assert any("出生日期" in n and "不改" in n for n in notes)
    edits = [
        {
            "find": "申 报 人    ANGELOPOULOS IOANNIS",
            "replace": "申 报 人    PHAM BAO TRUNG",
            "clause": "更正封面申报人姓名",
            "opinion": header,
        },
        {
            "find": "有效证件姓名\nANGELOPOULOS IOANNIS",
            "replace": "有效证件姓名\nPHAM BAO TRUNG",
            "clause": "更正有效证件姓名",
            "opinion": header,
        },
        {
            "find": "1984.07.30",
            "replace": "1986-02-13",
            "clause": "核准出生日期为1986-02-13",
            "opinion": birth,
        },
        {
            "find": "青年人才",
            "replace": "创新人才",
            "clause": "申报类型由青年人才修改为创新人才",
            "opinion": real,
        },
        {
            "find": "功率芯片应用工程师",
            "replace": "首席功率芯片应用工程师",
            "clause": "回国前职务修改为首席功率芯片应用工程师",
            "opinion": "4. 【回国前职务】\n   修改前：功率芯片应用工程师\n   修改后：首席功率芯片应用工程师",
        },
    ]
    app = (
        "申 报 人    ANGELOPOULOS IOANNIS\n"
        "有效证件姓名\nANGELOPOULOS IOANNIS\n性别\n1986-02-13\n越南\n1984.07.30\n国籍地区\n希腊\n"
    )
    assert is_identity_field_edit(edits[0], app)
    assert is_identity_field_edit(edits[1], app)
    assert is_identity_field_edit(edits[2], app)
    assert is_identity_field_edit({
        "find": "ANGELOPOULOS IOANNIS",
        "replace": "PHAM BAO TRUNG",
        "clause": "申报人及项目ID基础信息说明",
        "opinion": header,
    }, app)
    assert is_identity_field_edit({
        "find": "1984.07.30",
        "replace": "1986-02-13",
        "clause": "申报人出生日期（1986-02-13）及模板修改指引",
        "opinion": birth,
    }, app)
    assert not is_identity_field_edit(edits[3], app)
    assert not is_identity_field_edit(edits[4], app)
    out, issues = sanitize_identity_edits(edits, app)
    assert len(out) == 2, [e.get("clause") for e in out]
    assert {e["replace"] for e in out} == {"创新人才", "首席功率芯片应用工程师"}
    assert any("身份栏不可改" in x for x in issues)
    raw = (
        "【26年 QM → 27年模板修改意见】\n"
        "申报人：KRISTEN NICOLE PARRISH\n"
        "申报 ID：54578\n"
        "库内 id：829\n"
        "出生日期：1986-01-01\n"
        "模板：QM\n"
        "来源：26年\n\n"
        "请按下列条款修改申报书原文；「修改前」用于定位，「修改后」为写入内容。\n\n"
        "1. 【申报类型】\n"
        "   修改前：青年人才\n"
        "   修改后：创新人才\n\n"
        "2. 【回国前职务中文】\n"
        "   修改前：功率芯片应用工程师\n"
        "   修改后：首席功率芯片应用工程师\n"
    )
    units = split_source_units(raw)
    cleaned, _ = sanitize_opinion_blocks([{"id": "S" + str(i), "name": "op.txt", "text": u} for i, u in enumerate(units, 1)])
    cleaned_txt = "\n".join(b["text"] for b in cleaned)
    assert "创新人才" in cleaned_txt
    assert "首席功率芯片应用工程师" in cleaned_txt
    assert not any(is_year_template_meta(b["text"]) for b in cleaned)
    print("year-template identity guard ok", len(out), issues)


def test_pre_return_title_not_domestic_equiv():
    from app.manual_fill import locate_anchor, locate_pre_return_title

    app = (
        "现工作单位\n亚里士多德大学\n"
        "回国(来华)前职务\nAssociate Professor\n副教授\n"
        "相当于国内职务情况\n引进职务\n技术总监\n"
    )
    zh, hint = locate_anchor(app, "", opinion="2. 【回国前职务中文】\n   修改前：研究员\n   修改后：首席研究员")
    assert "回国(来华)前职务" in zh, zh
    assert "相当于国内职务情况" not in zh, zh
    assert "副教授" in zh, zh
    en, _ = locate_anchor(app, "", opinion="3. 【回国前职务英文】\n   修改前：Researcher\n   修改后：Chief Researcher")
    assert "Associate Professor" in en, en
    assert "相当于国内职务情况" not in en, en
    hit, _ = locate_pre_return_title(app, "zh")
    assert hit.startswith("回国(来华)前职务"), hit
    wrong = [{
        "find": "副教授\n相当于国内职务情况",
        "replace": "首席研究员\n相当于国内职务情况",
        "clause": "回国前职务中文修改为首席研究员",
        "opinion": "2. 【回国前职务中文】\n   修改前：研究员\n   修改后：首席研究员",
    }]
    fixed, issues = retarget_pre_return_title_edits(wrong, app)
    assert len(fixed) == 1
    assert "相当于国内职务情况" not in fixed[0]["find"], fixed[0]
    assert "回国(来华)前职务" in fixed[0]["find"]
    assert "首席研究员" in fixed[0]["replace"]
    assert any("回国前职务定位" in x for x in issues)
    print("pre-return title locate ok", fixed[0]["find"].replace("\n", "|"))


if __name__ == "__main__":
    test_word_point_comment_anchors()
    test_split_wecom_screenshot_ocr()
    test_question_unit_and_remap()
    test_company_sanitize_and_support_span()
    test_project_row_count()
    test_short_comment_expands_to_paragraph()
    test_locate_conversion_not_tech_field()
    test_keep_corresponding_author_on_same_paper()
    test_editorial_board_is_stable()
    test_split_multi_person_summary_by_id()
    test_project_paper_opinion_split()
    test_year_template_header_not_identity_edit()
    test_pre_return_title_not_domestic_equiv()
    print("ALL OK")
