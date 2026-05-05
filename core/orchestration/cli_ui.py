"""Rich terminal renderer for Faultline interactive CLI."""

import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional, Union

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

SEVERITY_STYLES = {"critical": "red", "high": "orange3", "medium": "yellow", "low": "blue"}
STEP_GLYPHS = {
    "running": "[bold cyan]>[/bold cyan]",
    "done": "[bold green]+[/bold green]",
    "error": "[bold red]![/bold red]",
    "skipped": "[dim]-[/dim]",
}
TOOL_ICONS = {
    "list_project_files": "DIR",
    "read_project_file": "FILE",
    "analyze_project_structure": "MAP",
    "run_deterministic_checks": "SCAN",
    "index_project_documentation": "DOCS",
    "query_knowledge_base": "FIND",
    "validate_python_code": "OK",
    "run_functional_test": "TEST",
    "execute_chaos_campaign": "CHAOS",
    "propose_code_patch": "PATCH",
    "record_finding": "ALERT",
    "request_user_input": "USER",
    "retrieve_stored_content": "BOX",
    "copy_test_boilerplate": "COPY",
    "discover_api_schema": "API",
    "summarize_to_report": "REPORT",
    "list_run_folder_files": "DIR",
    "read_run_folder_file": "FILE",
}


def _coerce_text(content: Union[str, list, None]) -> str:
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
        self._status = None

    def show_banner(self, target_dir: str, target_url: str, mode: str) -> None:
        body = (
            f"[bold]Target Dir:[/bold] {target_dir}\n"
            f"[bold]Target URL:[/bold] {target_url or '[dim]none[/dim]'}\n"
            f"[bold]Mode:[/bold] [magenta]{mode}[/magenta]"
        )
        self.console.print(Panel(body, title="[bold cyan]FAULTLINE[/bold cyan]", border_style="cyan"))

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
        table = Table(show_header=False, border_style="cyan", title="[bold cyan]FAULTLINE[/bold cyan]")
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
        self.console.print("  [dim]Esc: pause and steer the agent at any time[/dim]\n")

    def show_run_folder(self, path: str) -> None:
        self.console.print(f"  [bold green]Run folder:[/bold green] [bold]{path}[/bold]")

    def show_esc_hint(self) -> None:
        self.console.print("  [dim]Esc: pause and steer the agent at any time[/dim]\n")

    def show_campaign_estimate(
        self, endpoint_count: int = 0, auth_endpoints: int = 0, file_count: int = 0, max_turns: int = 40, schema_found: bool = False
    ) -> None:
        if endpoint_count > 30 or file_count > 100:
            complexity, est_minutes = "[red]HIGH[/red]", "20-40"
        elif endpoint_count > 10 or file_count > 40:
            complexity, est_minutes = "[yellow]MEDIUM[/yellow]", "10-25"
        else:
            complexity, est_minutes = "[green]LOW[/green]", "5-15"
        parts = []
        if endpoint_count:
            auth_note = f" ({auth_endpoints} authenticated)" if auth_endpoints else ""
            parts.append(f"  Endpoints: [bold]{endpoint_count}[/bold]{auth_note}")
        if file_count:
            parts.append(f"  Source files: [bold]{file_count}[/bold]")
        parts.append(f"  Estimated complexity: {complexity}")
        parts.append(f"  Budget: [bold]{max_turns}[/bold] turns (~{est_minutes} minutes)")
        parts.append("  Schema: [green]OK[/green] OpenAPI discovered" if schema_found else "  Schema: [dim]not found[/dim]")
        self.console.print(Panel("\n".join(parts), title="[bold yellow]Campaign Estimate[/bold yellow]", border_style="yellow"))

    def show_complete(self, report_path: str = "", auto_open: bool = False, run_folder: str = "") -> None:
        body = "[bold green]Campaign complete.[/bold green]\n\n"
        rf = Path(run_folder or report_path)
        if rf.is_file():
            rf = rf.parent
        body += "  [bold]Generated Artifacts:[/bold]\n"
        reports = [
            ("Final Findings", "vulnerability_report.md"),
            ("Activity Log", "live_report.md"),
            ("Pipeline Log", "pipeline_report.md"),
            ("API Index", "api_test_data.json"),
            ("Full Transcript", "transcript.txt"),
        ]
        found_any = False
        for label, filename in reports:
            p = rf / filename
            if p.exists():
                body += f"  - {label:15}: {p}\n"
                found_any = True
        if not found_any:
            body += f"  - Report: {report_path}\n"
        body += f"\n  [dim]Tool calls observed:[/dim] {self._tool_call_count}\n"
        if run_folder or report_path:
            cmd_path = run_folder or report_path
            body += f"\n  [bold cyan]Resume Command:[/bold cyan]\n  python faultline.py --resume {cmd_path}\n"
        self.console.print(Panel(body, border_style="green", padding=(1, 2)))
        sys.stdout.write("\a")
        sys.stdout.flush()
        if auto_open and report_path:
            self._open_report(report_path)

    def _open_report(self, report_path: str) -> None:
        import webbrowser

        rp = Path(report_path)
        candidates = [rp / "agent_report.md", rp / "agent_report.html", rp / "vulnerability_report.md"]
        for c in candidates:
            if c.exists():
                webbrowser.open(c.as_uri())
                return

    def show_checkpoint_saved(self, path: str, turn: int = 0) -> None:
        self.console.print(f"  [green]+[/green] Checkpoint saved (turn {turn}): [bold]{path}[/bold]")

    def show_resumed(self, turn: int, run_folder: str) -> None:
        self.console.print(Panel(f"[bold cyan]Resumed[/bold cyan] from turn [bold]{turn}[/bold]\n[dim]Run:[/dim] {run_folder}", border_style="cyan"))

    def show_model_switch(self, old_model: str, new_model: str) -> None:
        self.console.print(f"  [green]+[/green] Model switched: [dim]{old_model}[/dim] -> [bold cyan]{new_model}[/bold cyan]")

    def show_pipeline_step(self, name: str, status: str, detail: str = "") -> None:
        glyph = STEP_GLYPHS.get(status, "[?]")
        line = f"  {glyph} [bold]{name}[/bold]"
        if detail:
            line += f" [dim]- {detail}[/dim]"
        self.console.print(line)

    def show_agent_iteration(self, n: int) -> None:
        self.console.print()
        self.console.print(Rule(f"[bold cyan]Agent Turn {n}[/bold cyan]", style="dim cyan"))

    def show_agent_thinking(self, content: Union[str, list]) -> None:
        text = _coerce_text(content).strip()
        if not text or self.quiet:
            return
        if any(marker in text.lower() for marker in ["## plan", "## checklist", "- [ ]", "- [x]"]):
            self._current_plan = text
        try:
            self.console.print(Panel(Markdown(text), border_style="dim", padding=(0, 1), expand=True))
        except Exception:
            self.console.print(f"  {text}")

    def show_tool_call(self, tool_name: str, args_summary: str = "") -> None:
        self._tool_call_count += 1
        icon = TOOL_ICONS.get(tool_name, "TOOL")
        suffix = f" [dim]{args_summary}[/dim]" if args_summary else ""
        self.console.print(f"\n  {icon} [bold cyan]{tool_name}[/bold cyan]{suffix}")

    def show_tool_result(self, tool_name: str, result_summary: str = "") -> None:
        text = (result_summary or "").strip()
        if self.quiet:
            oneline = text[:120].replace("\n", " ") if text else "(no output)"
            self.console.print(f"  [green]<[/green] [dim]{oneline}[/dim]")
            return
        max_len = 1000 if tool_name in {"run_deterministic_checks", "run_functional_test", "execute_chaos_campaign"} else 400
        if len(text) > max_len:
            text = text[:max_len].rstrip() + f" [dim]... ({len(result_summary)} chars total)[/dim]"
        if not text:
            self.console.print("  [green]<[/green] [dim](no output)[/dim]")
        elif "\n" in text and len(text) > 200:
            self.console.print(Panel(text, title=f"[green]{tool_name} result[/green]", border_style="dim green"))
        else:
            self.console.print(f"  [green]<[/green] [dim]{text.replace(chr(10), ' | ')}[/dim]")

    def show_plan_update(self, plan_text: str) -> None:
        self._current_plan = plan_text
        try:
            self.console.print(Panel(Markdown(plan_text), title="[bold yellow]Campaign Plan[/bold yellow]", border_style="yellow"))
        except Exception:
            self.console.print(plan_text)

    def show_progress_bar(
        self,
        turn: int,
        max_turns: int,
        plan_done: int,
        plan_total: int,
        token_pct: int,
        findings: int,
        elapsed_str: str = "",
        current_tokens: int = 0,
        max_tokens: int = 0,
        budget_used_tokens: Optional[int] = None,
        budget_limit_tokens: Optional[int] = None,
    ) -> None:
        title = f"Faultline | Turn {turn}/{max_turns} | findings {findings} | budget {token_pct}%"
        if elapsed_str:
            title += f" | {elapsed_str}"
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

        width = 22

        def _bar(pct: int, color: str) -> str:
            filled = int(width * max(0, min(100, pct)) / 100)
            return f"[{color}]{'#' * filled}{'-' * (width - filled)}[/{color}]"

        ctx_pct = int((current_tokens / max_tokens) * 100) if max_tokens > 0 else 0
        ctx_color = "green" if ctx_pct < 60 else ("yellow" if ctx_pct < 85 else "red")
        ctx_bar = _bar(ctx_pct, ctx_color)
        ctx_str = f"{current_tokens//1000}k/{max(1, max_tokens)//1000}k ({ctx_pct}%)"
        budget_bar = _bar(token_pct, "blue")
        budget_str = f"{token_pct}%"
        if budget_used_tokens is not None and budget_limit_tokens:
            budget_str = f"{budget_used_tokens//1000}k/{budget_limit_tokens//1000}k ({token_pct}%)"

        if plan_total > 0:
            plan_pct = int((plan_done / max(1, plan_total)) * 100)
            plan_bar = _bar(plan_pct, "cyan")
            plan_str = f"{plan_done}/{plan_total}"
        else:
            plan_bar = _bar(0, "dim")
            plan_str = "none"
        elapsed_part = f" | elapsed {elapsed_str}" if elapsed_str else ""

        self.console.print(
            f"\n  [dim]+- Campaign Progress --------------------------------------------+[/dim]\n"
            f"  [dim]|[/dim] Turn [bold]{turn}[/bold]/{max_turns} | Findings [bold]{findings}[/bold]{elapsed_part}\n"
            f"  [dim]|[/dim] Plan    {plan_bar} {plan_str}\n"
            f"  [dim]|[/dim] Context {ctx_bar} {ctx_str}\n"
            f"  [dim]|[/dim] Budget  {budget_bar} {budget_str}\n"
            f"  [dim]+---------------------------------------------------------------+[/dim]"
        )

    def start_phase(self) -> None:
        self._phase_start = time.monotonic()

    def show_phase_timing(self, phase: str) -> None:
        if self._phase_start is None:
            return
        elapsed = time.monotonic() - self._phase_start
        self._phase_start = None
        self.console.print(f"  [dim]{phase} completed in {elapsed:.1f}s[/dim]")

    def show_finding(self, severity: str, title: str, detail: str = "") -> None:
        sev = (severity or "medium").lower()
        color = SEVERITY_STYLES.get(sev, "white")
        body = f"[bold]{title}[/bold]"
        if detail:
            body += f"\n[dim]{detail}[/dim]"
        self.console.print(Panel(body, title=f"[{color}]Finding: {sev.upper()}[/{color}]", border_style=color))

    def show_file_generated(self, path: str) -> None:
        self.console.print(f"  [green][+][/green] [bold]{path}[/bold]")

    def show_hitl_request(self, prompt: str, is_sensitive: bool = False) -> None:
        title = "[bold yellow]Awaiting Sensitive Input[/bold yellow]" if is_sensitive else "[yellow]Awaiting Human Input[/yellow]"
        self.console.print(Panel(prompt, title=title, border_style="yellow", padding=(0, 2)))

    def show_message(self, text: str, style: str = "white") -> None:
        self.console.print(Text(text, style=style))

    def show_cli_turn(self, turn: int, max_turns: int, cli_name: str) -> None:
        self.console.print(f"\n  [bold cyan][ {cli_name} - turn {turn}/{max_turns} ][/bold cyan]")
        self._status = self.console.status(f"[dim]{cli_name} is initializing...[/dim]", spinner="dots")
        self._status.start()

    def show_cli_waiting(self, elapsed: int, cli_name: str) -> None:
        eta_msg = " [dim](typical turn: 2-3m)[/dim]" if elapsed < 60 else (" [yellow](running longer than usual...)[/yellow]" if elapsed > 180 else "")
        if self._status:
            self._status.update(f"[dim]{cli_name} is thinking... {elapsed}s elapsed{eta_msg}[/dim]")
        else:
            self.console.print(f"  [dim]{cli_name} is working... {elapsed}s elapsed{eta_msg}[/dim]")

    def show_cli_turn_done(self, turn: int, done: bool) -> None:
        if self._status:
            self._status.stop()
            self._status = None
        marker = "[bold green]finished[/bold green]" if done else "[dim]continuing...[/dim]"
        self.console.print(f"  [green]+[/green] Turn {turn} complete - {marker}")

    def show_cli_turn_error(self, turn: int, error: str) -> None:
        if self._status:
            self._status.stop()
            self._status = None
        short = error[:120].replace("\n", " ")
        self.console.print(f"  [bold red]![/bold red] Turn {turn} error: [dim]{short}[/dim]")

    def show_cli_section(self, heading: str) -> None:
        self.console.print(f"  [bold magenta]>>[/bold magenta] [dim]{heading}[/dim]")

    def show_cli_mode_warning(self, cli_name: str) -> None:
        max_turns = os.environ.get("FAULTLINE_CLI_MAX_TURNS", "12")
        self.console.print(f"  [bold cyan]~[/bold cyan] [dim]Using {cli_name} CLI mode (up to {max_turns} turns).[/dim]")


_FILE_PATH_RE = re.compile(r"((?:reports|\.aegis_patches|tests)[\\\/][^\s'\"\)\]]+\.(?:md|py|html|json|log))")
_FINDING_TITLE_RE = re.compile(r"recorded finding ['\"'](.+?)['\"]")


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
    if not isinstance(args, dict):
        return ""
    parts: List[str] = []
    for k, v in list(args.items())[:max_args]:
        val = str(v).replace("\n", " ")
        if len(val) > max_val:
            val = val[:max_val] + "..."
        parts.append(f"{k}={val}")
    if len(args) > max_args:
        parts.append(f"+{len(args) - max_args} more")
    return ", ".join(parts)

