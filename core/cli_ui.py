"""
Rich terminal renderer for the interactive Faultline CLI.

Used by faultline.py to surface real-time agent activity:
  - banner with target info and run folder path
  - pipeline step progress with timing
  - agent reasoning rendered as readable markdown
  - tool calls (cyan) and results (green)
  - findings (severity-colored panels)
  - plan/checklist tracking
  - file-generation events
  - HITL pause notices
  - phase timing
  - completion summary

All renderer methods are no-ops when invoked without a Console (defensive).
"""

import os
import re
import sys
import time
from typing import Optional, Union, List

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


SEVERITY_STYLES = {
    "critical": "red",
    "high": "orange3",
    "medium": "yellow",
    "low": "blue",
}

STEP_GLYPHS = {
    "running": "[bold cyan]○[/bold cyan]",
    "done": "[bold green]✓[/bold green]",
    "error": "[bold red]✗[/bold red]",
    "skipped": "[dim]–[/dim]",
}

# Tool category icons for visual grouping
TOOL_ICONS = {
    "list_project_files": "📂",
    "read_project_file": "📄",
    "analyze_project_structure": "🗺️",
    "run_deterministic_checks": "🔍",
    "index_project_documentation": "📚",
    "query_knowledge_base": "🔎",
    "validate_python_code": "✅",
    "run_functional_test": "🧪",
    "execute_chaos_campaign": "💥",
    "propose_code_patch": "🩹",
    "record_finding": "🚨",
    "request_user_input": "👤",
    "retrieve_stored_content": "📦",
    "copy_test_boilerplate": "📋",
    "discover_api_schema": "🌐",
}


def _coerce_text(content: Union[str, list, None]) -> str:
    """Anthropic models return content as a list of blocks. Extract text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content)


class CLIRenderer:
    def __init__(self, console: Optional[Console] = None, quiet: bool = False):
        self.console = console or Console()
        self.quiet = quiet
        self._tool_call_count = 0
        self._phase_start: Optional[float] = None
        self._current_plan: Optional[str] = None

    # ------------------------------------------------------------------
    # Banner / completion
    # ------------------------------------------------------------------

    def show_banner(self, target_dir: str, target_url: str, mode: str) -> None:
        body = (
            f"[bold]Target Dir:[/bold] {target_dir}\n"
            f"[bold]Target URL:[/bold] {target_url or '[dim]none[/dim]'}\n"
            f"[bold]Mode:[/bold] [magenta]{mode}[/magenta]"
        )
        self.console.print(Panel(
            body,
            title="[bold cyan]FAULTLINE[/bold cyan] [dim]interactive cli[/dim]",
            border_style="cyan",
            padding=(1, 2),
        ))

    def show_startup_dashboard(
        self,
        target_dir: str,
        target_url: str,
        mode: str,
        run_folder: str,
        session_id: str = "",
        model: str = "",
        budget_str: str = "",
        auth_status: str = "",
    ) -> None:
        """Consolidated startup dashboard — replaces scattered banner/info prints."""
        table = Table(
            show_header=False,
            border_style="cyan",
            title="[bold cyan]FAULTLINE[/bold cyan] [dim]interactive cli[/dim]",
            padding=(0, 2),
            expand=False,
            min_width=60,
        )
        table.add_column("key", style="bold", width=12)
        table.add_column("value")

        table.add_row("Target", target_dir)
        table.add_row("URL", target_url or "[dim]none[/dim]")
        table.add_row("Mode", f"[magenta]{mode}[/magenta]")
        if model:
            table.add_row("Model", f"[cyan]{model}[/cyan]")
        if budget_str:
            table.add_row("Budget", f"[dim]{budget_str}[/dim]")
        if auth_status:
            table.add_row("Auth", auth_status)
        table.add_row("Run", f"[bold]{run_folder}[/bold]")
        if session_id:
            table.add_row("Session", f"[dim]{session_id}[/dim]")

        self.console.print()
        self.console.print(table)
        self.console.print(
            "  [dim]Press [bold cyan]Esc[/bold cyan] at any time to "
            "pause and steer the agent.[/dim]\n"
        )

    def show_run_folder(self, path: str) -> None:
        self.console.print(
            f"  [bold green]Run folder:[/bold green] [bold]{path}[/bold]\n"
            f"  [dim]All reports, logs, and test scripts will be saved there.[/dim]"
        )

    def show_esc_hint(self) -> None:
        """Show the Esc key hint after the banner."""
        self.console.print(
            "  [dim]Press [bold cyan]Esc[/bold cyan] at any time to pause and steer the agent.[/dim]\n"
        )

    def show_campaign_estimate(
        self,
        endpoint_count: int = 0,
        auth_endpoints: int = 0,
        file_count: int = 0,
        max_turns: int = 40,
        schema_found: bool = False,
    ) -> None:
        """
        Show a brief difficulty / scope estimate before the agent starts.
        Helps the operator set expectations for campaign duration.
        """
        # Estimate complexity
        if endpoint_count > 30 or file_count > 100:
            complexity = "[red]HIGH[/red]"
            est_minutes = "20-40"
        elif endpoint_count > 10 or file_count > 40:
            complexity = "[yellow]MEDIUM[/yellow]"
            est_minutes = "10-25"
        else:
            complexity = "[green]LOW[/green]"
            est_minutes = "5-15"

        body_parts = []
        if endpoint_count:
            auth_note = f" ({auth_endpoints} authenticated)" if auth_endpoints else ""
            body_parts.append(f"  Endpoints: [bold]{endpoint_count}[/bold]{auth_note}")
        if file_count:
            body_parts.append(f"  Source files: [bold]{file_count}[/bold]")
        body_parts.append(f"  Estimated complexity: {complexity}")
        body_parts.append(f"  Budget: [bold]{max_turns}[/bold] turns (~{est_minutes} minutes)")
        if schema_found:
            body_parts.append("  Schema: [green]OK[/green] OpenAPI discovered")
        else:
            body_parts.append("  Schema: [dim]not found (agent will discover manually)[/dim]")

        self.console.print(Panel(
            "\n".join(body_parts),
            title="[bold yellow]Campaign Estimate[/bold yellow]",
            border_style="yellow",
            padding=(0, 1),
        ))

    def show_complete(self, report_path: str = "", auto_open: bool = False) -> None:
        body = "[bold green]Campaign complete.[/bold green]"
        if report_path:
            body += f"\n[dim]Report:[/dim] {report_path}"
        body += f"\n[dim]Tool calls observed:[/dim] {self._tool_call_count}"
        self.console.print(Panel(body, border_style="green", padding=(1, 2)))
        # Terminal bell — notify operator that campaign is done
        sys.stdout.write("\a")
        sys.stdout.flush()
        # Auto-open report if requested
        if auto_open and report_path:
            self._open_report(report_path)

    def _open_report(self, report_path: str) -> None:
        """Open the campaign report in the default application."""
        import webbrowser
        from pathlib import Path

        rp = Path(report_path)

        # Try known report files in preference order
        candidates = [
            rp / "agent_report.md",
            rp / "agent_report.html",
            rp / "live_report.md",
            rp / "pipeline_report.md",
        ]
        # If report_path is itself a file, open it directly
        if rp.is_file():
            target = rp
        else:
            target = next((c for c in candidates if c.exists()), None)

        if target:
            try:
                webbrowser.open(str(target.resolve().as_uri()))
                self.console.print(
                    f"  [green]+[/green] Opened report: [bold]{target.name}[/bold]"
                )
            except Exception as exc:
                self.console.print(
                    f"  [dim yellow]Could not auto-open report: {exc}[/dim yellow]"
                )
        else:
            self.console.print(
                f"  [dim]No report file found in {report_path} to auto-open.[/dim]"
            )

    def show_checkpoint_saved(self, path: str, turn: int = 0) -> None:
        """Confirmation when checkpoint is written."""
        self.console.print(
            f"  [green]✓[/green] Checkpoint saved (turn {turn}): [bold]{path}[/bold]"
        )

    def show_resumed(self, turn: int, run_folder: str) -> None:
        """Shown when resuming from checkpoint."""
        self.console.print(Panel(
            f"[bold cyan]Resumed[/bold cyan] from turn [bold]{turn}[/bold]\n"
            f"[dim]Run folder:[/dim] {run_folder}",
            title="[bold cyan]RESUME[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        ))

    def show_model_switch(self, old_model: str, new_model: str) -> None:
        """Shown when the LLM model is switched mid-campaign."""
        self.console.print(
            f"  [green]✓[/green] Model switched: "
            f"[dim]{old_model}[/dim] → [bold cyan]{new_model}[/bold cyan]"
        )

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def show_pipeline_step(self, name: str, status: str, detail: str = "") -> None:
        glyph = STEP_GLYPHS.get(status, "[?]")
        line = f"  {glyph} [bold]{name}[/bold]"
        if detail:
            line += f" [dim]- {detail}[/dim]"
        self.console.print(line)

    # ------------------------------------------------------------------
    # Agent stream — rich live output
    # ------------------------------------------------------------------

    def show_agent_iteration(self, n: int) -> None:
        self.console.print()
        self.console.print(Rule(
            f"[bold cyan]Agent Turn {n}[/bold cyan]",
            style="dim cyan",
        ))

    def show_agent_thinking(self, content: Union[str, list]) -> None:
        """
        Render the agent's reasoning as readable text.
        Shows the full content with markdown formatting — the operator
        needs to see everything to judge if the agent is on track.
        """
        text = _coerce_text(content).strip()
        if not text:
            return
        # Quiet mode: suppress agent reasoning panels entirely
        if self.quiet:
            return

        # Check for plan/checklist in the response
        if any(marker in text.lower() for marker in ["## plan", "## checklist", "- [ ]", "- [x]"]):
            self._current_plan = text

        # Render as markdown inside a subtle panel
        try:
            md = Markdown(text)
            self.console.print(Panel(
                md,
                border_style="dim",
                padding=(0, 1),
                expand=True,
            ))
        except Exception:
            # Fallback to plain text if markdown parsing fails
            self.console.print(f"  {text}")

    def show_tool_call(self, tool_name: str, args_summary: str = "") -> None:
        """Show a tool call with an icon and clear formatting."""
        self._tool_call_count += 1
        icon = TOOL_ICONS.get(tool_name, "⚡")
        suffix = f" [dim]{args_summary}[/dim]" if args_summary else ""
        self.console.print(
            f"\n  {icon} [bold cyan]{tool_name}[/bold cyan]{suffix}"
        )

    def show_tool_result(self, tool_name: str, result_summary: str = "") -> None:
        """Show tool result — longer display for important tools."""
        # Quiet mode: show only one-line summary for all tools
        if self.quiet:
            text = (result_summary or "").strip()
            oneline = text[:120].replace("\n", " ") if text else "(no output)"
            self.console.print(f"  [green]<[/green] [dim]{oneline}[/dim]")
            return

        text = (result_summary or "").strip()
        icon = TOOL_ICONS.get(tool_name, "⚡")

        # Determine display length based on tool importance
        important_tools = {
            "run_deterministic_checks", "run_functional_test",
            "execute_chaos_campaign", "analyze_project_structure",
        }

        if tool_name in important_tools:
            # Show more for important tools — up to 1000 chars
            max_len = 1000
        else:
            max_len = 400

        if len(text) > max_len:
            text = text[:max_len].rstrip() + f" [dim]... ({len(result_summary)} chars total)[/dim]"

        if text:
            # Multi-line results get a panel
            if "\n" in text and len(text) > 200:
                self.console.print(Panel(
                    text,
                    title=f"[green]{icon} {tool_name} result[/green]",
                    border_style="dim green",
                    padding=(0, 1),
                ))
            else:
                text_oneline = text.replace("\n", " ↵ ")
                self.console.print(f"  [green]←[/green] [dim]{text_oneline}[/dim]")
        else:
            self.console.print(f"  [green]←[/green] [dim](no output)[/dim]")

    # ------------------------------------------------------------------
    # Plan / checklist display
    # ------------------------------------------------------------------

    def show_plan_update(self, plan_text: str) -> None:
        """Display a plan or checklist update from the agent."""
        self._current_plan = plan_text
        try:
            md = Markdown(plan_text)
            self.console.print(Panel(
                md,
                title="[bold yellow]📋 Campaign Plan[/bold yellow]",
                border_style="yellow",
                padding=(0, 1),
            ))
        except Exception:
            self.console.print(plan_text)

    def show_progress_bar(
        self, turn: int, max_turns: int, plan_done: int, plan_total: int,
        token_pct: int, findings: int, elapsed_str: str = "",
    ) -> None:
        """Show a compact progress summary line between agent turns."""
        # Update terminal title bar so operator can see status from any window
        title = f"Faultline │ Turn {turn}/{max_turns} │ {findings} findings │ {token_pct}% tokens"
        if elapsed_str:
            title += f" │ {elapsed_str}"
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

        # Build visual progress bar for token usage
        bar_width = 20
        filled = int(bar_width * min(token_pct, 100) / 100)
        bar_color = "green" if token_pct < 50 else ("yellow" if token_pct < 80 else "red")
        bar = f"[{bar_color}]{'█' * filled}{'░' * (bar_width - filled)}[/{bar_color}]"

        # Plan progress
        if plan_total > 0:
            plan_bar_filled = int(bar_width * plan_done / plan_total)
            plan_bar = f"[cyan]{'█' * plan_bar_filled}{'░' * (bar_width - plan_bar_filled)}[/cyan]"
            plan_str = f"{plan_done}/{plan_total}"
        else:
            plan_bar = f"[dim]{'░' * bar_width}[/dim]"
            plan_str = "[yellow]no plan[/yellow]"

        elapsed_part = f" │ ⏱ {elapsed_str}" if elapsed_str else ""

        self.console.print(
            f"\n  [dim]┌─ Progress ──────────────────────────────────────────────┐[/dim]\n"
            f"  [dim]│[/dim] Turn [bold]{turn}[/bold]/{max_turns} │ "
            f"Plan {plan_bar} {plan_str} │ "
            f"Findings [bold]{findings}[/bold]{elapsed_part}\n"
            f"  [dim]│[/dim] Tokens {bar} {token_pct}%\n"
            f"  [dim]└────────────────────────────────────────────────────────┘[/dim]"
        )

    # ------------------------------------------------------------------
    # Phase timing
    # ------------------------------------------------------------------

    def start_phase(self) -> None:
        self._phase_start = time.monotonic()

    def show_phase_timing(self, phase: str) -> None:
        if self._phase_start is None:
            return
        elapsed = time.monotonic() - self._phase_start
        self._phase_start = None
        self.console.print(f"  [dim]  {phase} completed in {elapsed:.1f}s[/dim]")

    # ------------------------------------------------------------------
    # Findings & files
    # ------------------------------------------------------------------

    def show_finding(self, severity: str, title: str, detail: str = "") -> None:
        sev = (severity or "medium").lower()
        color = SEVERITY_STYLES.get(sev, "white")
        body = f"[bold]{title}[/bold]"
        if detail:
            body += f"\n[dim]{detail}[/dim]"
        self.console.print(Panel(
            body,
            title=f"[{color}]Finding: {sev.upper()}[/{color}]",
            border_style=color,
        ))

    def show_file_generated(self, path: str) -> None:
        self.console.print(f"  [green][+][/green] [bold]{path}[/bold]")

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    def show_hitl_request(self, prompt: str, is_sensitive: bool = False) -> None:
        title = "[bold yellow]Awaiting Sensitive Input[/bold yellow]" if is_sensitive else "[yellow]Awaiting Human Input[/yellow]"
        self.console.print(Panel(prompt, title=title, border_style="yellow", padding=(0, 2)))

    def show_message(self, text: str, style: str = "white") -> None:
        self.console.print(Text(text, style=style))

    # ------------------------------------------------------------------
    # CLI-mode warning
    # ------------------------------------------------------------------

    def show_cli_turn(self, turn: int, max_turns: int, cli_name: str) -> None:
        self.console.print(
            f"\n  [bold cyan][ {cli_name} — turn {turn}/{max_turns} ][/bold cyan]"
        )
        self._status = self.console.status(
            f"[dim]  {cli_name} is initializing…[/dim]",
            spinner="dots",
        )
        self._status.start()

    def show_cli_waiting(self, elapsed: int, cli_name: str) -> None:
        # Provide a subtle "ETA" based on typical turn durations
        eta_msg = ""
        if elapsed < 60:
            eta_msg = " [dim](typical turn: 2-3m)[/dim]"
        elif elapsed > 180:
            eta_msg = " [yellow](running longer than usual…)[/yellow]"
            
        if hasattr(self, "_status") and self._status:
            self._status.update(
                f"[dim]  {cli_name} is thinking… {elapsed}s elapsed{eta_msg}[/dim]"
            )
        else:
            self.console.print(
                f"  [dim]  {cli_name} is working… {elapsed}s elapsed{eta_msg}[/dim]"
            )

    def show_cli_turn_done(self, turn: int, done: bool) -> None:
        if hasattr(self, "_status") and self._status:
            self._status.stop()
        marker = "[bold green]finished[/bold green]" if done else "[dim]continuing…[/dim]"
        self.console.print(f"  [green]✓[/green] Turn {turn} complete — {marker}")

    def show_cli_turn_error(self, turn: int, error: str) -> None:
        if hasattr(self, "_status") and self._status:
            self._status.stop()
        short = error[:120].replace("\n", " ")
        self.console.print(
            f"  [bold red]![/bold red] Turn {turn} error: [dim]{short}[/dim]"
        )

    def show_cli_section(self, heading: str) -> None:
        """Surface a section heading extracted from the agent's response."""
        self.console.print(f"  [bold magenta]»[/bold magenta] [dim]{heading}[/dim]")

    def show_cli_mode_warning(self, cli_name: str) -> None:
        max_turns = __import__("os").environ.get("FAULTLINE_CLI_MAX_TURNS", "12")
        self.console.print(
            f"  [bold cyan]~[/bold cyan] [dim]Using {cli_name} CLI in multi-turn mode "
            f"(up to {max_turns} turns). Set FAULTLINE_CLI_MAX_TURNS to override.[/dim]"
        )


# ---------------------------------------------------------------------------
# Helpers used by core/agent.py to parse stream events
# ---------------------------------------------------------------------------

_FILE_PATH_RE = re.compile(
    r"((?:reports|\.aegis_patches|tests)[\\\/][^\s'\"\)\]]+\.(?:md|py|html|json|log))"
)
_FINDING_TITLE_RE = re.compile(r"recorded finding ['\"'](.+?)['\"']")


def extract_file_paths(text: str) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(_FILE_PATH_RE.findall(text)))


def extract_finding_title(text: str) -> Optional[str]:
    if not text:
        return None
    m = _FINDING_TITLE_RE.search(text)
    return m.group(1) if m else None


def summarize_args(args: dict, max_args: int = 3, max_val: int = 80) -> str:
    """Compact one-line summary of a tool call's arguments."""
    if not isinstance(args, dict):
        return ""
    parts: List[str] = []
    for k, v in list(args.items())[:max_args]:
        val = str(v).replace("\n", " ")
        if len(val) > max_val:
            val = val[:max_val] + "…"
        parts.append(f"{k}={val}")
    if len(args) > max_args:
        parts.append(f"+{len(args) - max_args} more")
    return ", ".join(parts)
