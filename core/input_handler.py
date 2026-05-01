"""
core/input_handler.py

Non-blocking input handler for the Faultline interactive CLI.
Provides:
  - Esc key detection (polls in background without blocking the agent loop)
  - Slash command parsing and dispatch
  - The "Steering Room" — an interactive menu shown when Esc is pressed
  - Smart raw-text fallback: any non-slash input is treated as a steer command

Platform: Windows (msvcrt) with a fallback stub for Unix.
"""

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

logger = logging.getLogger("FaultlineInput")


# ---------------------------------------------------------------------------
# Action types returned by the steering room
# ---------------------------------------------------------------------------

class ActionType(Enum):
    RESUME = "resume"
    STEER = "steer"
    SKIP = "skip"
    QUIT = "quit"
    SAVE = "save"
    STATUS = "status"
    FINDINGS = "findings"
    MODEL = "model"
    HELP = "help"


@dataclass
class SteeringAction:
    type: ActionType
    text: str = ""
    model_value: str = ""


# ---------------------------------------------------------------------------
# Non-blocking key detection
# ---------------------------------------------------------------------------

def _check_esc_key() -> bool:
    """
    Check if the Esc key (0x1B) was pressed without blocking.
    Returns True if Esc was detected.
    """
    try:
        import msvcrt
        if msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b'\x1b':
                return True
            # Consume any extra bytes (e.g., arrow key sequences)
    except ImportError:
        # Unix fallback — use select on stdin
        try:
            import select
            if select.select([sys.stdin], [], [], 0)[0]:
                ch = sys.stdin.read(1)
                if ch == '\x1b':
                    return True
        except Exception:
            pass
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Slash command parsing
# ---------------------------------------------------------------------------

def parse_slash_command(raw: str) -> SteeringAction:
    """
    Parse user input into a SteeringAction.
    Supports slash commands and raw text (treated as /steer).
    """
    text = raw.strip()
    if not text:
        return SteeringAction(ActionType.RESUME)

    # Slash commands
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        command_map = {
            "/help": ActionType.HELP,
            "/h": ActionType.HELP,
            "/status": ActionType.STATUS,
            "/s": ActionType.STATUS,
            "/steer": ActionType.STEER,
            "/findings": ActionType.FINDINGS,
            "/f": ActionType.FINDINGS,
            "/skip": ActionType.SKIP,
            "/save": ActionType.SAVE,
            "/model": ActionType.MODEL,
            "/m": ActionType.MODEL,
            "/quit": ActionType.QUIT,
            "/q": ActionType.QUIT,
            "/exit": ActionType.QUIT,
            "/resume": ActionType.RESUME,
            "/r": ActionType.RESUME,
            "/continue": ActionType.RESUME,
            "/c": ActionType.RESUME,
        }

        action_type = command_map.get(cmd)
        if action_type is None:
            # Unknown command — treat as steer with the full text
            return SteeringAction(ActionType.STEER, text=text)

        if action_type == ActionType.STEER:
            return SteeringAction(ActionType.STEER, text=arg)
        if action_type == ActionType.MODEL:
            return SteeringAction(ActionType.MODEL, model_value=arg)
        return SteeringAction(action_type, text=arg)

    # Raw text without slash → treat as a steer message
    return SteeringAction(ActionType.STEER, text=text)


# ---------------------------------------------------------------------------
# InputHandler — background Esc listener + steering room
# ---------------------------------------------------------------------------

class InputHandler:
    """
    Manages non-blocking Esc key detection and the interactive steering room.
    Runs a background asyncio task that polls for keypresses.
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.pause_requested = asyncio.Event()
        self._poll_task: Optional[asyncio.Task] = None
        self._stopped = False

    def start(self) -> None:
        """Start the background key-polling task."""
        self._stopped = False
        self.pause_requested.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())

    def stop(self) -> None:
        """Stop the background polling."""
        self._stopped = True
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self.pause_requested.clear()

    def suspend(self) -> None:
        """Temporarily suspend polling (during interactive prompts)."""
        self._stopped = True

    def resume_polling(self) -> None:
        """Resume polling after a suspend."""
        self._stopped = False
        self.pause_requested.clear()
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Background coroutine: check for Esc every 100ms."""
        while not self._stopped:
            try:
                if _check_esc_key():
                    self.pause_requested.set()
                    logger.info("Esc key detected — pause requested")
                    return  # Stop polling once paused
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                return

    # ------------------------------------------------------------------
    # Steering Room
    # ------------------------------------------------------------------

    async def enter_steering_room(
        self,
        turn: int = 0,
        findings_count: int = 0,
        elapsed_seconds: float = 0,
        active_model: str = "",
    ) -> SteeringAction:
        """
        Show the interactive steering room and wait for user input.
        Runs in a thread to avoid blocking the async loop.
        """
        self.suspend()
        try:
            action = await asyncio.to_thread(
                self._steering_room_sync,
                turn, findings_count, elapsed_seconds, active_model,
            )
        finally:
            self.pause_requested.clear()
        return action

    def _steering_room_sync(
        self,
        turn: int,
        findings_count: int,
        elapsed_seconds: float,
        active_model: str,
    ) -> SteeringAction:
        """Synchronous steering room — runs in a thread."""
        elapsed_str = f"{elapsed_seconds:.0f}s" if elapsed_seconds else "—"
        model_str = active_model or "(default from .env)"

        self.console.print()
        self.console.print(Panel(
            f"[bold]Turn:[/bold] {turn}  │  "
            f"[bold]Findings:[/bold] {findings_count}  │  "
            f"[bold]Elapsed:[/bold] {elapsed_str}  │  "
            f"[bold]Model:[/bold] {model_str}\n\n"
            "[dim]Commands:[/dim]\n"
            "  [cyan]/steer[/cyan] [dim]<msg>[/dim]   Redirect the agent's focus\n"
            "  [cyan]/status[/cyan]          Show campaign progress\n"
            "  [cyan]/findings[/cyan]        List findings so far\n"
            "  [cyan]/model[/cyan] [dim]<name>[/dim]  Switch LLM model\n"
            "  [cyan]/skip[/cyan]            Skip current phase\n"
            "  [cyan]/save[/cyan]            Force-save checkpoint\n"
            "  [cyan]/resume[/cyan]          Continue the campaign\n"
            "  [cyan]/quit[/cyan]            Save checkpoint and exit\n"
            "  [cyan]/help[/cyan]            Show this menu\n\n"
            "[dim]Or just type a message to steer the agent.[/dim]",
            title="[bold yellow]⏸  PAUSED[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        ))

        while True:
            try:
                raw = Prompt.ask("[yellow]faultline[/yellow]")
            except (EOFError, KeyboardInterrupt):
                return SteeringAction(ActionType.QUIT)

            action = parse_slash_command(raw)

            if action.type == ActionType.HELP:
                self._show_help()
                continue

            if action.type == ActionType.STATUS:
                self._show_status(turn, findings_count, elapsed_seconds, active_model)
                continue

            if action.type == ActionType.FINDINGS:
                # Findings display is delegated to the caller (agent has the data)
                return action

            if action.type == ActionType.MODEL:
                return self._handle_model_command(action)

            if action.type == ActionType.STEER and not action.text:
                self.console.print("[dim]  Provide a message, e.g.:[/dim] /steer Focus on the auth endpoints")
                continue

            # All other actions (resume, quit, skip, save, steer with text) → return
            return action

    def _show_help(self) -> None:
        """Print the help panel."""
        table = Table(title="Faultline Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="cyan")
        table.add_column("Alias", style="dim")
        table.add_column("Description")
        table.add_row("/steer <msg>", "—", "Redirect the agent's focus (or just type a message)")
        table.add_row("/status", "/s", "Show campaign progress summary")
        table.add_row("/findings", "/f", "List findings recorded so far")
        table.add_row("/model <name>", "/m", "Switch LLM model mid-campaign")
        table.add_row("/skip", "—", "Skip current phase, move to next")
        table.add_row("/save", "—", "Force-save a checkpoint now")
        table.add_row("/resume", "/r, /c", "Continue the campaign")
        table.add_row("/quit", "/q", "Save checkpoint and exit gracefully")
        table.add_row("/help", "/h", "Show this help")
        self.console.print(table)

    def _show_status(self, turn: int, findings: int, elapsed: float, model: str) -> None:
        """Print a quick status summary."""
        self.console.print(
            f"\n  [bold]Campaign Status[/bold]\n"
            f"  Turn:     {turn}\n"
            f"  Findings: {findings}\n"
            f"  Elapsed:  {elapsed:.0f}s\n"
            f"  Model:    {model or '(default from .env)'}\n"
        )

    def _handle_model_command(self, action: SteeringAction) -> SteeringAction:
        """Handle /model — list models or switch to a specific one."""
        from core.model_registry import list_models, find_model, format_model_list

        if not action.model_value:
            # List all models
            self.console.print(f"\n[bold]Available Models:[/bold]\n")
            self.console.print(format_model_list())
            self.console.print("\n[dim]  Usage: /model <name or number>[/dim]\n")
            # Don't return — stay in steering room
            return SteeringAction(ActionType.HELP)  # Signal to continue loop

        query = action.model_value.strip()

        # Try number first
        if query.isdigit():
            idx = int(query) - 1
            models = list_models()
            if 0 <= idx < len(models):
                m = models[idx]
                self.console.print(
                    f"  [green]✓[/green] Switching to [bold]{m.name}[/bold] "
                    f"([dim]{m.value}[/dim]) on next turn"
                )
                return SteeringAction(ActionType.MODEL, model_value=m.value)

        # Try name/value search
        m = find_model(query)
        if m:
            self.console.print(
                f"  [green]✓[/green] Switching to [bold]{m.name}[/bold] "
                f"([dim]{m.value}[/dim]) on next turn"
            )
            return SteeringAction(ActionType.MODEL, model_value=m.value)

        self.console.print(f"  [red]✗[/red] No model matching '{query}'. Use /model to list all.")
        return SteeringAction(ActionType.HELP)  # Stay in loop
