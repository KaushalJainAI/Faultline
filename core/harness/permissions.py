"""Permission and safety layer for tool execution."""

from __future__ import annotations

from dataclasses import dataclass


READ_ONLY = "read-only"
WORKSPACE = "workspace"
FULL_ACCESS = "full-access"


@dataclass
class PermissionDecision:
    allowed: bool
    reason: str = ""


class PermissionLayer:
    """
    Minimal permission checker for harness tools.
    Current implementation enforces policy labels and allows extension.
    """

    def __init__(self, max_level: str = WORKSPACE) -> None:
        self.max_level = max_level
        self._order = {READ_ONLY: 0, WORKSPACE: 1, FULL_ACCESS: 2}

    def authorize(self, required: str) -> PermissionDecision:
        req = required or WORKSPACE
        if self._order.get(req, 1) <= self._order.get(self.max_level, 1):
            return PermissionDecision(True, "")
        return PermissionDecision(False, f"Permission denied: requires {req}, max is {self.max_level}")

