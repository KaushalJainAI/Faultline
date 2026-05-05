"""Sub-agent management primitives."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SubAgentSpec:
    name: str
    system_prompt: str
    allowed_tools: List[str] = field(default_factory=list)


class SubAgentManager:
    """Tracks spawned sub-agent specs and isolation boundaries."""

    def __init__(self) -> None:
        self._agents: Dict[str, SubAgentSpec] = {}

    def register(self, spec: SubAgentSpec) -> None:
        self._agents[spec.name] = spec

    def list(self) -> List[SubAgentSpec]:
        return list(self._agents.values())

