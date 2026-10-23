"""项目证明补缺链路本地测试：识别 → 参数抽取 → CodeBuddy → 生成 API。"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import project_proof as PP
from app.attachments import extract_needed_kinds, leftover_lines, resolve_missing


def test_detect_and_extract() -> None:
    opinion = "请补充提供主持科研项目的立项批文扫描件，严禁用论文代替项目证明。"
    needed = extract_needed_kinds([opinion])
    labels = [k["label"] for k in needed]
    assert "项目证明" in labels, labels
    snap = {
        "talent": {
            "name": "Test User",
            "payload": {
                "科研项目": [
                    {
                        "项目名称": "WorDepth monocular depth estimation framework",
                        "项目来源": "(国家自然科学基金)",
                        "起止时间": "2020-2024",
                    }
                ]
            },
        },
        "keys": {"names": ["Test User"], "company": "Test University"},
    }
    projects = PP.extract_projects(snap, "")
    person = PP.person_name(snap)
    print("[OK] 意见识别:", labels)
    print("[OK] 申报人:", person)
    print("[OK] 项目列表:", json.dumps(projects, ensure_ascii=False))
    return person, projects, snap


def test_parse() -> None:
    raw = '{"found":true,"items":[{"title":"NSF Award","url":"https://example.gov/award.pdf","filename":"","note":"test"}]}'
    items = PP._parse_items(raw)
    assert len(items) == 1 and items[0]["url"].startswith("https://"), items
    print("[OK] JSON 解析:", items[0]["url"])


def test_prompt_and_filter() -> None:
    prompt = PP.build_search_prompt(
        "Test User",
        "TEST001",
        [{"name": "WorDepth monocular depth estimation framework", "项目来源": "(国家自然科学基金)"}],
    )
    for needle in ("不是论文", "GitHub", "立项批文", "found=false", "NSF Award Search", "不要只搜论文标题"):
        assert needle in prompt, "prompt 缺少约束：" + needle
    print("[OK] prompt 含论文/代码排除与检索优先级")

    reject = [
        ("https://arxiv.org/pdf/2404.03635", "WorDepth arXiv"),
        ("https://openaccess.thecvf.com/content/CVPR2024/html/Zeng_WorDepth.html", "CVPR 2024 Open Access"),
        ("https://github.com/Adonis-galaxy/WorDepth", "GitHub"),
        ("https://ieeexplore.ieee.org/document/10658211", "IEEE Xplore"),
        ("https://scholar.google.com/scholar?q=WorDepth", "Google Scholar"),
    ]
    for url, title in reject:
        got = PP._usable_url(url, title)
        assert not got, "应过滤论文/代码链接：" + url + " -> " + got
    print("[OK] 已过滤 arXiv/CVF/GitHub/IEEE/Scholar")

    keep = [
        "https://www.nsf.gov/awardsearch/showAward?AWD_ID=1234567",
        "https://gtr.ukri.org/projects?ref=EP/X012345/1",
        "https://cordis.europa.eu/project/id/101012345",
        "https://reporter.nih.gov/project-details/12345678",
    ]
    for url in keep:
        got = PP._usable_url(url, "Official award page")
        assert got == url, "应保留官方资助页：" + url
    print("[OK] 保留 NSF/UKRI/CORDIS/NIH 官方页")


def test_autoref_contract() -> None:
    letters = PP._build_autoref_letters(
        "Ada Lovelace", "DTU",
        [{"name": "Demo Grant", "经费总额": "4,238,095 DKK"}],
        "abc123",
    )
    assert isinstance(letters, list) and letters[0]["type"] == "work"
    assert letters[0]["candidateName"] == "Ada Lovelace"
    assert letters[0]["customProjects"][0]["name"] == "Demo Grant"
    assert letters[0]["projects"][0]["name"] == "Demo Grant"
    content = str(letters[0].get("letterContent") or "")
    assert "Demo Grant" in content
    assert "hosted or participated" in content.lower()
    assert "employer prior" not in content.lower()
    assert "Funding / source" not in content
    assert "Human Resources" not in content
    body = PP._build_autoref_body("Ada Lovelace", "DTU", [{"name": "Demo Grant"}], "abc123")
    assert "letters" in body and isinstance(body["letters"], list)
    print("[OK] documents 请求体为 {letters: [...]}（已有经历、不传简历）")

    tmp = ROOT / "tasks" / "_project_proof_live_test" / "parse"
    tmp.mkdir(parents=True, exist_ok=True)
    sample = {
        "ok": True,
        "candidateName": "Ada Lovelace",
        "letterCount": 1,
        "documentCount": 1,
        "letters": [{"id": "abc123", "type": "work", "companyName": "DTU", "projects": [{"name": "Demo Grant"}], "customProjects": []}],
        "documents": [{
            "kind": "project.certificate",
            "titleZh": "项目证明",
            "letterId": "abc123",
            "fields": {"content": "This certifies Demo Grant.", "projectName": "Demo Grant"},
        }],
    }
    saved, err = PP._save_autoref_documents(sample, tmp)
    assert not err and saved, err
    assert saved[0]["filename"].endswith(".html")
    html = (tmp / saved[0]["filename"]).read_text(encoding="utf-8")
    assert "Demo Grant" in html
    print("[OK] 解析 documents[]（无 html 时用 fields.content 落 HTML）")

    fail = {"ok": False, "error": "原因"}
    saved2, err2 = PP._save_autoref_documents(fail, tmp)
    assert not saved2 and "原因" in err2
    print("[OK] ok=false 时中止")


def _boeing_snap() -> dict:
    return {
        "keys": {"company": "苏州启明智造科技有限公司", "names": ["林启明"], "attachIds": ["51104"]},
        "talent": {
            "name": "KEITH DANIEL HUMFELD",
            "payload": {
                "申报人基本信息": {
                    "有效证件姓名": "KEITH DANIEL HUMFELD",
                    "外籍专家中文姓名": "基思•丹尼尔•汉弗尔德",
                    "回国前单位中文": "波音公司",
                    "回国前单位英文": "BOEING",
                    "现工作单位": "波音公司",
                    "回国前所在地": "美国",
                    "回国前职务中文": "首席科学家",
                    "回国前职务英文": "Chief Scientist",
                },
                "主要学历": [
                    {
                        "校名称": "University of Washington",
                        "学位": "博士",
                        "开始时间": "1997-09-01",
                        "结束时间": "2001-12-31",
                        "所在国家": "美国",
                        "是否全日制": "是",
                    }
                ],
                "工作经历": [{
                    "工作单位": "波音公司 / BOEING",
                    "担任职务": "首席科学家 / Chief Scientist",
                    "开始时间": "2008-06-01",
                    "结束时间": "至今",
                    "所在国家": "美国",
                    "是否为回国前最后一段工作经历": "是",
                }],
                "工作成果及业绩": [
                    {
                        "项目名称": "中文课题 / MACHINE LEARNING COMPOSITES CONTROL",
                        "项目来源": "波音公司",
                        "开始时间": "2020-02-13",
                        "结束时间": "2021-06-20",
                        "完成人排序": "1/2",
                        "经费总额": "210",
                    },
                    {
                        "项目名称": "Overlap PI / Real-time inverse modeling",
                        "项目来源": "波音公司",
                        "开始时间": "2020-01-19",
                        "结束时间": "2021-09-14",
                        "完成人排序": "1/5",
                    },
                    {
                        "项目名称": "PINN curing / Co-training of PINNs",
                        "项目来源": "美国能源部",
                        "开始时间": "2023-04-15",
                        "结束时间": "2025-05-13",
                        "完成人排序": "1/8",
                    },
                    {
                        "项目名称": "Colloidal Particles / Synthesis Photonic",
                        "项目来源": "美国国防部",
                        "开始时间": "2000-02-13",
                        "结束时间": "2001-07-20",
                        "完成人排序": "3/5",
                    },
                    {
                        "项目名称": "汽车零部件在线视觉检测系统",
                        "项目来源": "苏州启明智造科技有限公司",
                    },
                ],
            },
        },
    }


def test_prior_from_hj_app_when_pool_empty() -> None:
    """反馈#43：人才库失败时仍从 HJ 申报书抽来华前单位并生成项目清单。"""
    app_path = ROOT / "tasks" / "1a1615218f7-afb774" / "work" / "txt" / "1-【HJ】42616-2026申报书.txt"
    if app_path.is_file():
        app_text = app_path.read_text(encoding="utf-8")
    else:
        app_text = (
            "*\t申报人姓名(中文名)\t\t\t富尔维奥·达基斯托\n"
            "*\tName of Applicant\t\t\tFULVIO D'ACQUISTO\n"
            "依托单位名称\t\t常州恒邦药业有限公司\t\t请输入依托单位全称\n"
            "回国（来华）前所在国家（地区）：英国\n"
            "回国（来华）前工作单位（英文）：罗汉普顿大学\n"
            "回国（来华）前职务（中文）：教授\n"
            "Former Employer (English)：University of Roehampton\n"
            "Former Position (English)：Professor\n"
            "2.代表性科研项目(主持/参与)\n"
            "2016-03-01 00:00:00\t\t2017-03-05 00:00:00\t\t项目性质：基础研究\n"
            "项目来源：医学研究理事会\t\t276\t\t6/6\t\t具体职务：项目主持人\n"
            "任务：申报人负责环境丰富化（EE）对小鼠炎症免疫反应影响的总体实验构思，确立研究框架。\n"
        )
    snap = {"talent": None, "keys": {"names": ["(中文名) 富尔维奥·达基斯托"], "company": "始终锚定以原始创新引领。当下，全球医药市场。", "attachIds": ["42616"]}}
    ident = PP.identity_from_app_text(app_text)
    assert "恒邦" in (ident.get("company") or ""), ident
    assert "ACQUISTO" in (ident.get("name") or ident.get("name_en") or "").upper(), ident
    ctx = PP.prior_work_context(snap, app_text)
    assert ctx.get("ok"), ctx
    assert "Roehampton" in (ctx.get("company") or "") or "罗汉普顿" in (ctx.get("company") or ""), ctx
    assert "恒邦" not in (ctx.get("company") or "")
    assert "ACQUISTO" in str(ctx.get("person") or "").upper(), ctx.get("person")
    assert ctx.get("projects"), ctx
    names = " ".join(str(p.get("name") or "") for p in ctx["projects"])
    assert "环境丰富化（EE）" in names, names
    roles = " ".join(str(p.get("担任角色") or p.get("role") or "") for p in ctx["projects"])
    assert "主持" in roles, roles
    forbidden = ctx.get("forbidden") or []
    assert any("恒邦" in str(x) for x in forbidden), forbidden
    print("[OK] 空人才库仍从来华前栏位签发", ctx.get("company"), "projects=", len(ctx["projects"]))


def test_feedback_project_sanitize() -> None:
    """回归：同阶段双负责人、博士期高职级、汇总含项目来源、签发日不早于项目。"""
    app_text = "申报企业：苏州启明智造科技有限公司"
    ctx = PP.prior_work_context(_boeing_snap(), app_text)
    assert ctx["ok"], ctx
    projects = ctx["projects"]
    assert projects, projects

    by_name = {str(p.get("name") or "").upper(): p for p in projects}
    # 博士期间项目不得保留首席/负责人
    phd = next(p for p in projects if "PHOTONIC" in str(p.get("name") or "").upper() or "COLLOIDAL" in str(p.get("name") or "").upper())
    role = str(phd.get("担任角色") or phd.get("role") or "")
    assert "Chief" not in role and "首席" not in role and "负责人" not in role, phd
    assert "Participant" in role or "参与" in role, phd

    # 同阶段仅保留一个负责人
    leads = []
    for p in projects:
        r = str(p.get("担任角色") or p.get("role") or "")
        if "Principal" in r or "负责人" in r:
            s = PP._parse_ym(p.get("开始时间") or p.get("startDate") or "")
            e = PP._parse_ym(p.get("结束时间") or p.get("endDate") or "")
            leads.append((PP._ym_key(s), PP._ym_key(e, end=True), str(p.get("name"))))
    for i, (s0, e0, n0) in enumerate(leads):
        for s1, e1, n1 in leads[i + 1:]:
            assert not PP._ranges_overlap(s0, e0, s1, e1), (n0, n1)

    letters = PP._build_autoref_letters(
        ctx["person"], ctx["company"], projects, "51104",
        role=ctx["role"], start_date=ctx["startDate"], end_date=ctx["endDate"],
        language=ctx["language"], education=ctx.get("education") or [],
    )
    body = str(letters[0].get("letterContent") or "")
    assert "Funding source:" in body, body
    assert "Chief Scientist" not in body, body
    issue = str(letters[0].get("issueDate") or letters[0].get("endDate") or "")
    assert issue >= "2023-04", (issue, letters[0])

    html = (
        '<html lang="en"><body>'
        '<div style="font-size:14px;line-height:1.7;min-height:120mm">OLD</div>'
        '<div style="font-size:11px;color:#6b7280;margin-top:8px">July 15, 2021</div>'
        "</body></html>"
    )
    doc = {"kind": "project.all", "titleEn": "All Projects", "html": html, "fields": {}}
    out = PP._render_project_html(
        doc, language="en", issuer=ctx["company"], person=ctx["person"],
        projects=projects, default_role=ctx["role"],
    )
    assert "Funding source:" in out, out
    assert "July 15, 2021" not in out, out
    foot = re.search(r'margin-top:\s*8px[^>]*>([^<]+)<', out)
    assert foot and foot.group(1).strip() >= "2023-04", out
    assert "Chief Scientist" not in out, out
    print("[OK] 反馈回归：双负责人/博士期角色/项目来源/签发日")


def test_prior_employer_and_html() -> None:
    app_text = "申报人姓名：林启明\n申报企业：苏州启明智造科技有限公司\n项目1：汽车零部件在线视觉检测系统。项目来源：企业自筹。"
    ctx = PP.prior_work_context(_boeing_snap(), app_text)
    assert ctx["ok"], ctx
    assert ctx["language"] == "en", ctx
    assert "Boeing" in ctx["company"], ctx["company"]
    assert "苏州" not in ctx["company"]
    assert "基思" not in ctx["person"]
    assert ctx["role"] == "Chief Scientist"
    names = [str(p.get("name") or "") for p in ctx["projects"]]
    assert names, names
    assert all("汽车零部件" not in n for n in names), names
    assert any("MACHINE LEARNING" in n.upper() for n in names), names
    letters = PP._build_autoref_letters(
        ctx["person"], ctx["company"], ctx["projects"], "51104",
        role=ctx["role"], start_date=ctx["startDate"], end_date=ctx["endDate"], language=ctx["language"],
    )
    assert letters[0]["companyName"] == ctx["company"]
    assert "苏州" not in json.dumps(letters, ensure_ascii=False)
    letter_body = str(letters[0].get("letterContent") or "")
    assert "hosted or participated" in letter_body.lower()
    assert "employer prior" not in letter_body.lower()
    assert "host company of the application" not in letter_body.lower()
    assert "Funding / source" not in letter_body
    if "Funding" in letter_body:
        assert "Funding source:" in letter_body, letter_body
    zh_body = PP._project_certificate_text(
        person="林启明", company="某某大学",
        project={"name": "示范项目", "funding": "国家自然科学基金"}, language="zh",
    )
    assert "主持或参与" in zh_body
    assert "来华前" not in zh_body
    assert "非申报" not in zh_body
    mixed = (
        '<html lang="en"><body>'
        '<div>苏州启明智造科技有限公司</div>'
        '<div>Project Certificate</div>'
        '<div>项目证明</div>'
        '<div>KEITH served at 苏州启明智造科技有限公司</div>'
        "</body></html>"
    )
    html = PP.unify_html_language(
        mixed, "en", issuer=ctx["company"],
        forbidden_names=ctx["forbidden"], aliases=ctx["aliases"],
    )
    assert "项目证明" not in html, html
    assert "苏州启明" not in html, html
    assert "The Boeing Company" in html
    skipped = PP.prior_work_context(
        {"keys": {"company": "Demo Co"}, "talent": {"name": "A", "payload": {}}},
        "申报企业：Demo Co",
    )
    assert not skipped["ok"]

    tmp = ROOT / "tasks" / "_project_proof_live_test" / "parse"
    tmp.mkdir(parents=True, exist_ok=True)
    employ = (
        '<html lang="en"><body>'
        '<div>苏州启明智造科技有限公司</div>'
        '<div>Project Certificate</div>'
        '<div style="font-size:12px">项目证明</div>'
        '<div style="font-size:14px;line-height:1.7;min-height:120mm">'
        "KEITH DANIEL HUMFELD served as Researcher at 苏州启明智造科技有限公司 "
        "from 2020-02-13 to 2021-06-20.<br/><br/>"
        "During this period, KEITH DANIEL HUMFELD fulfilled the responsibilities of the role "
        "with professionalism and reliability, and completed the assigned duties in good standing."
        "</div></body></html>"
    )
    sample = {
        "ok": True,
        "documents": [{
            "kind": "project.certificate",
            "titleEn": "Project Certificate",
            "titleZh": "项目证明",
            "html": employ,
            "fields": {"projectName": "MACHINE LEARNING COMPOSITES CONTROL"},
        }],
    }
    saved, err = PP._save_autoref_documents(
        sample, tmp,
        language="en", issuer=ctx["company"],
        forbidden_names=ctx["forbidden"], aliases=ctx["aliases"],
        person=ctx["person"], projects=ctx["projects"], default_role=ctx["role"],
    )
    assert not err and saved, err
    out = (tmp / saved[0]["filename"]).read_text(encoding="utf-8")
    assert "苏州启明" not in out, out
    assert "苏州" not in out, out
    assert "项目证明" not in out, out
    assert "fulfilled the responsibilities of the role" not in out, out
    assert "served as Researcher" not in out, out
    assert "hosted or participated" in out.lower(), out
    assert "employer prior" not in out.lower(), out
    assert "host company of the application" not in out.lower(), out
    assert "Funding / source" not in out, out
    assert "MACHINE LEARNING" in out.upper(), out
    assert "The Boeing Company" in out
    print("[OK] 来华前工作单位=Boeing，过滤申报企业项目，任职信改写为项目主持/参与证明")


def test_codebuddy(person: str, projects: list) -> dict:
    work = ROOT / "tasks" / "_project_proof_live_test" / "codebuddy"
    print("[..] CodeBuddy 联网检索（可能 30s~3min）…")
    cb = PP.run_codebuddy_search(
        person=person, attach_id="TEST001", projects=projects, work_dir=work
    )
    print("[CB] ok=", cb.get("ok"), "error=", cb.get("error"))
    print("[CB] items=", len(cb.get("items") or []))
    for it in (cb.get("items") or [])[:5]:
        print("     -", it.get("title"), (it.get("url") or "")[:100])
    return cb


async def test_live_prior_gen() -> int:
    pool = ROOT / "tasks" / "1a0d17aab99-7292f2" / "work" / "tmp" / "pool.json"
    snap = json.loads(pool.read_text(encoding="utf-8")) if pool.is_file() else _boeing_snap()
    app_text = "申报企业：苏州启明智造科技有限公司"
    ctx = PP.prior_work_context(snap, app_text)
    print("[CTX]", ctx.get("note"), "projects=", len(ctx.get("projects") or []))
    if not ctx.get("ok"):
        print("[FAIL] 未解析到来华前工作单位")
        return 1
    work = ROOT / "tasks" / "_51104_prior_employer_gen"
    gen = await PP.call_generate_api(
        person=ctx["person"],
        attach_id="51104",
        company=ctx["company"],
        projects=ctx["projects"],
        work_dir=work,
        language=ctx["language"],
        role=ctx["role"],
        start_date=ctx["startDate"],
        end_date=ctx["endDate"],
        forbidden_names=ctx.get("forbidden") or [],
        aliases=ctx.get("aliases") or [],
    )
    print("[GEN] ok=", gen.get("ok"), "n=", len(gen.get("items") or []), "err=", gen.get("error") or "")
    leak = []
    for it in gen.get("items") or []:
        print("     -", it.get("filename"))
        p = Path(str(it.get("url") or ""))
        if p.suffix.lower() == ".html" and p.is_file():
            body = p.read_text(encoding="utf-8", errors="ignore")
            if "苏州启明" in body:
                leak.append(p.name + ":苏州启明")
            if ctx["language"] == "en" and "项目证明" in body:
                leak.append(p.name + ":项目证明")
            if "fulfilled the responsibilities of the role" in body:
                leak.append(p.name + ":任职信模板")
            if "hosted or participated" not in body.lower() and "主持或参与" not in body:
                leak.append(p.name + ":非项目主持/参与证明")
            if "employer prior to the candidate" in body.lower() or "非申报依托单位" in body:
                leak.append(p.name + ":内部说明写进正文")
            if "Funding / source" in body:
                leak.append(p.name + ":Funding斜杠")
    if leak:
        print("[FAIL] 混排/申报企业残留:", leak)
        return 1
    if not gen.get("ok") or not (gen.get("items") or []):
        print("[FAIL] 生成接口未产出")
        return 1
    print("[OK] 签发单位=", ctx["company"], "语言=", ctx["language"], "无申报企业/中英标题混排")
    return 0


async def test_generate(person: str, projects: list) -> dict:
    work = ROOT / "tasks" / "_project_proof_live_test" / "generate"
    print("[..] 生成 API（AutoRef）…")
    gen = await PP.call_generate_api(
        person=person,
        attach_id="TEST001",
        company="Test University",
        projects=projects,
        work_dir=work,
    )
    print("[GEN] ok=", gen.get("ok"), "error=", gen.get("error"))
    print("[GEN] items=", len(gen.get("items") or []))
    for it in (gen.get("items") or [])[:5]:
        print("     -", it.get("title"), it.get("filename"))
    return gen


async def test_resolve_skip_external() -> None:
    opinion = ["请补充主持科研项目的立项批文，项目证明必须提供。"]
    snap = {
        "talent": {"name": "Demo", "payload": {"科研项目": [{"项目名称": "AI Vision Project"}]}},
        "keys": {"names": ["Demo"], "company": "Demo Co"},
    }
    prev = {
        "codebuddyFetched": True,
        "generateFetched": True,
        "items": [],
        "private": {},
        "notes": [],
    }
    r = await resolve_missing(
        "test-task", opinion, snap, "DEMO001", prev=prev, app_text="", task_dir=ROOT / "tasks" / "_mock"
    )
    assert r["needed"] == ["项目证明"]
    lines = leftover_lines(r)
    assert any("项目证明" in x for x in lines)
    print("[OK] resolve_missing（跳过外部调用）:", r["summary"])
    print("     leftover:", lines[0][:120])


async def test_resolve_live(person: str, projects: list, snap: dict) -> dict:
    work = ROOT / "tasks" / "_project_proof_e2e"
    opinion = [
        "请补充提供主持科研项目的立项批文扫描件，严禁用论文代替项目证明。"
        "部分项目证明附件也看不清，请重新上传。"
    ]
    print("[..] 全流程 resolve_missing：人才库附件包 → CodeBuddy → 生成 API …")
    r = await resolve_missing(
        "e2e-proof",
        opinion,
        snap,
        "TEST001",
        app_text="主持加拿大 NSERC、Mitacs 等项目累计超 70 万加元。",
        task_dir=work,
    )
    print("[E2E] needed:", r.get("needed"))
    print("[E2E] summary:", r.get("summary"))
    print("[E2E] notes:")
    for n in r.get("notes") or []:
        print("      -", n)
    print("[E2E] items:", len(r.get("items") or []))
    for it in r.get("items") or []:
        print("      -", it.get("source"), it.get("kind"), it.get("filename"), it.get("download"))
    print("[E2E] leftovers:")
    for line in leftover_lines(r):
        print("      ", line[:200])
    return r


def main() -> int:
    live = "--live" in sys.argv
    if "--live-gen" in sys.argv:
        return asyncio.run(test_live_prior_gen())
    test_parse()
    test_prompt_and_filter()
    test_autoref_contract()
    test_prior_employer_and_html()
    test_prior_from_hj_app_when_pool_empty()
    test_feedback_project_sanitize()
    person, projects, snap = test_detect_and_extract()
    asyncio.run(test_resolve_skip_external())
    if not live:
        print("\n=== 结论：prompt 与过滤单测通过。全流程请加 --live ===")
        return 0
    snap["talent"]["name"] = person
    r = asyncio.run(test_resolve_live(person, projects, snap))
    items = r.get("items") or []
    gen_ok = any(it.get("source") == "generate" for it in items)
    cb_ok = any(it.get("source") == "codebuddy" for it in items)
    pool_ok = any(it.get("source") == "pool" for it in items)
    if items:
        print("\n=== 结论：全流程跑通，已产出", len(items), "个项目证明 ===")
        print("    来源：人才库" if pool_ok else "    来源：非人才库",
              "/ 联网检索" if cb_ok else "",
              "/ 生成接口" if gen_ok else "", sep="")
        return 0
    print("\n=== 结论：全流程已执行，但未产出可下载项目证明 ===")
    print("    CodeBuddy:", r.get("codebuddyError") or "无命中")
    print("    生成 API:", r.get("generateError") or "无命中")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
