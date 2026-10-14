from __future__ import annotations

from .agents import all_agents
from .agent_extract import filename_of, is_ack_only, is_system, text_of
from .party import classify, infer_platform

SKIP_ON_ACK = {"deadline", "revision", "talent_change", "match", "material", "question", "risk"}


def route_message(message: dict, context: list[dict] | None = None, platform: str = "wecom") -> dict:
    sender = str(message.get("sender") or "")
    party = classify(sender, platform)
    if is_system(message):
        return {
            "party": party,
            "skipped": True,
            "skipReason": "系统/会议/撤回消息",
            "triggers": [],
        }
    if party["party"] != "甲方":
        return {
            "party": party,
            "skipped": True,
            "skipReason": "乙方消息不进入甲方处理链路",
            "triggers": [],
        }
    text = text_of(message)
    fname = filename_of(message)
    if not text and not fname:
        return {
            "party": party,
            "skipped": True,
            "skipReason": "无正文也无附件",
            "triggers": [],
        }

    extras = {"party": party}
    ctx = list(context or [])
    ack = is_ack_only(text) and not fname
    triggers = []
    for agent in all_agents():
        if ack and agent.id in SKIP_ON_ACK:
            continue
        hit = agent.inspect(message, ctx, extras)
        if hit:
            triggers.append({
                "agentId": hit.agent_id,
                "agentName": hit.agent_name,
                "confidence": hit.confidence,
                "reasons": hit.reasons,
                "action": hit.action,
                "fields": hit.fields,
            })
    return {
        "party": party,
        "skipped": False,
        "skipReason": "",
        "ackOnly": ack,
        "triggers": triggers,
    }


def route_with_session(message: dict, context: list[dict] | None = None, session: dict | None = None) -> dict:
    platform = infer_platform(message, session)
    return route_message(message, context, platform)
