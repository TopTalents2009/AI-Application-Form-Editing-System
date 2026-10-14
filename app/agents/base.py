from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Trigger:
    agent_id: str
    agent_name: str
    confidence: str
    reasons: list[str]
    action: str
    fields: dict = field(default_factory=dict)


class Agent:
    id = ""
    name = ""
    mission = ""
    listens_for = ""

    def inspect(self, message: dict, context: list[dict], extras: dict) -> Trigger | None:
        raise NotImplementedError
