"""
core/progress_tracker.py

Tracks the agent's campaign progress: checklist state, token budget,
tool call history, and findings. Injects a compact progress summary
into the agent's message state between turns so the agent always
knows exactly where it stands.
"""

import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from langchain_core.messages import BaseMessage, SystemMessage, AIMessage


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """
    Rough token estimation: ~4 chars per token for English text.
    Good enough for budget awareness without needing tiktoken.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


MAX_TOKENS_PER_MESSAGE = 30_000  # Cap contribution of a single message to budget awareness

def estimate_message_tokens(msg: BaseMessage) -> int:
    """Estimate tokens in a single message (content + tool calls)."""
    content = getattr(msg, "content", "") or ""
    if isinstance(content, list):
        content = " ".join(
            b.get("text", str(b)) if isinstance(b, dict) else str(b)
            for b in content
        )
    
    # If the content is a summarized reference [REF:xxx], it's small.
    # If it's raw text, we cap it to prevent UI explosion from massive files.
    raw_tokens = estimate_tokens(content)
    
    # Tool calls add overhead
    tool_calls = getattr(msg, "tool_calls", None) or []
    for tc in tool_calls:
        raw_tokens += estimate_tokens(str(tc.get("args", {})))
        raw_tokens += 10  # function name overhead

    if raw_tokens > MAX_TOKENS_PER_MESSAGE:
        # Log for debugging but return capped value for UI/budget awareness
        # import logging here to avoid circularity if any
        import logging
        logging.getLogger("ProgressTracker").debug(
            f"Message size ({raw_tokens} tokens) exceeded cap. Capping at {MAX_TOKENS_PER_MESSAGE}."
        )
        return MAX_TOKENS_PER_MESSAGE

    return raw_tokens


# ---------------------------------------------------------------------------
# Checklist parser
# ---------------------------------------------------------------------------

_CHECKBOX_RE = re.compile(
    r"^(\s*)-\s*\[([ xX!\/])\]\s*(.+)$", re.MULTILINE
)


@dataclass
class ChecklistItem:
    text: str
    status: str  # "pending", "done", "blocked", "in_progress"

    @property
    def marker(self) -> str:
        return {
            "pending": "[ ]",
            "done": "[x]",
            "blocked": "[!]",
            "in_progress": "[/]",
        }.get(self.status, "[ ]")


def parse_checklist(text: str) -> List[ChecklistItem]:
    """Extract checklist items from markdown text."""
    items = []
    for match in _CHECKBOX_RE.finditer(text):
        marker = match.group(2).strip()
        label = match.group(3).strip()
        if marker in ("x", "X"):
            status = "done"
        elif marker == "!":
            status = "blocked"
        elif marker == "/":
            status = "in_progress"
        else:
            status = "pending"
        items.append(ChecklistItem(text=label, status=status))
    return items


def format_checklist(items: List[ChecklistItem]) -> str:
    """Render checklist items back to markdown."""
    if not items:
        return "(No plan created yet)"
    return "\n".join(f"- {item.marker} {item.text}" for item in items)


# ---------------------------------------------------------------------------
# ProgressTracker
# ---------------------------------------------------------------------------

# Per-phase LLM-call caps (Discovery, Test, Chaos, Report)
PHASE_ORDER = ["discovery", "test", "chaos", "report"]
PHASE_CAPS = {
    "discovery": 15,
    "test":      30,
    "chaos":     20,
    "report":    10,
}
# Keywords in tool names / checklist items that signal a phase transition
_PHASE_SIGNALS = {
    "discovery": {"list_project_files", "analyze_project_structure", "run_deterministic_checks",
                  "glob_and_read", "fetch_endpoint_bundle", "index_project_documentation"},
    "test":      {"run_functional_test", "copy_test_boilerplate", "read_run_folder_file",
                  "write_run_folder_file"},
    "chaos":     {"execute_chaos_campaign"},
    "report":    {"record_finding", "save_vulnerability_report", "summarize_to_report"},
}

WRAP_UP_BUDGET_PCT = 0.60  # Force wrap-up message at 60% token budget (was 85%)


@dataclass
class ProgressTracker:
    """
    Maintains running state of the campaign's progress.
    Call `update()` after each agent turn, then `build_context_message()`
    to get a SystemMessage to inject into the agent's state.
    """

    # Configurable limits
    max_turns: int = 100
    token_budget: int = 200_000  # Conservative context window budget

    # Running state
    turn: int = 0
    total_tokens_used: int = 0
    tool_calls_made: int = 0
    findings_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    checklist: List[ChecklistItem] = field(default_factory=list)
    tools_history: List[str] = field(default_factory=list)

    # Phase tracking
    current_phase: str = "discovery"
    phase_turns: dict = field(default_factory=lambda: {p: 0 for p in PHASE_ORDER})

    def _infer_phase_from_tool(self, tool_name: str) -> Optional[str]:
        for phase, signals in _PHASE_SIGNALS.items():
            if tool_name in signals:
                return phase
        return None

    def update(self, messages: List[BaseMessage], new_iteration: int, new_findings: int) -> None:
        """Update progress from the latest message state."""
        self.turn = new_iteration
        self.findings_count = new_findings

        # Re-estimate total tokens from all messages
        self.total_tokens_used = sum(estimate_message_tokens(m) for m in messages)

        # Count tool calls
        self.tool_calls_made = sum(
            len(getattr(m, "tool_calls", []) or [])
            for m in messages
            if isinstance(m, AIMessage)
        )

        # Extract latest checklist from the most recent AI message that has one
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                content = getattr(msg, "content", "") or ""
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in content
                    )
                items = parse_checklist(content)
                if items:
                    self.checklist = items
                    break

        # Track recent tool names + update phase turn counters
        for msg in messages:
            if isinstance(msg, AIMessage):
                for tc in (getattr(msg, "tool_calls", None) or []):
                    name = tc.get("name", "")
                    if name and (not self.tools_history or self.tools_history[-1] != name):
                        self.tools_history.append(name)
                    # Advance phase
                    inferred = self._infer_phase_from_tool(name)
                    if inferred:
                        self.current_phase = inferred
                        self.phase_turns[inferred] = self.phase_turns.get(inferred, 0) + 1

    def build_context_message(self) -> SystemMessage:
        """
        Build a compact SystemMessage that gives the agent full awareness
        of its progress, budget, and plan state.
        """
        elapsed = time.monotonic() - self.start_time
        elapsed_str = f"{elapsed / 60:.1f}m" if elapsed > 60 else f"{elapsed:.0f}s"

        tokens_pct = min(100, int(self.total_tokens_used / max(1, self.token_budget) * 100))
        turns_remaining = max(0, self.max_turns - self.turn)

        # Checklist summary
        if self.checklist:
            done = sum(1 for i in self.checklist if i.status == "done")
            total = len(self.checklist)
            blocked = sum(1 for i in self.checklist if i.status == "blocked")
            checklist_summary = f"{done}/{total} complete"
            if blocked:
                checklist_summary += f", {blocked} blocked"
            checklist_text = format_checklist(self.checklist)
        else:
            checklist_summary = "⚠️ No plan created yet — create one NOW"
            checklist_text = ""

        # Phase cap warning
        cap = PHASE_CAPS.get(self.current_phase, 999)
        phase_used = self.phase_turns.get(self.current_phase, 0)
        phase_remaining = max(0, cap - phase_used)
        if phase_used >= cap:
            phase_warning = (
                f"⚠️ PHASE CAP REACHED: {self.current_phase.upper()} phase has used "
                f"{phase_used}/{cap} allocated turns. "
                f"ADVANCE to the next phase immediately: "
                f"{PHASE_ORDER[min(PHASE_ORDER.index(self.current_phase)+1, len(PHASE_ORDER)-1)].upper()}."
            )
        elif phase_remaining <= 3:
            phase_warning = (
                f"⚠️ {phase_remaining} turn(s) left in {self.current_phase.upper()} phase "
                f"(cap={cap}). Wrap up this phase and advance."
            )
        else:
            phase_warning = f"Phase: {self.current_phase.upper()} — {phase_used}/{cap} turns used"

        # Token budget warning — trigger at 60%, not 85%
        if tokens_pct > 85:
            budget_warning = "⚠️ CRITICAL: Token budget nearly exhausted. Wrap up and write the report NOW."
        elif tokens_pct > 60:
            budget_warning = (
                "⚠️ WARNING: Over 60% of token budget used. "
                "Call save_vulnerability_report NOW to preserve findings, then continue."
            )
        elif tokens_pct > 50:
            budget_warning = "Note: Over half the token budget used. Prioritize high-value actions."
        else:
            budget_warning = ""

        # Build the context block
        lines = [
            "═══ PROGRESS STATUS ═══",
            f"Turn: {self.turn}/{self.max_turns} ({turns_remaining} remaining)",
            f"Elapsed: {elapsed_str}",
            f"Token Budget: ~{self.total_tokens_used:,}/{self.token_budget:,} ({tokens_pct}% used)",
            f"Tool Calls: {self.tool_calls_made}",
            f"Findings Recorded: {self.findings_count}",
            f"Plan Progress: {checklist_summary}",
            f"{phase_warning}",
        ]

        if budget_warning:
            lines.append(f"\n{budget_warning}")

        if checklist_text:
            lines.append(f"\n### Current Checklist\n{checklist_text}")

        # Recent tools (last 5)
        if self.tools_history:
            recent = self.tools_history[-5:]
            lines.append(f"\nRecent tools: {' → '.join(recent)}")

        lines.append("═══════════════════════")

        return SystemMessage(content="\n".join(lines))

    @property
    def is_budget_critical(self) -> bool:
        return self.total_tokens_used > self.token_budget * WRAP_UP_BUDGET_PCT

    @property
    def is_phase_capped(self) -> bool:
        cap = PHASE_CAPS.get(self.current_phase, 999)
        return self.phase_turns.get(self.current_phase, 0) >= cap

    @property
    def is_over_turns(self) -> bool:
        return self.turn >= self.max_turns
