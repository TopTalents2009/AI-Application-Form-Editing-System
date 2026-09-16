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
    test_parse()
    test_prompt_and_filter()
    test_autoref_contract()
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
