"""Jev 式固定题目：只判断有没有修改意见。答题模型是现有 Gemini，不走 OpenRouter。"""
from __future__ import annotations
import json

# 题目用英文，消息原文保持中文。一次请求问完，避免自由发挥。
_PROMPT = """You judge a window of enterprise WeChat messages about Chinese talent application forms (申报书).
Instructions are English. Keep message text unchanged. Quote ids from the input only. Never invent an id.

Questions:
- has_opinion (yes/no): Is there a concrete request to revise the form or to supply a named missing field or material
  (passport number, education proof, work dates, employer name, or a review/annotation document)?
  Not an opinion: 收到 / 好的 / 谢谢, contracts, intention agreements, passport or diploma scans themselves,
  material checklists, or small talk.
- opinion_ids: input ids that are opinions. Empty when has_opinion is false.
- confidence: high when an opinion document or an explicit field change is present; medium when likely but brief; low when unclear.
  Use one level for the whole window.
- pair_ready (yes/no): true only if this window contains both an application-form file (rule=app) and at least one opinion.
  pair_ready does not change has_opinion.

rule meanings: app=application form, opinion=opinion document, text=opinion-like text, ignore=ignore.

Output one JSON object and nothing else:
{"hasOpinion":true,"opinions":[{"id":3,"confidence":"high"}],"pairReady":false,"reason":"one short Chinese sentence"}
If none: {"hasOpinion":false,"opinions":[],"pairReady":false,"reason":""}

Messages:
"""


def judgment_prompt(messages: list) -> str:
    slim = []
    for row in messages or []:
        if not isinstance(row, dict):
            continue
        slim.append({
            "id": row.get("id"),
            "time": row.get("time") or "",
            "sender": row.get("sender") or "",
            "filename": row.get("filename") or "",
            "text": row.get("text") or "",
            "rule": row.get("rule") or "",
        })
    return _PROMPT + json.dumps(slim, ensure_ascii=False)
