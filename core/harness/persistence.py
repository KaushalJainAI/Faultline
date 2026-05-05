"""Session persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class AppendOnlySessionLog:
    """Append-only JSONL event log for replayable sessions."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

