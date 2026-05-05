"""
Delegation tools for CLI-backed providers.

Kept in a separate module to reduce the size and responsibility surface of
core/tools.py while preserving tool names and behavior.
"""

import logging

from langchain_core.tools import tool

from core.providers.cli_provider import ProviderManager

logger = logging.getLogger("FaultlineTools")


@tool
def execute_claude_code_task(task: str, target_dir: str) -> str:
    """
    Delegates a complex coding or investigation task to the 'claude' CLI.
    Use this for high-level refactoring or multi-file architectural changes.
    """
    logger.info("Tool Call: Delegating to Claude Code: %s", task)
    try:
        manager = ProviderManager(target_dir=target_dir)
        return manager.run("claude", task)
    except Exception as e:
        return f"Execution error: {e}"


@tool
def execute_gemini_cli_task(prompt: str, target_dir: str) -> str:
    """Delegates a reasoning or code analysis task to the 'gemini' CLI."""
    logger.info("Tool Call: Delegating to Gemini CLI: %s", prompt)
    try:
        manager = ProviderManager(target_dir=target_dir)
        return manager.run("gemini", prompt)
    except Exception as e:
        return f"Execution error: {e}"


@tool
def execute_codex_cli_task(task: str, target_dir: str) -> str:
    """Delegates a coding task to the 'codex' CLI."""
    logger.info("Tool Call: Delegating to Codex CLI: %s", task)
    try:
        manager = ProviderManager(target_dir=target_dir)
        return manager.run("codex", task)
    except Exception as e:
        return f"Execution error: {e}"
