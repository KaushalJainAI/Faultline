"""
Rich terminal renderer for the interactive Faultline CLI.

Used by faultline.py to surface real-time agent activity:
  - banner with target info
  - pipeline step progress
  - agent reasoning (dim italics)
  - tool calls (cyan) and results (green)
  - findings (severity-colored panels)
  - file-generation events
  - HITL pause notices
  - completion summary

All renderer methods are no-ops when invoked without a Console (defensive).
"""

import re
from typing import Optional, Union, List

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


SEVERITY_STYLES = {
    "critical": "red",
    "high": "orange3",
    "medium": "yellow",
    "low": "blue",
}

STEP_GLYPHS = {
    "running": "[bold cyan]o[/bold cyan]",
    "done": "[bold green]+[/bold green]",
    "error": "[bold red]x[/bold red]",
    "skipped": "[dim]-[/dim]",
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
    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self._tool_call_count = 0

    # ------------------------------------------------------------------
    # Banner / completion
    # ------------------------------------------------------------------

    def show_banner(self, target_dir: str, target_url: str, mode: str) -> None:
        body = (
            f"[bold]Target Dir:[/bold] {target_dir}\n"
            f"[bold]Target URL:[/bold] {target_url or '[dim]none[/dim]'}\n"
            f"[bold]Mode:[/bold] [magenta]{mode}[/magenta]"
        )
        panel = Panel(
            body,
            title="[bold cyan]FAULTLINE[/bold cyan] [dim]interactive cli[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )
        self.console.print(panel)

    def show_complete(self, report_path: str = "") -> None:
        body = "[bold green]Campaign complete.[/bold green]"
        if report_path:
            body += f"\n[dim]Report:[/dim] {report_path}"
        body += f"\n[dim]Tool calls observed:[/dim] {self._tool_call_count}"
        self.console.print(Panel(body, border_style="green", padding=(1, 2)))

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
    # Agent stream
    # ------------------------------------------------------------------

    def show_agent_thinking(self, content: Union[str, list]) -> None:
        text = _coerce_text(content).strip()
        if not text:
            return
        if len(text) > 300:
            text = text[:300].rstrip() + "..."
        self.console.print(Text(f"  {text}", style="dim italic"))

    def show_tool_call(self, tool_name: str, args_summary: str = "") -> None:
        self._tool_call_count += 1
        suffix = f"([dim]{args_summary}[/dim])" if args_summary else "()"
        self.console.print(
            f"  [bold cyan]->[/bold cyan] [bold]{tool_name}[/bold]{suffix}"
        )

    def show_tool_result(self, tool_name: str, result_summary: str = "") -> None:
        text = (result_summary or "").strip()
        if len(text) > 200:
            text = text[:200].rstrip() + "..."
        text = text.replace("\n", " ")
        if text:
            self.console.print(f"  [green]<-[/green] [dim]{text}[/dim]")
        else:
            self.console.print(f"  [green]<-[/green] [dim](no output)[/dim]")

    # ------------------------------------------------------------------
    # Findings & files
    # ------------------------------------------------------------------

    def show_finding(self, severity: str, title: str, detail: str = "") -> None:
        sev = (severity or "medium").lower()
        color = SEVERITY_STYLES.get(sev, "white")
        body = f"[bold]{title}[/bold]"
        if detail:
            body += f"\n[dim]{detail}[/dim]"
        panel = Panel(
            body,
            title=f"[{color}]Finding: {sev.upper()}[/{color}]",
            border_style=color,
        )
        self.console.print(panel)

    def show_file_generated(self, path: str) -> None:
        self.console.print(f"  [green][+][/green] [bold]{path}[/bold]")

    # ------------------------------------------------------------------
    # HITL
    # ------------------------------------------------------------------

    def show_hitl_request(self, prompt: str, is_sensitive: bool = False) -> None:
        title = "[yellow]Awaiting Human Input[/yellow]"
        if is_sensitive:
            title = "[bold yellow]Awaiting Sensitive Input[/bold yellow]"
        self.console.print(
            Panel(prompt, title=title, border_style="yellow", padding=(0, 2))
        )

    def show_message(self, text: str, style: str = "white") -> None:
        """Generic single-line message escape hatch."""
        self.console.print(Text(text, style=style))


# ---------------------------------------------------------------------------
# Helpers used by core/agent.py to parse stream events
# ---------------------------------------------------------------------------

_FILE_PATH_RE = re.compile(
    r"((?:reports|\.aegis_patches|tests)[\\/][^\s'\"\)\]]+\.(?:md|py|html|json|log))"
)
_FINDING_TITLE_RE = re.compile(r"recorded finding ['\"](.+?)['\"]")


def extract_file_paths(text: str) -> List[str]:
    if not text:
        return []
    return list(dict.fromkeys(_FILE_PATH_RE.findall(text)))


def extract_finding_title(text: str) -> Optional[str]:
    if not text:
        return None
    m = _FINDING_TITLE_RE.search(text)
    return m.group(1) if m else None


def summarize_args(args: dict, max_args: int = 3, max_val: int = 60) -> str:
    """Compact one-line summary of a tool call's arguments."""
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
