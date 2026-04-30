"""
Human-in-the-Loop (HITL) manager for Faultline.

Used by the interactive CLI (faultline.py) to pause the agent and ask the
operator for permission before destructive actions or for credentials when
the agent encounters an authentication challenge.

When HITL is disabled (REST API path, scripts, headless mode) all methods
return safe defaults and never block.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("FaultlineHITL")

_HITL_ENABLED: bool = False


def enable_hitl() -> None:
    """Opt in to interactive prompts. Called only by the CLI entry point."""
    global _HITL_ENABLED
    _HITL_ENABLED = True


def disable_hitl() -> None:
    global _HITL_ENABLED
    _HITL_ENABLED = False


def is_enabled() -> bool:
    return _HITL_ENABLED


class HITLManager:
    """
    Synchronous human-in-the-loop prompts.

    Call directly from sync code, or from async code via the
    `async_request_*` helpers below which off-load the blocking prompt
    to a thread so the event loop is not frozen.
    """

    def request_permission(self, action_name: str, description: str) -> bool:
        if not _HITL_ENABLED:
            return True
        try:
            from rich.prompt import Confirm
            from rich.console import Console
            from rich.panel import Panel
            console = Console()
            panel = Panel(
                f"[bold]Action:[/bold] {action_name}\n[dim]{description}[/dim]",
                title="[yellow]HITL Permission Request[/yellow]",
                border_style="yellow",
            )
            console.print(panel)
            return Confirm.ask("  Approve this action?", default=False)
        except Exception as exc:
            logger.warning("HITL permission prompt failed: %s. Defaulting to deny.", exc)
            return False

    def request_credential(self, name: str, hint: str = "", sensitive: bool = True) -> str:
        if not _HITL_ENABLED:
            return ""
        try:
            from rich.prompt import Prompt
            from rich.console import Console
            from rich.panel import Panel
            console = Console()
            body = f"[bold]Needed:[/bold] {name}"
            if hint:
                body += f"\n[dim]{hint}[/dim]"
            panel = Panel(
                body,
                title="[cyan]Credential Request[/cyan]",
                border_style="cyan",
            )
            console.print(panel)
            return Prompt.ask(f"  Enter {name}", password=sensitive, default="")
        except Exception as exc:
            logger.warning("HITL credential prompt failed: %s. Returning empty.", exc)
            return ""


# Module-level singleton — import this from anywhere
hitl = HITLManager()


# ---------------------------------------------------------------------------
# Async bridges — call these from inside an asyncio coroutine
# ---------------------------------------------------------------------------

async def async_request_permission(action_name: str, description: str) -> bool:
    """Async-safe wrapper. Off-loads the blocking Rich prompt to a thread."""
    if not _HITL_ENABLED:
        return True
    return await asyncio.to_thread(hitl.request_permission, action_name, description)


async def async_request_credential(name: str, hint: str = "", sensitive: bool = True) -> str:
    """Async-safe wrapper. Off-loads the blocking Rich prompt to a thread."""
    if not _HITL_ENABLED:
        return ""
    return await asyncio.to_thread(hitl.request_credential, name, hint, sensitive)
