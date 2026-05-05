"""Tools and skills registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass
class ToolSpec:
    name: str
    tool: Any
    permission: str = "workspace"


class ToolRegistry:
    """Central index of tools available to the harness."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, tool: Any, permission: str = "workspace") -> None:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", "")
        if not name:
            raise ValueError("tool must expose a name or __name__")
        self._tools[name] = ToolSpec(name=name, tool=tool, permission=permission)

    def register_many(self, tools: Iterable[Any], permission: str = "workspace") -> None:
        for tool in tools:
            self.register(tool, permission=permission)

    def get_permission(self, tool_name: str) -> str:
        spec = self._tools.get(tool_name)
        return spec.permission if spec else "workspace"

    def tools(self) -> List[Any]:
        return [spec.tool for spec in self._tools.values()]

