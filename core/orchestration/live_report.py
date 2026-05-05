"""
core/live_report.py

Append-only live report written to <run_folder>/live_report.md.

The file is created at campaign start (with pipeline findings pre-filled)
and grown in real-time as the agent calls record_finding() and
save_vulnerability_report(). Every append is flushed to disk immediately
so a crash never loses a finding that was already recorded.

On --resume, faultline.py reads this file and injects its contents into
the initial prompt so the agent knows exactly what was already done.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional


_HEADER = """\
# Faultline Live Report

**Target:** {target_dir}
**URL:** {target_url}
**Mode:** {mode}
**Started:** {started_at}

---

## Pipeline Findings

{pipeline_block}

---

## Agent Findings

"""


class LiveReport:
    """Thread-safe, async-friendly append-only markdown report."""

    def __init__(
        self,
        run_folder: str,
        target_dir: str = "",
        target_url: str = "",
        mode: str = "hybrid",
        pipeline_report_path: str = "",
    ) -> None:
        self.path = Path(run_folder) / "live_report.md"
        self.jsonl_path = Path(run_folder) / "findings.jsonl"
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

        # Only create the file if it doesn't exist â€” preserve it across resume
        if not self.path.exists():
            pipeline_block = self._read_pipeline_summary(pipeline_report_path)
            self.path.write_text(
                _HEADER.format(
                    target_dir=target_dir or "(not set)",
                    target_url=target_url or "(not set)",
                    mode=mode,
                    started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    pipeline_block=pipeline_block or "_No pipeline report found._",
                ),
                encoding="utf-8",
            )

    # ------------------------------------------------------------------
    # Append helpers
    # ------------------------------------------------------------------

    async def append_finding(self, finding: dict) -> None:
        """Append one structured finding block. Called after record_finding() succeeds."""
        ts = datetime.now().strftime("%H:%M:%S")
        sev = (finding.get("severity") or "UNKNOWN").upper()
        title = finding.get("title", "Untitled")
        fp = finding.get("file_path", "") or "N/A"
        ln = finding.get("line_number", "")
        loc = f"{fp}:{ln}" if ln else fp

        block = (
            f"\n### [{sev}] {title} â€” {ts}\n\n"
            f"- **Category:** {finding.get('category', '')}\n"
            f"- **Location:** {loc}\n"
            f"- **Summary:** {finding.get('summary', '')}\n"
        )
        if finding.get("evidence"):
            block += f"- **Evidence:** {finding['evidence']}\n"
        if finding.get("reproduction_steps"):
            block += f"- **Reproduce:** {finding['reproduction_steps']}\n"
        if finding.get("suggested_fix"):
            block += f"- **Fix:** {finding['suggested_fix']}\n"
        block += "\n---\n"

        await self._append(block)
        self._append_jsonl(finding)

    def append_finding_sync(self, finding: dict) -> None:
        """
        Synchronous append â€” safe to call from sync code (LangGraph @tool wrappers).
        Mirrors append_finding() but uses a threading.Lock and blocking I/O so the
        write is guaranteed to land on disk before the caller returns.
        """
        ts = datetime.now().strftime("%H:%M:%S")
        sev = (finding.get("severity") or "UNKNOWN").upper()
        title = finding.get("title", "Untitled")
        fp = finding.get("file_path", "") or "N/A"
        ln = finding.get("line_number", "")
        loc = f"{fp}:{ln}" if ln else fp

        block = (
            f"\n### [{sev}] {title} â€” {ts}\n\n"
            f"- **Category:** {finding.get('category', '')}\n"
            f"- **Location:** {loc}\n"
            f"- **Summary:** {finding.get('summary', '')}\n"
        )
        if finding.get("evidence"):
            block += f"- **Evidence:** {finding['evidence']}\n"
        if finding.get("reproduction_steps"):
            block += f"- **Reproduce:** {finding['reproduction_steps']}\n"
        if finding.get("suggested_fix"):
            block += f"- **Fix:** {finding['suggested_fix']}\n"
        block += "\n---\n"

        with self._sync_lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(block)
                f.flush()
        self._append_jsonl(finding)

    def _append_jsonl(self, finding: dict) -> None:
        """Append a single finding as a JSON line for machine consumption."""
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            **finding,
        }
        try:
            with self._sync_lock:
                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
        except Exception:
            pass

    async def append_section(self, heading: str, content: str) -> None:
        """Append a freeform section (agent synthesis, session end, etc.)."""
        block = f"\n## {heading}\n\n{content}\n\n---\n"
        await self._append(block)

    def append_section_sync(self, heading: str, content: str) -> None:
        """Synchronous variant of append_section â€” safe to call from sync code."""
        block = f"\n## {heading}\n\n{content}\n\n---\n"
        with self._sync_lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(block)
                f.flush()

    def write_heartbeat_sync(
        self,
        turn: int,
        max_turns: int,
        llm_calls: int,
        max_llm_calls: int,
        token_pct: int,
        findings: int,
        last_action: str = "",
    ) -> None:
        """
        Rewrite the ## Live Status section near the top of live_report.md so the
        operator always sees current progress without appending noise.
        Called after every agent turn.
        """
        ts = datetime.now().strftime("%H:%M:%S")
        bar_filled = round(token_pct / 10)
        bar = "â–ˆ" * bar_filled + "â–‘" * (10 - bar_filled)
        budget_str = f"`{bar}` {token_pct}%"
        last = f" Â· last: `{last_action}`" if last_action else ""
        line = (
            f"> **[{ts}]** Turn {turn}/{max_turns} "
            f"Â· LLM calls {llm_calls}/{max_llm_calls} "
            f"Â· tokens {budget_str} "
            f"Â· findings {findings}"
            f"{last}"
        )
        status_block = f"\n## Live Status\n\n{line}\n\n---\n"

        with self._sync_lock:
            try:
                text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
                # Replace existing ## Live Status block if present
                import re as _re
                pattern = r"\n## Live Status\n.*?(?=\n## |\Z)"
                if _re.search(pattern, text, flags=_re.DOTALL):
                    text = _re.sub(pattern, status_block, text, flags=_re.DOTALL)
                else:
                    # Insert after the header separator (first ---)
                    sep_idx = text.find("\n---\n")
                    if sep_idx != -1:
                        text = text[: sep_idx + 5] + status_block + text[sep_idx + 5 :]
                    else:
                        text += status_block
                self.path.write_text(text, encoding="utf-8")
            except Exception:
                pass

    async def append_session_end(self, turn: int, reason: str = "completed") -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = (
            f"\n## Run End\n\n"
            f"- **Timestamp:** {ts}\n"
            f"- **Reason:** {reason}\n"
            f"- **Turns completed:** {turn}\n\n---\n"
        )
        await self._append(block)

    # ------------------------------------------------------------------
    # Resume helper
    # ------------------------------------------------------------------

    def read_for_resume(self) -> str:
        """Return the full live_report.md content for injection into the resume prompt."""
        if self.path.exists():
            return self.path.read_text(encoding="utf-8")
        return ""

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _append(self, text: str) -> None:
        async with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)

    @staticmethod
    def _read_pipeline_summary(pipeline_report_path: str) -> str:
        """Extract the Findings section from the pipeline report, or return a notice."""
        if not pipeline_report_path:
            return "_Pipeline phase not run._"
        p = Path(pipeline_report_path)
        if not p.exists():
            return "_Pipeline report not found._"
        try:
            text = p.read_text(encoding="utf-8")
            # Grab everything from the first ## Findings heading onward (up to next top-level ##)
            match = re.search(r"(## Findings.*?)(?=\n## |\Z)", text, re.DOTALL)
            if match:
                return match.group(1).strip()
            # Fallback: return first 3000 chars
            return text[:3000].strip()
        except Exception:
            return "_Could not read pipeline report._"

