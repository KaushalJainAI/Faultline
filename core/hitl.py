"""
Human-in-the-Loop (HITL) manager for Faultline.

Permission prompts are printed inline to stdout (like Claude Code) — no Rich
modals. The operator sees the tool name and description, then types y or N.
A 30-second timeout defaults to deny so a missed prompt never stalls the run.

When HITL is disabled (headless, REST API) all methods return safe defaults.
"""

import asyncio
import logging
import sys
import threading
from typing import Optional

logger = logging.getLogger("FaultlineHITL")

_HITL_ENABLED: bool = False
HITL_PROMPT_TIMEOUT: int = 30   # seconds before defaulting to deny


def enable_hitl() -> None:
    """Opt in to interactive prompts. Called only by the CLI entry point."""
    global _HITL_ENABLED
    _HITL_ENABLED = True


def disable_hitl() -> None:
    global _HITL_ENABLED
    _HITL_ENABLED = False


def is_enabled() -> bool:
    return _HITL_ENABLED


# ---------------------------------------------------------------------------
# Cross-platform stdin readline with timeout
# ---------------------------------------------------------------------------

def _read_line_with_timeout(seconds: int) -> Optional[str]:
    """
    Read one line from stdin, returning None if the user doesn't respond
    within `seconds`. Works on both POSIX and Windows.
    """
    result: list[Optional[str]] = [None]
    done = threading.Event()

    def _reader():
        try:
            result[0] = sys.stdin.readline()
        except Exception:
            result[0] = None
        finally:
            done.set()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    if done.wait(timeout=seconds):
        return result[0]
    # Timeout — print a newline so the terminal isn't left mid-line
    sys.stdout.write("\n")
    sys.stdout.flush()
    return None


# ---------------------------------------------------------------------------
# HITLManager
# ---------------------------------------------------------------------------

class HITLManager:
    """
    Inline human-in-the-loop prompts — no modal dialogs.

    Call directly from sync code, or from async code via the
    `async_request_*` helpers below which off-load the blocking read
    to a thread so the event loop is not frozen.
    """

    def request_permission(self, action_name: str, description: str) -> bool:
        if not _HITL_ENABLED:
            return True
        try:
            sys.stdout.write(
                f"\n[faultline] Tool: {action_name}\n"
                f"  {description}\n"
                f"  Allow? [y/N] "
            )
            sys.stdout.flush()
            line = _read_line_with_timeout(HITL_PROMPT_TIMEOUT)
            if line is None:
                sys.stdout.write(f"  (no response in {HITL_PROMPT_TIMEOUT}s — denied)\n")
                sys.stdout.flush()
                logger.warning("HITL: %s — timed out, denied.", action_name)
                return False
            approved = (line or "").strip().lower() == "y"
            logger.info("HITL: %s — %s.", action_name, "approved" if approved else "denied")
            return approved
        except Exception as exc:
            logger.warning("HITL permission prompt failed: %s. Defaulting to deny.", exc)
            return False

    def request_credential(self, name: str, hint: str = "", sensitive: bool = True) -> str:
        if not _HITL_ENABLED:
            return ""
        try:
            msg = f"\n[faultline] Credential needed: {name}"
            if hint:
                msg += f" ({hint})"
            sys.stdout.write(msg + "\n  Value: ")
            sys.stdout.flush()
            if sensitive:
                import getpass
                return getpass.getpass("") or ""
            value = (_read_line_with_timeout(60) or "").strip()
            return value
        except Exception as exc:
            logger.warning("HITL credential prompt failed: %s. Returning empty.", exc)
            return ""


# Module-level singleton — import this from anywhere
hitl = HITLManager()


# ---------------------------------------------------------------------------
# Async bridges — call these from inside an asyncio coroutine
# ---------------------------------------------------------------------------

async def async_request_permission(action_name: str, description: str) -> bool:
    """Async-safe wrapper. Off-loads the blocking stdin read to a thread."""
    if not _HITL_ENABLED:
        return True
    return await asyncio.to_thread(hitl.request_permission, action_name, description)


async def async_request_credential(name: str, hint: str = "", sensitive: bool = True) -> str:
    """Async-safe wrapper. Off-loads the blocking stdin read to a thread."""
    if not _HITL_ENABLED:
        return ""
    return await asyncio.to_thread(hitl.request_credential, name, hint, sensitive)
