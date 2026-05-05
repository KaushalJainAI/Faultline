"""
Shared token estimation helpers.

This module centralizes the rough token-estimation formula while allowing each
caller to keep its own calibration (chars-per-token, minimum non-empty tokens).
"""

from __future__ import annotations


def estimate_tokens(
    text: str,
    *,
    chars_per_token: float = 4.0,
    min_tokens_for_non_empty: int = 1,
) -> int:
    """
    Rough token estimate using character length.

    Args:
        text: Input text to estimate.
        chars_per_token: Heuristic conversion ratio.
        min_tokens_for_non_empty: Lower bound when text is non-empty.
    """
    if not text:
        return 0

    if chars_per_token <= 0:
        chars_per_token = 4.0

    approx = int(len(text) / chars_per_token)
    return max(min_tokens_for_non_empty, approx)

