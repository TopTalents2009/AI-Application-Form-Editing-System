from __future__ import annotations

import re

from .base import Agent, Trigger
from ..agent_extract import (
    QUESTION_RE,
    companies_in,
    dates_in,
    file_kind,
    filename_of,
    is_ack_only,
    summary,
    talent_ids,
    text_of,
)


def _trig(agent: Agent, reasons: list[str], action: str, fields: dict | None = None, confidence: str = "medium") -> Trigger:
    return Trigger(
        agent_id=agent.id,
        agent_name=agent.name,
        confidence=confidence,
        reasons=reasons,
        action=action,
        fields=fields or {},
    )


def _blob(message: dict) -> str:
    return f"{text_of(message)} {filename_of(message)}"


def _ctx_ids(context: list[dict]) -> list[str]:
    found: list[str] = []
    for row in context[-8:]:
        for tid in talent_ids(text_of(row), filename_of(row)):
            if tid not in found:
                found.append(tid)
    return found[:6]


class ReadLogAgent(Agent):
    id = "read_log"
    name = "甲方收件台"
    mission = "登记甲方每一条有效消息，形成已读摘要，供后续 Agent 共用。"
    listens_for = "甲方发出的非系统消息"

    def inspect(self, message, context, extras):
        text = text_of(message)
        fname = filename_of(message)
        if not text and not fname:
            return None
        return _trig(
            self,
            ["甲方有效消息，登记收件"],
            f"已读登记：{summary(text, fname)}",
            {"summary": summary(text, fname, 120)},
            "high",
        )


class FileIntakeAgent(Agent):
    id = "file_intake"
    name = "材料入库"
    mission = "识别甲方发来的申报书、修改意见、企业材料、护照等文件，提取人才编号并建议入库。"
    listens_for = "带附件、文件名或材料包的消息"

    def inspect(self, message, context, extras):
        fname = filename_of(message)
        text = text_of(message)
        has_file = bool(message.get("has_attachment") or fname)
        if not has_file:
            return None
        kind, label = file_kind(fname, text)
        ids = talent_ids(fname, text) or _ctx_ids(context)
        fields = {"filename": fname, "fileKind": kind, "fileKindLabel": label, "talentIds": ids}
        bits = [label or "附件"]
        if ids:
            bits.append("人才 " + "、".join(ids))
        action = "入库 " + "，".join(bits) + (f"：{fname}" if fname else "")
        return _trig(self, ["消息含附件或文件名"], action, fields, "high")


class DeadlineAgent(Agent):
    id = "deadline"
    name = "交期管家"
    mission = "捕捉甲方给出的截止日期、催交和紧急上报要求，生成交期待办。"
    listens_for = "截止、之前、尽快、今晚、上报、预计、什么时候交"

    _RE = re.compile(
        r"截止|尽快|今晚|明天|后天|来不及|务必|上报|做好了吗|改好|"
        r"预计.{0,8}(时候|时间)|什么时候|啥时候|节前|\d{1,2}月\d{1,2}[日号]前"
    )

    def inspect(self, message, context, extras):
        text = text_of(message)
        if not text or is_ack_only(text):
            return None
        if not self._RE.search(text):
            return None
        dates = dates_in(text)
        ids = talent_ids(text) or _ctx_ids(context)
        urgency = "高" if re.search(r"尽快|赶|务必|来不及|今晚|明天", text) else "中"
        action = f"记录交期（紧急{urgency}）"
        if dates:
            action += "：" + "、".join(dates)
        if ids:
            action += "；关联人才 " + "、".join(ids)
        return _trig(self, ["出现交期或催办用语"], action, {"dates": dates, "talentIds": ids, "urgency": urgency}, "high")


class RevisionAgent(Agent):
    id = "revision"
    name = "修改意见采集"
    mission = "从甲方文字和质检/修改文件中抽出要改的点，准备改稿任务。"
    listens_for = "修改、意见、退回、缺失、护照、工作日期、字数、请改"

    _RE = re.compile(r"修改意见|质检反馈|请改|更正|退回|缺失|不清晰|补传|不合格|批注|逐一对应|再修改|修改")

    def inspect(self, message, context, extras):
        text = text_of(message)
        fname = filename_of(message)
        blob = _blob(message)
        if is_ack_only(text):
            return None
        kind, _ = file_kind(fname, text)
        hit = bool(self._RE.search(blob) or kind == "opinion")
        if not hit:
            return None
        ids = talent_ids(fname, text) or _ctx_ids(context)
        action = "采集修改点并生成改稿任务"
        if ids:
            action += "：" + "、".join(ids)
        if fname:
            action += f"；文件 {fname}"
        return _trig(self, ["修改/意见/材料问题用语或质检文件"], action, {"talentIds": ids, "filename": fname}, "high")


class TalentChangeAgent(Agent):
    id = "talent_change"
    name = "人选变更"
    mission = "处理甲方放弃、换人、再匹配人选，同步匹配组。"
    listens_for = "放弃、换人、再找、物色、不申报、退出、另外一个"

    _RE = re.compile(r"放弃|换人|再找|物色|不申报|不再报|取消人选|换一个|再帮.*人选")

    def inspect(self, message, context, extras):
        text = text_of(message)
        if not text or is_ack_only(text) or not self._RE.search(text):
            return None
        ids = talent_ids(text) or _ctx_ids(context)
        action = "标记人选变更并通知匹配"
        if ids:
            action += "：" + "、".join(ids)
        return _trig(self, ["出现放弃/换人/再找等变更用语"], action, {"talentIds": ids}, "high")


class MatchAgent(Agent):
    id = "match"
    name = "匹配确认"
    mission = "记录甲方对企业匹配、申报类别和入选目标的确认或否决。"
    listens_for = "匹配、企业、入选、A类、QM、HJ、确认配合"

    _RE = re.compile(r"匹配|入选|A类|B类|企业初步确认|配合匹配|换.*对吧")

    def inspect(self, message, context, extras):
        text = text_of(message)
        if not text or is_ack_only(text) or not self._RE.search(text):
            return None
        if len(text) < 8 and text in ("申报", "QM", "HJ"):
            return None
        companies = companies_in(text)
        ids = talent_ids(text) or _ctx_ids(context)
        action = "更新匹配/类别状态"
        if companies:
            action += "：" + "、".join(companies)
        if ids:
            action += "；人才 " + "、".join(ids)
        return _trig(self, ["匹配、类别或企业确认相关"], action, {"companies": companies, "talentIds": ids})


class MaterialAgent(Agent):
    id = "material"
    name = "材料清单"
    mission = "整理甲方要求提供或补齐的企业信息、模板和证明材料。"
    listens_for = "提供、材料、基本信息、模板、企业信息、必填、护照"

    _RE = re.compile(r"提供|材料|基本信息|基本情况|模板|企业信息|必填|麻烦发|请发|护照|证明")

    def inspect(self, message, context, extras):
        text = text_of(message)
        fname = filename_of(message)
        if is_ack_only(text):
            return None
        kind, _ = file_kind(fname, text) if fname else ("", "")
        asked = bool(self._RE.search(text or ""))
        file_hit = bool(fname) and kind in {"company", "passport", "id_doc", "work", "edu", "report"}
        if not asked and not file_hit:
            return None
        action = "生成甲方材料清单"
        if fname:
            action += f"：{fname}"
        return _trig(self, ["甲方要材料或发来企业/证明类文件"], action, {"filename": fname, "fileKind": kind})


class QuestionAgent(Agent):
    id = "question"
    name = "甲方问答"
    mission = "抓住甲方提问，提炼问题并给出建议答复要点。"
    listens_for = "问号、吗、能否、有没有、预计、什么时候"

    def inspect(self, message, context, extras):
        text = text_of(message)
        if not text or is_ack_only(text) or not QUESTION_RE.search(text):
            return None
        if len(text) < 4:
            return None
        return _trig(self, ["甲方提问"], f"待答复：{summary(text, limit=60)}", {"question": summary(text, limit=120)})


class RiskAgent(Agent):
    id = "risk"
    name = "风险预警"
    mission = "标出不匹配、重复报名、退回、来不及等风险，建议升级处理。"
    listens_for = "不匹配、重复报名、退回、放弃、上级、来不及、风险"

    _RE = re.compile(r"不匹配|重复报名|退回|放弃|来不及|风险|冲突")

    def inspect(self, message, context, extras):
        text = text_of(message)
        if not text or is_ack_only(text) or not self._RE.search(text):
            return None
        level = "高" if re.search(r"重复报名|不匹配|退回|来不及", text) else "中"
        return _trig(self, ["出现风险用语"], f"风险{level}：{summary(text, limit=50)}", {"level": level}, "high")


class ConfirmAgent(Agent):
    id = "confirm"
    name = "收悉确认"
    mission = "甲方只是确认收到或致谢时，关闭待回复，不再叠加重业务动作。"
    listens_for = "收到、好的、谢谢、辛苦、嗯、ok、1"

    def inspect(self, message, context, extras):
        text = text_of(message)
        fname = filename_of(message)
        if fname:
            return None
        if not text or not is_ack_only(text):
            return None
        return _trig(self, ["纯确认/致谢"], "关闭待回复，无需业务动作", {}, "high")


def all_agents() -> list[Agent]:
    return [
        ReadLogAgent(),
        FileIntakeAgent(),
        DeadlineAgent(),
        RevisionAgent(),
        TalentChangeAgent(),
        MatchAgent(),
        MaterialAgent(),
        QuestionAgent(),
        RiskAgent(),
        ConfirmAgent(),
    ]


def get_agent(agent_id: str) -> Agent | None:
    for agent in all_agents():
        if agent.id == agent_id:
            return agent
    return None
