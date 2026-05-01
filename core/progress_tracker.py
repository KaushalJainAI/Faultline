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

@dataclass
class ProgressTracker:
    """
    Maintains running state of the campaign's progress.
    Call `update()` after each agent turn, then `build_context_message()`
    to get a SystemMessage to inject into the agent's state.
    """

    # Configurable limits
    max_turns: int = 40
    token_budget: int = 120_000  # Conservative context window budget

    # Running state
    turn: int = 0
    total_tokens_used: int = 0
    tool_calls_made: int = 0
    findings_count: int = 0
    start_time: float = field(default_factory=time.monotonic)
    checklist: List[ChecklistItem] = field(default_factory=list)
    tools_history: List[str] = field(default_factory=list)

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

        # Track recent tool names
        for msg in messages:
            if isinstance(msg, AIMessage):
                for tc in (getattr(msg, "tool_calls", None) or []):
                    name = tc.get("name", "")
                    if name and (not self.tools_history or self.tools_history[-1] != name):
                        self.tools_history.append(name)

    def build_context_message(self) -> SystemMessage:
        """
        Build a compact SystemMessage that gives the agent full awareness
        of its progress, budget, and plan state.
        """
        elapsed = time.monotonic() - self.start_time
        elapsed_str = f"{elapsed / 60:.1f}m" if elapsed > 60 else f"{elapsed:.0f}s"

        tokens_pct = min(100, int(self.total_tokens_used / self.token_budget * 100))
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

        # Token budget warning
        if tokens_pct > 85:
            budget_warning = "⚠️ CRITICAL: Token budget nearly exhausted. Wrap up and write the report NOW."
        elif tokens_pct > 70:
            budget_warning = "⚠️ WARNING: Over 70% of token budget used. Start focusing on reporting."
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
        return self.total_tokens_used > self.token_budget * 0.85

    @property
    def is_over_turns(self) -> bool:
        return self.turn >= self.max_turns
