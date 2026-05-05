"""Lifecycle hooks around tool execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


PreToolHook = Callable[[str, Dict[str, Any]], "HookDecision"]
PostToolHook = Callable[[str, Dict[str, Any], Any], Any]


@dataclass
class HookDecision:
    allowed: bool = True
    reason: str = ""
    args_override: Optional[Dict[str, Any]] = None


class HookManager:
    """Registers and runs pre/post tool hooks."""

    def __init__(self) -> None:
        self._pre_hooks: List[PreToolHook] = []
        self._post_hooks: List[PostToolHook] = []

    def add_pre_hook(self, hook: PreToolHook) -> None:
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: PostToolHook) -> None:
        self._post_hooks.append(hook)

    def run_pre(self, tool_name: str, args: Dict[str, Any]) -> HookDecision:
        effective_args = dict(args or {})
        for hook in self._pre_hooks:
            decision = hook(tool_name, effective_args)
            if not decision.allowed:
                return decision
            if decision.args_override is not None:
                effective_args = decision.args_override
        return HookDecision(allowed=True, args_override=effective_args)

    def run_post(self, tool_name: str, args: Dict[str, Any], result: Any) -> Any:
        out = result
        for hook in self._post_hooks:
            out = hook(tool_name, args, out)
        return out

