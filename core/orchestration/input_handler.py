"""
core/input_handler.py

Non-blocking input handler for the Faultline interactive CLI.
Provides:
  - Esc key detection (polls in background without blocking the agent loop)
  - Slash command parsing and dispatch
  - The "Steering Room" â€” an interactive menu shown when Esc is pressed
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
    FINISH = "finish"
    SETVAR = "setvar"
    SHOWVARS = "showvars"


@dataclass
class SteeringAction:
    type: ActionType
    text: str = ""
    model_value: str = ""
    key: str = ""
    value: str = ""


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
        # Unix fallback â€” use select on stdin
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
            "/finish": ActionType.FINISH,
            "/wrapup": ActionType.FINISH,
            "/wrap": ActionType.FINISH,
            "/vars": ActionType.SHOWVARS,
        }

        if cmd == "/set":
            if not arg:
                return SteeringAction(ActionType.SETVAR, key="", value="")
            pair = arg.split(maxsplit=1)
            if len(pair) == 1:
                return SteeringAction(ActionType.SETVAR, key=pair[0].strip(), value="")
            return SteeringAction(ActionType.SETVAR, key=pair[0].strip(), value=pair[1].strip())

        action_type = command_map.get(cmd)
        if action_type is None:
            # Unknown command â€” treat as steer with the full text
            return SteeringAction(ActionType.STEER, text=text)

        if action_type == ActionType.STEER:
            return SteeringAction(ActionType.STEER, text=arg)
        if action_type == ActionType.MODEL:
            return SteeringAction(ActionType.MODEL, model_value=arg)
        return SteeringAction(action_type, text=arg)

    # Raw text without slash â†’ treat as a steer message
    return SteeringAction(ActionType.STEER, text=text)


def build_structured_steer(text: str) -> str:
    """
    Convert free-form steering into a structured control block the agent can follow.
    If the operator already provides labeled fields, preserve as-is.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    labels = ("objective:", "constraint:", "priority:", "stop_doing:", "success:")
    if any(label in lower for label in labels):
        return raw
    return (
        "STEERING_UPDATE\n"
        f"OBJECTIVE: {raw}\n"
        "CONSTRAINT: Respect current budget/tool limits unless operator changes them.\n"
        "PRIORITY: Focus on highest-signal paths first. Avoid broad re-reading.\n"
        "STOP_DOING: Do not repeat low-yield loops.\n"
        "SUCCESS: Next response should show plan delta and one concrete next action."
    )


# ---------------------------------------------------------------------------
# InputHandler â€” background Esc listener + steering room
# ---------------------------------------------------------------------------

class InputHandler:
    """
    Manages non-blocking Esc key detection and the interactive steering room.
    Runs a background asyncio task that polls for keypresses.
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.pause_requested = asyncio.Event()
        # halt_requested signals the agent loop to terminate the campaign
        # entirely (operator pressed ESC inside the steering room or chose /quit).
        # Distinct from pause_requested which only opens the steering menu.
        self.halt_requested = asyncio.Event()
        self._poll_task: Optional[asyncio.Task] = None
        self._stopped = False

    def start(self) -> None:
        """Start the background key-polling task. Idempotent."""
        self._stopped = False
        self.pause_requested.clear()
        if self._poll_task is None or self._poll_task.done():
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
                    logger.info("Esc key detected â€” pause requested")
                    await asyncio.sleep(0.15)
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
        session_vars: Optional[dict] = None,
    ) -> SteeringAction:
        """
        Show the interactive steering room and wait for user input.
        Runs in a thread to avoid blocking the async loop.
        """
        self.suspend()
        try:
            action = await asyncio.to_thread(
                self._steering_room_sync,
                turn, findings_count, elapsed_seconds, active_model, session_vars or {},
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
        session_vars: dict,
    ) -> SteeringAction:
        """Synchronous steering room â€” runs in a thread."""
        elapsed_str = f"{elapsed_seconds:.0f}s" if elapsed_seconds else "â€”"
        model_str = active_model or "(default from .env)"

        vars_preview = ", ".join(f"{k}={v}" for k, v in (session_vars or {}).items()) or "none"
        self.console.print()
        self.console.print(Panel(
            f"[bold]Turn:[/bold] {turn}  â”‚  "
            f"[bold]Findings:[/bold] {findings_count}  â”‚  "
            f"[bold]Elapsed:[/bold] {elapsed_str}  â”‚  "
            f"[bold]Model:[/bold] {model_str}\n"
            f"[bold]Session Vars:[/bold] {vars_preview}\n\n"
            "[dim]Commands:[/dim]\n"
            "  [cyan]/steer[/cyan] [dim]<msg>[/dim]   Structured steering update\n"
            "  [cyan]/set[/cyan] [dim]<var> <value>[/dim]  Change session variable\n"
            "  [cyan]/vars[/cyan]            Show session variables\n"
            "  [cyan]/status[/cyan]          Show campaign progress\n"
            "  [cyan]/findings[/cyan]        List findings so far\n"
            "  [cyan]/model[/cyan] [dim]<name>[/dim]  Switch LLM model\n"
            "  [cyan]/skip[/cyan]            Skip current phase\n"
            "  [cyan]/save[/cyan]            Force-save checkpoint\n"
            "  [cyan]/resume[/cyan]          Continue the campaign\n"
            "  [cyan]/wrapup[/cyan]          Force final report + walkthrough in a few calls\n"
            "  [cyan]/finish[/cyan]          Alias for /wrapup\n"
            "  [cyan]/quit[/cyan]            Save checkpoint and exit\n"
            "  [cyan]/help[/cyan]            Show this menu\n\n"
            "[dim]Or just type a message to steer the agent.[/dim]\n"
            "[dim]Press Esc again here to halt the campaign.[/dim]",
            title="[bold yellow]â¸  PAUSED[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        ))

        while True:
            raw = self._read_steering_input()
            if raw is None:
                # ESC / Ctrl-C / EOF inside the steering room â†’ halt the campaign.
                self.halt_requested.set()
                self.console.print(
                    "  [yellow]ESC pressed â€” halting campaign. "
                    "Checkpoint will be saved; rerun without --resume to start fresh.[/yellow]"
                )
                return SteeringAction(ActionType.QUIT)

            action = parse_slash_command(raw)

            if action.type == ActionType.HELP:
                self._show_help()
                continue

            if action.type == ActionType.STATUS:
                self._show_status(turn, findings_count, elapsed_seconds, active_model, session_vars or {})
                continue

            if action.type == ActionType.FINDINGS:
                # Findings display is delegated to the caller (agent has the data)
                return action

            if action.type == ActionType.MODEL:
                return self._handle_model_command(action)

            if action.type == ActionType.SHOWVARS:
                self._show_session_vars(session_vars or {})
                continue

            if action.type == ActionType.SETVAR:
                if not action.key or not action.value:
                    self.console.print(
                        "[dim]  Usage:[/dim] /set <max_tool_calls|max_llm_calls|max_turns|token_budget|max_rpm|reasoning_level> <value>"
                    )
                    continue
                return action

            if action.type == ActionType.STEER and not action.text:
                self.console.print("[dim]  Provide a message, e.g.:[/dim] /steer Focus on the auth endpoints")
                continue
            if action.type == ActionType.STEER:
                action.text = build_structured_steer(action.text)

            # All other actions (resume, quit, skip, save, steer with text) â†’ return
            return action

    def _read_steering_input(self) -> Optional[str]:
        """
        Read one line from the operator. Returns None if the operator pressed
        ESC, Ctrl-C, or hit EOF â€” caller should treat that as a halt signal.

        Prefers questionary (ESC is distinguishable from text input). Falls
        back to rich's Prompt.ask if questionary isn't available.
        """
        try:
            import questionary
            answer = questionary.text(
                "faultline >",
                qmark="",
            ).ask()
            if answer is None:
                return None
            return answer
        except ImportError:
            try:
                return Prompt.ask("[yellow]faultline[/yellow]")
            except (EOFError, KeyboardInterrupt):
                return None
        except (EOFError, KeyboardInterrupt):
            return None

    def _show_help(self) -> None:
        """Print the help panel."""
        table = Table(title="Faultline Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="cyan")
        table.add_column("Alias", style="dim")
        table.add_column("Description")
        table.add_row("/steer <msg>", "â€”", "Redirect the agent's focus (or just type a message)")
        table.add_row("/status", "/s", "Show campaign progress summary")
        table.add_row("/findings", "/f", "List findings recorded so far")
        table.add_row("/model <name>", "/m", "Switch LLM model mid-campaign")
        table.add_row("/skip", "â€”", "Skip current phase, move to next")
        table.add_row("/save", "â€”", "Force-save a checkpoint now")
        table.add_row("/resume", "/r, /c", "Continue the campaign")
        table.add_row("/wrapup", "/wrap, /finish", "Stop testing and synthesize final report + walkthrough")
        table.add_row("/quit", "/q", "Save checkpoint and exit gracefully")
        table.add_row("/help", "/h", "Show this help")
        self.console.print(table)

    def _show_status(self, turn: int, findings: int, elapsed: float, model: str, session_vars: Optional[dict] = None) -> None:
        """Print a quick status summary."""
        vars_ = session_vars or {}
        llm = ""
        if vars_.get("llm_calls_used") is not None and vars_.get("max_llm_calls") is not None:
            llm = f"  LLM Calls: {vars_.get('llm_calls_used')}/{vars_.get('max_llm_calls')}\n"
        tools = ""
        if vars_.get("tool_calls_used") is not None and vars_.get("max_tool_calls") is not None:
            tools = f"  Tool Calls: {vars_.get('tool_calls_used')}/{vars_.get('max_tool_calls')}\n"
        request = ""
        if vars_.get("request_context_tokens") is not None and vars_.get("request_context_limit") is not None:
            request = (
                f"  Request:  {vars_.get('request_context_tokens')}/"
                f"{vars_.get('request_context_limit')} compacted tokens\n"
            )
        self.console.print(
            f"\n  [bold]Campaign Status[/bold]\n"
            f"  Turn:     {turn}\n"
            f"  Findings: {findings}\n"
            f"  Elapsed:  {elapsed:.0f}s\n"
            f"{llm}"
            f"{tools}"
            f"{request}"
            f"  Model:    {model or '(default from .env)'}\n"
        )

    def _show_session_vars(self, session_vars: dict) -> None:
        rows = session_vars or {}
        self.console.print("\n  [bold]Session Variables[/bold]")
        if not rows:
            self.console.print("  [dim](none)[/dim]\n")
            return
        for k, v in rows.items():
            self.console.print(f"  {k}: [cyan]{v}[/cyan]")
        self.console.print()

    def _handle_model_command(self, action: SteeringAction) -> SteeringAction:
        """Handle /model â€” list models or switch to a specific one."""
        from core.providers.model_registry import list_models, find_model, format_model_list

        if not action.model_value:
            # Try arrow-key + Enter selection via questionary; fall back to
            # listing models for text-based selection if questionary isn't
            # available or the operator dismisses the picker (ESC).
            try:
                import questionary
                models = list_models()
                if models:
                    choices = [
                        questionary.Choice(
                            title=f"{m.name}  â€”  {m.value}",
                            value=m.value,
                        )
                        for m in models
                    ]
                    selected = questionary.select(
                        "Pick a model (â†‘/â†“ to navigate, Enter to select, Esc to cancel):",
                        choices=choices,
                    ).ask()
                    if selected is None:
                        self.console.print(
                            "  [dim]Selection cancelled â€” agent will continue with current model.[/dim]"
                        )
                        return SteeringAction(ActionType.HELP)  # stay in loop
                    m = find_model(selected)
                    if m:
                        self.console.print(
                            f"  [green]âœ“[/green] Switching to [bold]{m.name}[/bold] "
                            f"([dim]{m.value}[/dim]) on next turn"
                        )
                        return SteeringAction(ActionType.MODEL, model_value=m.value)
            except ImportError:
                pass
            except Exception as _e:
                logger.warning("questionary picker failed (%s), falling back to text list", _e)

            # Fallback: print the list and let the operator type a name/number
            self.console.print(f"\n[bold]Available Models:[/bold]\n")
            self.console.print(format_model_list())
            self.console.print("\n[dim]  Usage: /model <name or number>[/dim]\n")
            return SteeringAction(ActionType.HELP)  # Signal to continue loop

        query = action.model_value.strip()

        # Try number first
        if query.isdigit():
            idx = int(query) - 1
            models = list_models()
            if 0 <= idx < len(models):
                m = models[idx]
                self.console.print(
                    f"  [green]âœ“[/green] Switching to [bold]{m.name}[/bold] "
                    f"([dim]{m.value}[/dim]) on next turn"
                )
                return SteeringAction(ActionType.MODEL, model_value=m.value)

        # Try name/value search
        m = find_model(query)
        if m:
            self.console.print(
                f"  [green]âœ“[/green] Switching to [bold]{m.name}[/bold] "
                f"([dim]{m.value}[/dim]) on next turn"
            )
            return SteeringAction(ActionType.MODEL, model_value=m.value)

        self.console.print(f"  [red]âœ—[/red] No model matching '{query}'. Use /model to list all.")
        return SteeringAction(ActionType.HELP)  # Stay in loop

