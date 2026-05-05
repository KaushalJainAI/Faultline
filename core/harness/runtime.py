"""Composable harness runtime containing the 9 architecture components."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List

from .built_in_skills import BUILT_IN_SKILLS
from .context_compaction import CompactionPolicy
from .hooks import HookManager
from .iteration_engine import IterationPolicy
from .permissions import PermissionLayer
from .registry import ToolRegistry
from .sub_agents import SubAgentManager


@dataclass
class HarnessRuntime:
    iteration_policy: IterationPolicy = field(default_factory=IterationPolicy)
    compaction_policy: CompactionPolicy = field(default_factory=CompactionPolicy)
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    hooks: HookManager = field(default_factory=HookManager)
    sub_agents: SubAgentManager = field(default_factory=SubAgentManager)
    permissions: PermissionLayer = field(default_factory=PermissionLayer)
    built_in_skills: List[str] = field(default_factory=lambda: list(BUILT_IN_SKILLS))

    @classmethod
    def from_tools(cls, tools: Iterable[Any], permission: str = "workspace") -> "HarnessRuntime":
        rt = cls()
        rt.registry.register_many(tools, permission=permission)
        return rt

