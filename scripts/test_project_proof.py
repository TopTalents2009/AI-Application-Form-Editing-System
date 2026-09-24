"""项目证明补缺链路本地测试：识别 → 参数抽取 → CodeBuddy → 生成 API。"""
from __future__ import annotations

import asyncio
import json
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
                        "经费总额": "210",
                    },
                    {
                        "项目名称": "PINN curing / Co-training of PINNs",
                        "项目来源": "美国能源部",
                        "开始时间": "2023-04-15",
                        "结束时间": "2025-05-13",
                    },
                    {
                        "项目名称": "汽车零部件在线视觉检测系统",
                        "项目来源": "苏州启明智造科技有限公司",
                    },
                ],
            },
        },
    }


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
    print("[OK] 来华前工作单位=Boeing，过滤申报企业项目，HTML 单语言")


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
