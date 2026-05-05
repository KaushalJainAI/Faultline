"""Human-in-the-Loop (HITL) manager for Faultline."""

import asyncio
import logging
import sys
import threading
from typing import Optional, Sequence

logger = logging.getLogger("FaultlineHITL")

_HITL_ENABLED: bool = False
HITL_PROMPT_TIMEOUT: int = 30


def enable_hitl() -> None:
    global _HITL_ENABLED
    _HITL_ENABLED = True


def disable_hitl() -> None:
    global _HITL_ENABLED
    _HITL_ENABLED = False


def is_enabled() -> bool:
    return _HITL_ENABLED


def _read_line_with_timeout(seconds: int) -> Optional[str]:
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
    sys.stdout.write("\n")
    sys.stdout.flush()
    return None


def _normalize_ab_esc(raw: str) -> str:
    """Normalize user input into 'a', 'b', or 'esc'."""
    s = (raw or "").strip().lower()
    if s in {"a", "1", "option a"}:
        return "a"
    if s in {"b", "2", "option b"}:
        return "b"
    if s in {"esc", "escape", "cancel", "skip", ""}:
        return "esc"
    return "esc"


class HITLManager:
    def __init__(self) -> None:
        self._always_allow_actions: set[str] = set()
        self._always_deny_actions: set[str] = set()

    def request_permission(self, action_name: str, description: str) -> bool:
        if not _HITL_ENABLED:
            return True
        if action_name in self._always_allow_actions:
            return True
        if action_name in self._always_deny_actions:
            return False
        try:
            sys.stdout.write(
                f"\n[faultline] Permission required\n"
                f"  Action : {action_name}\n"
                f"  Detail : {description}\n"
                f"  A) Allow\n"
                f"  B) Deny\n"
                f"  Esc) Cancel / no decision\n"
                f"  Choose [A/B/Esc] (default: Esc, timeout: {HITL_PROMPT_TIMEOUT}s): "
            )
            sys.stdout.flush()
            line = _read_line_with_timeout(HITL_PROMPT_TIMEOUT)
            if line is None:
                logger.warning("HITL timeout for %s; denied.", action_name)
                return False
            choice = _normalize_ab_esc(line)
            if choice == "a":
                return True
            if choice == "b":
                return False
            return False
        except Exception as exc:
            logger.warning("HITL permission prompt failed: %s. Defaulting to deny.", exc)
            return False

    def request_credential(self, name: str, hint: str = "", sensitive: bool = True) -> str:
        if not _HITL_ENABLED:
            return ""
        try:
            msg = f"\n[faultline] Input needed: {name}"
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

    def request_text(self, question: str, hint: str = "", timeout: int = 120, default: str = "") -> str:
        if not _HITL_ENABLED:
            return ""
        try:
            sys.stdout.write(f"\n[faultline] Input needed\n  Question: {question}\n")
            if hint:
                sys.stdout.write(f"  Hint    : {hint}\n")
            if default:
                sys.stdout.write(f"  Default : {default}\n")
            sys.stdout.write(f"  Answer (timeout: {timeout}s): ")
            sys.stdout.flush()
            line = _read_line_with_timeout(timeout)
            if line is None:
                return default
            answer = (line or "").strip()
            return answer if answer else default
        except Exception as exc:
            logger.warning("HITL text prompt failed: %s. Returning default.", exc)
            return default

    def request_choice(
        self,
        question: str,
        options: Sequence[str],
        hint: str = "",
        timeout: int = 120,
        default_index: int = 0,
    ) -> str:
        opts = [o.strip() for o in options if str(o).strip()]
        if not opts:
            return self.request_text(question=question, hint=hint, timeout=timeout, default="")
        # This UI supports exactly two options + Esc.
        if len(opts) == 1:
            opts = [opts[0], "Cancel"]
        elif len(opts) > 2:
            opts = opts[:2]
        safe_default = max(0, min(default_index, len(opts) - 1))
        default_value = ""
        if not _HITL_ENABLED:
            return opts[safe_default]
        try:
            sys.stdout.write(f"\n[faultline] Choice needed\n  Question: {question}\n")
            if hint:
                sys.stdout.write(f"  Hint    : {hint}\n")
            sys.stdout.write(f"  A) {opts[0]}\n")
            sys.stdout.write(f"  B) {opts[1]}\n")
            sys.stdout.write("  Esc) Cancel / choose nothing\n")
            sys.stdout.write(f"  Choose [A/B/Esc] (default: Esc, timeout: {timeout}s): ")
            sys.stdout.flush()
            line = _read_line_with_timeout(timeout)
            if line is None:
                return default_value
            pick = _normalize_ab_esc(line)
            if pick == "a":
                return opts[0]
            if pick == "b":
                return opts[1]
            return default_value
        except Exception as exc:
            logger.warning("HITL choice prompt failed: %s. Returning default.", exc)
            return default_value


hitl = HITLManager()


async def async_request_permission(action_name: str, description: str) -> bool:
    if not _HITL_ENABLED:
        return True
    return await asyncio.to_thread(hitl.request_permission, action_name, description)


async def async_request_credential(name: str, hint: str = "", sensitive: bool = True) -> str:
    if not _HITL_ENABLED:
        return ""
    return await asyncio.to_thread(hitl.request_credential, name, hint, sensitive)


async def async_request_text(question: str, hint: str = "", timeout: int = 120, default: str = "") -> str:
    if not _HITL_ENABLED:
        return ""
    return await asyncio.to_thread(hitl.request_text, question, hint, timeout, default)


async def async_request_choice(
    question: str,
    options: Sequence[str],
    hint: str = "",
    timeout: int = 120,
    default_index: int = 0,
) -> str:
    if not _HITL_ENABLED:
        opts = [o.strip() for o in options if str(o).strip()]
        return opts[0] if opts else ""
    return await asyncio.to_thread(hitl.request_choice, question, list(options), hint, timeout, default_index)
