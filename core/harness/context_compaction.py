"""Context management and compaction settings."""

from dataclasses import dataclass


@dataclass
class CompactionPolicy:
    """
    Defines when compaction should trigger.
    Ratio maps to fraction of model context window.
    """

    trigger_ratio: float = 0.85
    keep_recent_turns: int = 3

