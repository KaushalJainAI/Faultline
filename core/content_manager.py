"""
core/content_manager.py

Hierarchical tiered content management for the Faultline agent pipeline.

Prevents OpenRouter token-limit rejections by windowing the message list
sent to model_with_tools.ainvoke(). state["messages"] is NEVER mutated —
windowing happens only at the call site.

Design:
  - Per-request budget: 200k tokens (configurable via FAULTLINE_MAX_TOKENS)
  - Large tool results (>5k estimated tokens) are stored to disk in
    <run_folder>/content_store/<ref_id>.txt and replaced with a rich
    excerpt + [REF:<ref_id>] marker.
  - The agent can call retrieve_stored_content(run_folder, ref_id) at any
    time to get the full content back — nothing is ever truly lost.

Three tiers applied before every ainvoke call:
  Tier 1 — Latest cycle + HumanMessage + SystemMessage (always full fidelity)
  Tier 2 — Previous TIER2_CYCLES cycles (large results → stored + summarised)
  Tier 3 — All older cycles (bullet summary with [REF:id] pointers)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger("AegisAgent")

# ── Tuning constants ────────────────────────────────────────────────────────
CHARS_PER_TOKEN: int = 4                  # realistic chars-per-token estimate (code-heavy content)
SUMMARIZATION_THRESHOLD_TOKENS: int = 5_000  # tool results larger than this get stored
EXCERPT_CHARS: int = 800                 # chars to show in the inline excerpt
TIER2_CYCLES: int = 5                    # number of recent cycles in Tier 2


# ── Primitives ───────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _msg_tokens(msg: BaseMessage) -> int:
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    base = estimate_tokens(content)
    if isinstance(msg, AIMessage) and msg.tool_calls:
        import json as _json
        try:
            base += estimate_tokens(_json.dumps(msg.tool_calls))
        except Exception:
            base += 50 * len(msg.tool_calls)
    return base


def _make_ref_id(tool_name: str, counter: int) -> str:
    return f"{tool_name}_{counter:03d}"


# ── Content store ────────────────────────────────────────────────────────────

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
) -> Tuple[str, str]:
    """
    Store full content to disk and return (summary_text, ref_id).

    The summary includes a rich excerpt so the agent can decide whether
    it needs to retrieve the full content.
    """
    ref_id = _make_ref_id(tool_name or "tool", counter)
    stored = _store_content(content, ref_id, run_folder)

    token_count = estimate_tokens(content)
    excerpt = content[:EXCERPT_CHARS].rstrip()
    if len(content) > EXCERPT_CHARS:
        excerpt += "\n..."

    if stored:
        summary = (
            f"[SUMMARISED — {tool_name} result, ~{token_count:,} tokens]\n"
            f"{excerpt}\n"
            f"[REF:{ref_id}] — call retrieve_stored_content(run_folder, \"{ref_id}\") "
            f"to get the complete output"
        )
    else:
        # No run_folder available — inline truncation with notice
        summary = (
            f"[TRUNCATED — {tool_name} result, ~{token_count:,} tokens. "
            f"No run_folder available for storage.]\n{excerpt}"
        )

    return summary, ref_id


# ── Cycle extraction ────────────────────────────────────────────────────────

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
        if isinstance(msg, AIMessage):
            if current:
                cycles.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        cycles.append(current)
    return cycles


# ── Cycle summarisation ──────────────────────────────────────────────────────

def _summarise_large_tool_messages(
    cycle: List[BaseMessage],
    run_folder: str,
    counter: int,
) -> Tuple[List[BaseMessage], int]:
    """
    Return a copy of the cycle where ToolMessages exceeding
    SUMMARIZATION_THRESHOLD_TOKENS are replaced with stored summaries.
    Counter is incremented for each stored item.
    """
    result: List[BaseMessage] = []
    for msg in cycle:
        if isinstance(msg, ToolMessage):
            content_str = str(msg.content)
            if estimate_tokens(content_str) > SUMMARIZATION_THRESHOLD_TOKENS:
                tool_name = msg.name or "tool"
                summary, _ = store_and_summarize(content_str, tool_name, run_folder, counter)
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
) -> Tuple[HumanMessage, int]:
    """
    Compress a list of historical cycles into a single bullet-point
    HumanMessage. Large ToolMessages are stored and referenced.
    """
    lines: List[str] = [
        "[Historical context — earlier cycles summarised to preserve context window]",
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
                    _, ref_id = store_and_summarize(content_str, tool_name, run_folder, counter)
                    counter += 1
                    first_line = next(
                        (ln.strip() for ln in content_str.splitlines() if ln.strip()),
                        content_str[:100],
                    )
                    lines.append(
                        f"  -> {tool_name}: {first_line[:120]} "
                        f"[REF:{ref_id}]"
                    )
                else:
                    first_line = next(
                        (ln.strip() for ln in content_str.splitlines() if ln.strip()),
                        content_str[:100],
                    )
                    lines.append(f"  -> {tool_name}: {first_line[:200]}")

    return HumanMessage(content="\n".join(lines)), counter


# ── Main entry point ─────────────────────────────────────────────────────────

def build_tiered_context(
    system_msg: SystemMessage,
    messages: List[BaseMessage],
    run_folder: str = "",
    max_tokens: int = 200_000,
) -> Tuple[List[BaseMessage], dict]:
    """
    Apply three-tier hierarchical windowing and return a pruned message list
    safe to pass to model_with_tools.ainvoke().

    Returns:
        (final_message_list, stats_dict)

    stats_dict keys:
        total_input_messages      int
        total_input_tokens_est    int
        output_messages           int
        output_tokens_est         int
        cycles_total              int
        cycles_in_tier1           int
        cycles_in_tier2           int
        cycles_compressed         int
        cycles_dropped            int
        windowing_applied         bool
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

    sys_tokens = estimate_tokens(str(system_msg.content))
    msg_tokens_total = sum(_msg_tokens(m) for m in messages)
    stats["total_input_tokens_est"] = sys_tokens + msg_tokens_total

    # Fast path — already within budget, no windowing needed
    if stats["total_input_tokens_est"] <= max_tokens:
        final = [system_msg] + list(messages)
        stats["output_messages"] = len(final)
        stats["output_tokens_est"] = stats["total_input_tokens_est"]
        return final, stats

    stats["windowing_applied"] = True

    # Separate the original HumanMessage (always Tier 1)
    human_msg: Optional[HumanMessage] = None
    rest: List[BaseMessage] = []
    for msg in messages:
        if human_msg is None and isinstance(msg, HumanMessage):
            human_msg = msg
        else:
            rest.append(msg)

    cycles = _extract_cycles(rest)
    stats["cycles_total"] = len(cycles)

    # Shared counter for content store ref IDs (monotone across all tiers)
    counter = 1

    # ── Tier 1: latest cycle at full fidelity ──────────────────────────────
    tier1_msgs: List[BaseMessage] = []
    if cycles:
        latest = cycles[-1]
        # Even Tier 1 gets large results stored (but at full excerpt, no further truncation)
        tier1_msgs, counter = _summarise_large_tool_messages(latest, run_folder, counter)
        stats["cycles_in_tier1"] = 1

    # ── Tier 2: previous TIER2_CYCLES cycles, large results summarised ─────
    older = cycles[:-1] if cycles else []
    tier2_raw = older[-TIER2_CYCLES:]
    tier3_raw = older[: len(older) - len(tier2_raw)]

    tier2_msgs: List[BaseMessage] = []
    for cycle in tier2_raw:
        processed, counter = _summarise_large_tool_messages(cycle, run_folder, counter)
        tier2_msgs.extend(processed)
        stats["cycles_in_tier2"] += 1

    # ── Tier 3: compress historical cycles into a bullet summary ───────────
    tier3_msgs: List[BaseMessage] = []
    if tier3_raw:
        summary_msg, counter = compress_cycle_to_summary(tier3_raw, run_folder, counter)
        tier3_msgs = [summary_msg]
        stats["cycles_compressed"] = len(tier3_raw)

    # ── Budget check: drop Tier 3 if it still pushes us over ──────────────
    def _list_tokens(msgs: List[BaseMessage]) -> int:
        return sum(_msg_tokens(m) for m in msgs)

    total_est = (
        sys_tokens
        + (_msg_tokens(human_msg) if human_msg else 0)
        + _list_tokens(tier3_msgs)
        + _list_tokens(tier2_msgs)
        + _list_tokens(tier1_msgs)
    )

    if total_est > max_tokens and tier3_msgs:
        logger.warning(
            "content_manager: dropping Tier 3 summary (%d est. tokens) — still over budget",
            _list_tokens(tier3_msgs),
        )
        stats["cycles_dropped"] = stats["cycles_compressed"]
        stats["cycles_compressed"] = 0
        tier3_msgs = []

    # ── Assemble: [System, Human, Tier3-summary?, Tier2..., Tier1-latest] ──
    final: List[BaseMessage] = [system_msg]
    if human_msg:
        final.append(human_msg)
    final.extend(tier3_msgs)
    final.extend(tier2_msgs)
    final.extend(tier1_msgs)

    stats["output_messages"] = len(final)
    stats["output_tokens_est"] = _list_tokens(final)

    return final, stats
