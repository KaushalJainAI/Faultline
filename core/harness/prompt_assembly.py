"""System prompt assembly pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


PROMPT_HINT_FILES = ("CLAUDE.md", "AGENTS.md", "AGENT.md")


def discover_prompt_hints(search_roots: Iterable[str]) -> List[str]:
    hints: List[str] = []
    for root in search_roots:
        base = Path(root)
        for name in PROMPT_HINT_FILES:
            p = base / name
            if p.exists():
                try:
                    hints.append(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
    return hints


def assemble_system_prompt(static_prompt: str, dynamic_hints: Iterable[str]) -> str:
    """
    Static prompt remains first to preserve prefix stability and caching.
    Dynamic hints are appended after.
    """
    parts = [static_prompt.strip()]
    for hint in dynamic_hints:
        h = (hint or "").strip()
        if h:
            parts.append(h)
    return "\n\n".join(parts)

