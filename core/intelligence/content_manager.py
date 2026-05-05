"""
core/content_manager.py

Hierarchical tiered content management for the Faultline agent pipeline.

Prevents OpenRouter token-limit rejections by windowing the message list
sent to model_with_tools.ainvoke(). state["messages"] is NEVER mutated -
windowing happens only at the call site.

Design:
  - Per-request budget: 200k tokens (configurable via FAULTLINE_MAX_TOKENS)
  - Large tool results (>5k estimated tokens) are stored to disk in
    <run_folder>/content_store/<ref_id>.txt and replaced with a rich
    excerpt + [REF:<ref_id>] marker.
  - The agent can call retrieve_stored_content(run_folder, ref_id) at any
    time to get the full content back - nothing is ever truly lost.
  - Every stored blob is indexed in <run_folder>/memory.json so the agent
    can find any piece of information by topic without guessing a ref_id.

Three tiers applied before every ainvoke call:
  Tier 1 - Latest cycle + HumanMessage + SystemMessage + memory.md (always full fidelity)
  Tier 2 - Previous TIER2_CYCLES cycles (large results â†’ stored + summarised)
  Tier 3 - All older cycles (bullet summary with [REF:id] pointers)
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from core.token_utils import estimate_tokens as _shared_estimate_tokens

import os
logger = logging.getLogger("AegisAgent")

# â”€â”€ Tuning constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
CHARS_PER_TOKEN: float = 2.0  # Even MORE conservative for JSON/Code heavy prompts
SUMMARIZATION_THRESHOLD_TOKENS: int = int(os.environ.get("FAULTLINE_SUMMARY_THRESHOLD", "3000"))
MESSAGE_CLIP_THRESHOLD_TOKENS: int = 15000  # Hard cap for ANY single message to prevent context bombs
EXCERPT_CHARS: int = 800
TIER2_CYCLES: int = int(os.environ.get("FAULTLINE_TIER2_CYCLES", "3"))
MEMORY_LEDGER_MAX_ROWS: int = 300        # LRU-prune above this
MEMORY_LEDGER_INLINE_ROWS: int = 150     # rows shown inline in system prompt
MEMORY_INJECT_MAX_TOKENS: int = int(os.environ.get("FAULTLINE_MEMORY_INJECT_MAX_TOKENS", "4000"))


# â”€â”€ Primitives â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def estimate_tokens(text: str) -> int:
    return _shared_estimate_tokens(
        text,
        chars_per_token=CHARS_PER_TOKEN,
        min_tokens_for_non_empty=0,
    )

def _archive_message(msg: BaseMessage, run_folder: str, msg_id: str) -> None:
    """Saves the full message content to the history_vault."""
    if not run_folder:
        return
    p = Path(run_folder) / "history_vault" / f"{msg_id}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    p.write_text(content, encoding="utf-8")


def _message_role(msg: BaseMessage) -> str:
    if isinstance(msg, HumanMessage):
        return "human"
    if isinstance(msg, ToolMessage):
        return "tool"
    if isinstance(msg, SystemMessage):
        return "system"
    if isinstance(msg, AIMessage) or hasattr(msg, "tool_calls"):
        return "ai"
    return getattr(msg, "type", "message")


def _history_message_id(index: int, msg: BaseMessage) -> str:
    role = re.sub(r"[^a-z0-9]+", "_", _message_role(msg).lower()).strip("_") or "msg"
    tool_name = ""
    if isinstance(msg, ToolMessage) and msg.name:
        tool_name = "__" + re.sub(r"[^a-z0-9]+", "_", msg.name.lower()).strip("_")[:32]
    return f"hist_{index:04d}_{role}{tool_name}"


def _archive_history_messages(messages: List[BaseMessage], run_folder: str) -> None:
    """
    Persist every message in the run folder before context windowing.

    The model receives only compact refs, but the full transcript remains
    available through retrieve_history_message(run_folder, message_id).
    """
    if not run_folder:
        return
    try:
        rows = [
            "## History Vault Index",
            "",
            "| message_id | role | tokens_est | summary |",
            "|------------|------|------------|---------|",
        ]
        vault_dir = Path(run_folder) / "history_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        for index, msg in enumerate(messages):
            msg_id = _history_message_id(index, msg)
            _archive_message(msg, run_folder, msg_id)
            summary = _summarize_message(msg).replace("|", "\\|").replace("\n", " ")
            rows.append(f"| `{msg_id}` | {_message_role(msg)} | {_msg_tokens(msg):,} | {summary[:140]} |")
        Path(run_folder, "history_index.md").write_text("\n".join(rows), encoding="utf-8")
    except Exception as exc:
        logger.debug("_archive_history_messages error: %s", exc)

def _summarize_message(msg: BaseMessage) -> str:
    """Returns a 1-line summary of a message's content/intent."""
    if isinstance(msg, HumanMessage):
        return f"Human: {str(msg.content)[:80]}..."
    if isinstance(msg, AIMessage) or hasattr(msg, "tool_calls"):
        tool_calls = getattr(msg, "tool_calls", []) or []
        if tool_calls:
            tools = [t.get("name", "?") for t in tool_calls]
            return f"AI: Calling tools {tools}"
        return f"AI: {str(msg.content)[:80]}..."
    if isinstance(msg, ToolMessage):
        # Extract tool name from ID if possible, or just call it Tool
        return f"Tool Response: {str(msg.content)[:100]}..."
    return f"Message: {str(msg.content)[:80]}..."


def _msg_tokens(msg: BaseMessage) -> int:
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    base = estimate_tokens(content)
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        import json as _json
        try:
            base += estimate_tokens(_json.dumps(tool_calls))
        except Exception:
            base += 50 * len(tool_calls)
    return base


def _make_ref_id(tool_name: str, source_hint: str, turn: int) -> str:
    """
    Build a semantic ref_id the agent can read at a glance.
    E.g. read_project_file__orchestrator_urls_py__t12
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (source_hint or "").lower())[:40].strip("_")
    safe_name = re.sub(r"[^a-z0-9_]", "", (tool_name or "tool").lower())
    turn_str = f"t{turn}" if turn >= 0 else "tx"
    parts = [safe_name, slug, turn_str] if slug else [safe_name, turn_str]
    return "__".join(parts)


# â”€â”€ Memory ledger â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _load_memory(run_folder: str) -> list:
    if not run_folder:
        return []
    try:
        p = Path(run_folder) / "memory.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_memory(run_folder: str, rows: list) -> None:
    if not run_folder:
        return
    try:
        p = Path(run_folder) / "memory.json"
        p.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    except Exception:
        pass


def _append_memory_entry(
    run_folder: str,
    ref_id: str,
    tool_name: str,
    source_hint: str,
    one_line: str,
    turn: int,
) -> None:
    """Add/update a memory ledger row and flush memory.md."""
    if not run_folder:
        return
    try:
        rows = _load_memory(run_folder)
        # Update last_used if ref_id already exists
        for row in rows:
            if row.get("ref_id") == ref_id:
                row["last_used"] = turn
                row["summary"] = one_line[:120]
                _save_memory(run_folder, rows)
                _flush_memory_md(run_folder, rows)
                return
        rows.append({
            "ref_id": ref_id,
            "tool": tool_name,
            "source": source_hint,
            "summary": one_line[:120],
            "turn": turn,
            "last_used": turn,
        })
        # LRU prune if over limit
        if len(rows) > MEMORY_LEDGER_MAX_ROWS:
            rows.sort(key=lambda r: r.get("last_used", 0))
            rows = rows[-(MEMORY_LEDGER_MAX_ROWS):]
        _save_memory(run_folder, rows)
        _flush_memory_md(run_folder, rows)
    except Exception as exc:
        logger.debug("_append_memory_entry error: %s", exc)


def _flush_memory_md(run_folder: str, rows: list) -> None:
    """Rewrite memory.md - the human-readable ledger injected into the system prompt."""
    if not run_folder:
        return
    try:
        recent = sorted(rows, key=lambda r: r.get("last_used", 0), reverse=True)
        lines = [
            "## Memory Ledger (session knowledge index)\n",
            "| ref_id | tool | source | summary |\n",
            "|--------|------|--------|---------|",
        ]
        for row in recent[:MEMORY_LEDGER_INLINE_ROWS]:
            lines.append(
                f"| `{row['ref_id']}` | {row['tool']} | {row.get('source','')[:40]} "
                f"| {row.get('summary','')[:80]} |"
            )
        if len(recent) > MEMORY_LEDGER_INLINE_ROWS:
            lines.append(f"\n_... {len(recent) - MEMORY_LEDGER_INLINE_ROWS} older entries in memory.json_")
        lines.append(
            "\n**Usage:** call `retrieve_stored_content(run_folder, ref_id)` with any ref_id above "
            "to get the full content. No information is lost - everything fetched this session is here."
        )
        Path(run_folder, "memory.md").write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        logger.debug("_flush_memory_md error: %s", exc)


def read_memory_md(run_folder: str) -> str:
    """Return memory.md content for injection into the system prompt. Empty string if missing."""
    if not run_folder:
        return ""
    try:
        p = Path(run_folder) / "memory.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def read_history_index(run_folder: str) -> str:
    """Return the compact history vault index, if present."""
    if not run_folder:
        return ""
    try:
        p = Path(run_folder) / "history_index.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _trim_memory_text(memory_text: str, max_tokens: int) -> str:
    """Trim injected memory.md text to a strict token ceiling."""
    if not memory_text or max_tokens <= 0:
        return ""
    if estimate_tokens(memory_text) <= max_tokens:
        return memory_text
    trimmed = memory_text[: max_tokens * int(CHARS_PER_TOKEN)]
    return (
        f"{trimmed}\n\n"
        f"... [TRUNCATED MEMORY LEDGER: showing first ~{max_tokens} tokens] ..."
    )


# â”€â”€ Content store â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _store_content(content: str, ref_id: str, run_folder: str) -> bool:
    """Write full content to <run_folder>/content_store/<ref_id>.txt. Returns True on success."""
    if not run_folder:
        return False
    try:
        store_dir = Path(run_folder) / "content_store"
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / f"{ref_id}.txt").write_text(content, encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("content_manager: failed to store %s: %s", ref_id, exc)
        return False


def store_and_summarize(
    content: str,
    tool_name: str,
    run_folder: str,
    counter: int,
    source_hint: str = "",
    turn: int = -1,
) -> Tuple[str, str]:
    """
    Store full content to disk, index it in memory.json, and return (summary_text, ref_id).

    source_hint: human-readable label (e.g. file path, query text) used to build
                 a semantic ref_id the agent can identify without guessing.
    turn: current agent turn for recency tracking (-1 = unknown).
    """
    ref_id = _make_ref_id(tool_name or "tool", source_hint, turn)
    stored = _store_content(content, ref_id, run_folder)

    token_count = estimate_tokens(content)
    excerpt = content[:EXCERPT_CHARS].rstrip()
    if len(content) > EXCERPT_CHARS:
        excerpt += "\n..."

    # Build one-line summary for the memory ledger
    first_meaningful = next(
        (ln.strip() for ln in content.splitlines() if ln.strip()),
        content[:80],
    )
    one_line = first_meaningful[:120]
    _append_memory_entry(run_folder, ref_id, tool_name, source_hint, one_line, turn)

    if stored:
        summary = (
            f"[SUMMARISED - {tool_name} result, ~{token_count:,} tokens]\n"
            f"{excerpt}\n"
            f"[REF:{ref_id}] - call retrieve_stored_content(run_folder, \"{ref_id}\") "
            f"to get the complete output"
        )
    else:
        summary = (
            f"[TRUNCATED - {tool_name} result, ~{token_count:,} tokens. "
            f"No run_folder available for storage.]\n{excerpt}"
        )

    return summary, ref_id


# â”€â”€ Cycle extraction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _extract_cycles(messages: List[BaseMessage]) -> List[List[BaseMessage]]:
    """
    Group messages into tool-call cycles.

    Each cycle starts with an AIMessage and includes all following
    ToolMessages until the next AIMessage.
    HumanMessages are skipped (handled separately as Tier 1).
    """
    cycles: List[List[BaseMessage]] = []
    current: List[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            continue
        if isinstance(msg, AIMessage) or hasattr(msg, "tool_calls"):
            if current:
                cycles.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        cycles.append(current)
    return cycles


# â”€â”€ Cycle summarisation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _source_hint_from_tool_msg(msg: ToolMessage) -> str:
    """Extract a readable source hint from a ToolMessage for use in ref_id generation."""
    content_str = str(msg.content)
    tool_name = msg.name or "tool"
    # For file-read tools, try to extract the path from the content JSON
    if tool_name in ("read_project_file", "read_run_folder_file"):
        try:
            d = json.loads(content_str)
            path = d.get("path", "")
            if path:
                import os
                return os.path.basename(str(path)).replace(".", "_")
        except Exception:
            pass
    # For query tools, use first meaningful word of content
    first = next((ln.strip() for ln in content_str.splitlines() if ln.strip()), "")
    return re.sub(r"[^a-z0-9]+", "_", first[:30].lower()).strip("_")


def _process_cycle(
    cycle: List[BaseMessage],
    run_folder: str,
    counter: int,
    turn: int = -1,
    seen_calls: dict[tuple[str, str], int] = None,
) -> Tuple[List[BaseMessage], int]:
    """
    Process a cycle:
    1. Detect and collapse redundant tool calls (same tool + same args).
    2. Summarise large tool messages (>THRESHOLD).
    """
    result: List[BaseMessage] = []
    ai_msg = cycle[0] if cycle and isinstance(cycle[0], AIMessage) else None
    tool_calls = getattr(ai_msg, "tool_calls", []) if ai_msg else []
    call_map = {tc.get("id"): tc for tc in tool_calls}

    for msg in cycle:
        if isinstance(msg, ToolMessage):
            tc = call_map.get(msg.tool_call_id)
            # 1. Redundant call detection
            if tc and seen_calls is not None:
                try:
                    call_key = (tc["name"], json.dumps(tc["args"], sort_keys=True))
                    if call_key in seen_calls:
                        prev_turn = seen_calls[call_key]
                        result.append(
                            ToolMessage(
                                content=f"[REDUNDANT: Result same as Turn {prev_turn}]",
                                tool_call_id=msg.tool_call_id,
                                name=msg.name,
                                status=msg.status,
                            )
                        )
                        continue
                    seen_calls[call_key] = turn
                except Exception:
                    pass

            # 2. Large message summarisation
            content_str = str(msg.content)
            if estimate_tokens(content_str) > SUMMARIZATION_THRESHOLD_TOKENS:
                tool_name = msg.name or "tool"
                source_hint = _source_hint_from_tool_msg(msg)
                summary, _ = store_and_summarize(
                    content_str, tool_name, run_folder, counter,
                    source_hint=source_hint, turn=turn,
                )
                counter += 1
                result.append(
                    ToolMessage(
                        content=summary,
                        tool_call_id=msg.tool_call_id,
                        name=msg.name,
                        status=msg.status,
                    )
                )
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result, counter


def compress_cycle_to_summary(
    cycles: List[List[BaseMessage]],
    run_folder: str,
    counter: int,
    turn: int = -1,
) -> Tuple[HumanMessage, int]:
    """
    Compress a list of historical cycles into a single bullet-point HumanMessage.
    Large ToolMessages are stored and referenced via semantic ref_ids.
    The resulting summary references memory.json so no information is lost.
    """
    lines: List[str] = [
        "[Historical context - earlier cycles summarised to preserve context window]",
        "[All data is still accessible: see memory.md for the full index of ref_ids]",
        "",
    ]
    for cycle in cycles:
        for msg in cycle:
            if isinstance(msg, AIMessage):
                if msg.tool_calls:
                    names = ", ".join(tc.get("name", "?") for tc in msg.tool_calls)
                    lines.append(f"- Agent called: {names}")
                elif msg.content:
                    excerpt = str(msg.content)[:300].replace("\n", " ")
                    lines.append(f"- Agent reasoning: {excerpt}")
            elif isinstance(msg, ToolMessage):
                tool_name = msg.name or "tool"
                content_str = str(msg.content)
                if estimate_tokens(content_str) > SUMMARIZATION_THRESHOLD_TOKENS:
                    source_hint = _source_hint_from_tool_msg(msg)
                    _, ref_id = store_and_summarize(
                        content_str, tool_name, run_folder, counter,
                        source_hint=source_hint, turn=turn,
                    )
                    counter += 1
                    first_line = next(
                        (ln.strip() for ln in content_str.splitlines() if ln.strip()),
                        content_str[:100],
                    )
                    lines.append(
                        f"  -> {tool_name} ({source_hint}): {first_line[:120]} "
                        f"[REF:{ref_id}]"
                    )
                else:
                    first_line = next(
                        (ln.strip() for ln in content_str.splitlines() if ln.strip()),
                        content_str[:100],
                    )
                    lines.append(f"  -> {tool_name}: {first_line[:200]}")

    return HumanMessage(content="\n".join(lines)), counter


# â”€â”€ Main entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def build_tiered_context(
    system_msg: SystemMessage,
    messages: List[BaseMessage],
    run_folder: str = "",
    max_tokens: int = 200_000,
    current_turn: int = -1,
) -> Tuple[List[BaseMessage], dict]:
    """
    Apply three-tier hierarchical windowing and return a pruned message list
    safe to pass to model_with_tools.ainvoke().

    Injects memory.md (session knowledge index) unconditionally into Tier 1 so
    the agent always has a catalogue of every stored blob - even after Tier 3
    compression. This replaces the lossy bullet-point summary that previously
    got dropped when over budget.

    Returns:
        (final_message_list, stats_dict)
    """
    stats = {
        "total_input_messages": len(messages),
        "total_input_tokens_est": 0,
        "output_messages": 0,
        "output_tokens_est": 0,
        "cycles_total": 0,
        "cycles_in_tier1": 0,
        "cycles_in_tier2": 0,
        "cycles_compressed": 0,
        "cycles_dropped": 0,
        "windowing_applied": False,
    }

    # Persist the complete message history before deciding what enters the
    # model window. This keeps storage lossless while prompt input stays small.
    working_messages = list(messages)
    _archive_history_messages(working_messages, run_folder)

    # â”€â”€ Memory ledger - always injected, never dropped â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    memory_parts = []
    stored_refs = read_memory_md(run_folder)
    if stored_refs:
        memory_parts.append(stored_refs)
    history_index = read_history_index(run_folder)
    if history_index:
        memory_parts.append(
            history_index
            + "\n\nUse `retrieve_history_message(run_folder, message_id)` "
            "to recover any exact prior message listed above."
        )
    memory_text = _trim_memory_text("\n\n".join(memory_parts), MEMORY_INJECT_MAX_TOKENS)
    memory_msg: Optional[SystemMessage] = (
        SystemMessage(content=memory_text) if memory_text else None
    )
    memory_tokens = estimate_tokens(memory_text) if memory_text else 0

    sys_tokens = estimate_tokens(str(system_msg.content))
    msg_tokens_total = sum(_msg_tokens(m) for m in working_messages)
    stats["total_input_tokens_est"] = sys_tokens + msg_tokens_total + memory_tokens

    # Dynamic safety buffer: Leave at least 30k tokens for the model to think/respond.
    safety_margin = 30000
    safe_max = max(1, max_tokens - safety_margin)

    # Fast path only for genuinely short conversations. Once a campaign has
    # meaningful history, keep the prompt ref-based even if it technically fits.
    if stats["total_input_tokens_est"] <= safe_max and len(working_messages) <= 12:
        final: List[BaseMessage] = [system_msg]
        if memory_msg:
            final.append(memory_msg)
        final.extend(working_messages)
        stats["output_messages"] = len(final)
        stats["output_tokens_est"] = sys_tokens + sum(_msg_tokens(m) for m in working_messages) + memory_tokens
        return final, stats

    stats["windowing_applied"] = True
    
    # VIRTUAL MEMORY ARCHIVAL ENGINE
    # ------------------------------------------------------------------
    # LangGraph appends AI/tool cycles, not a new HumanMessage per turn.
    # Group by those cycles so old tool-call args and large tool responses do
    # not remain "recent" forever.

    final_messages = [system_msg]
    if memory_msg:
        final_messages.append(memory_msg)

    human_messages = [m for m in working_messages if isinstance(m, HumanMessage)]
    non_human_messages = [m for m in working_messages if not isinstance(m, HumanMessage)]
    cycles = _extract_cycles(non_human_messages)
    stats["cycles_total"] = len(cycles)

    # Keep the operator's objective and most recent steering in full.
    for hm in human_messages[-3:]:
        final_messages.append(hm)

    latest_full_cycles = int(os.environ.get("FAULTLINE_TIER1_CYCLES", "1"))
    tier2_cycles = int(os.environ.get("FAULTLINE_TIER2_CYCLES", str(TIER2_CYCLES)))
    split_at = max(0, len(cycles) - latest_full_cycles)
    tier1 = cycles[split_at:]
    older = cycles[:split_at]
    tier2_start = max(0, len(older) - tier2_cycles)
    tier2 = older[tier2_start:]
    tier3 = older[:tier2_start]

    stats["cycles_in_tier1"] = len(tier1)
    stats["cycles_in_tier2"] = len(tier2)

    counter = 0
    seen_calls: dict[tuple[str, str], int] = {}

    if tier3:
        summary, counter = compress_cycle_to_summary(
            tier3,
            run_folder=run_folder,
            counter=counter,
            turn=current_turn,
        )
        final_messages.append(summary)
        stats["cycles_compressed"] += len(tier3)

    for cycle in tier2:
        summary, counter = compress_cycle_to_summary(
            [cycle],
            run_folder=run_folder,
            counter=counter,
            turn=current_turn,
        )
        final_messages.append(summary)
        stats["cycles_compressed"] += 1

    for cycle in tier1:
        processed, counter = _process_cycle(
            cycle,
            run_folder=run_folder,
            counter=counter,
            turn=current_turn,
            seen_calls=seen_calls,
        )
        final_messages.extend(processed)

    stats["output_messages"] = len(final_messages)
    stats["output_tokens_est"] = sum(_msg_tokens(m) for m in final_messages)

    # EMERGENCY PRUNING
    # Drop oldest non-essential context until the request is below the safe
    # limit. Preserve system, memory, and at least the latest cycle/result.
    safety_limit = max(1, safe_max)
    protected_prefix = 2 if memory_msg else 1
    while stats["output_tokens_est"] > safety_limit and len(final_messages) > protected_prefix + 2:
        dropped = final_messages.pop(protected_prefix)
        stats["output_tokens_est"] -= _msg_tokens(dropped)
        stats["cycles_dropped"] += 1

    # If a single recent cycle is still too large, collapse everything after
    # the fixed prefix into one tiny breadcrumb instead of risking a provider
    # 400. The full raw messages were already checkpointed/history-vaulted.
    if stats["output_tokens_est"] > safety_limit and len(final_messages) > protected_prefix:
        overflow_note = HumanMessage(content=(
            "[Context emergency compaction: recent messages were too large to fit. "
            "Use memory.md/content_store refs or retrieve_history_message for exact prior content.]"
        ))
        final_messages = final_messages[:protected_prefix] + [overflow_note]
        stats["output_tokens_est"] = sum(_msg_tokens(m) for m in final_messages)
        stats["cycles_dropped"] += 1

    return final_messages, stats

