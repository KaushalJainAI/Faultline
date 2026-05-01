"""
core/model_registry.py

Hardcoded catalog of LLM models available for Faultline agent campaigns.
Supports listing, fuzzy matching, and hot-swapping the active model mid-run.

Derived from AIAAS/Backend/populate_models.py model catalog.
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class ModelEntry:
    name: str           # Human-readable name
    value: str          # API model identifier
    provider: str       # Provider slug (openrouter, openai, anthropic, google)
    is_free: bool       # Whether the model is free-tier
    supports_tools: bool  # Whether the model supports tool calling


# ---------------------------------------------------------------------------
# Model Catalog — curated list of models that support tool-calling
# (Faultline requires tool-calling for the agent loop)
# ---------------------------------------------------------------------------

_CATALOG: List[ModelEntry] = [
    # OpenRouter (routed)
    ModelEntry("Kimi K2.6", "moonshotai/kimi-k2.6", "openrouter", False, True),
    ModelEntry("GPT-4o", "openai/gpt-4o", "openrouter", False, True),
    ModelEntry("GPT-4o Mini", "openai/gpt-4o-mini", "openrouter", False, True),
    ModelEntry("Claude Sonnet 4.6", "anthropic/claude-sonnet-4.6", "openrouter", False, True),
    ModelEntry("Claude Haiku 4.5", "anthropic/claude-haiku-4.5", "openrouter", False, True),
    ModelEntry("Claude Opus 4.6", "anthropic/claude-opus-4.6", "openrouter", False, True),
    ModelEntry("Gemini 2.5 Pro", "google/gemini-2.5-pro", "openrouter", False, True),
    ModelEntry("Gemini 2.5 Flash", "google/gemini-2.5-flash", "openrouter", True, True),
    ModelEntry("Gemini 3.1 Flash Preview", "google/gemini-3.1-flash-preview", "openrouter", False, True),
    ModelEntry("DeepSeek V3", "deepseek/deepseek-chat", "openrouter", False, True),
    ModelEntry("DeepSeek R1", "deepseek/deepseek-r1", "openrouter", False, True),
    ModelEntry("Grok 4", "x-ai/grok-4.20", "openrouter", False, True),
    ModelEntry("Qwen3 32B", "qwen/qwen3-32b", "openrouter", False, True),
    ModelEntry("Llama 4 Scout", "meta-llama/llama-4-scout", "openrouter", False, True),
    ModelEntry("MiniMax M2.5 (Free)", "minimax/minimax-m2.5:free", "openrouter", True, True),
    ModelEntry("Gemma 4 31B (Free)", "google/gemma-4-31b-it:free", "openrouter", True, True),

    # Direct OpenAI
    ModelEntry("GPT-4o (Direct)", "gpt-4o", "openai", False, True),
    ModelEntry("GPT-4o Mini (Direct)", "gpt-4o-mini", "openai", False, True),

    # Direct Anthropic
    ModelEntry("Claude Sonnet 4.5 (Direct)", "claude-sonnet-4-5", "anthropic", False, True),
    ModelEntry("Claude Haiku 4.5 (Direct)", "claude-haiku-4-5", "anthropic", False, True),

    # Direct Google
    ModelEntry("Gemini 2.5 Flash (Direct)", "gemini-2.5-flash", "google", False, True),
    ModelEntry("Gemini 2.0 Flash (Direct)", "gemini-2.0-flash-001", "google", False, True),
]


def list_models(provider_filter: Optional[str] = None, free_only: bool = False) -> List[ModelEntry]:
    """Return models filtered by provider and/or free-tier status."""
    results = _CATALOG
    if provider_filter:
        results = [m for m in results if m.provider == provider_filter.lower()]
    if free_only:
        results = [m for m in results if m.is_free]
    return results


def find_model(query: str) -> Optional[ModelEntry]:
    """
    Fuzzy-find a model by partial name or value match.
    Returns the best match or None.
    """
    q = query.lower().strip()

    # Exact value match first
    for m in _CATALOG:
        if m.value == q:
            return m

    # Exact name match (case-insensitive)
    for m in _CATALOG:
        if m.name.lower() == q:
            return m

    # Partial match on value
    for m in _CATALOG:
        if q in m.value.lower():
            return m

    # Partial match on name
    for m in _CATALOG:
        if q in m.name.lower():
            return m

    return None


def format_model_list(models: Optional[List[ModelEntry]] = None) -> str:
    """Format the model list as a rich-printable table string."""
    models = models or _CATALOG
    lines = [
        "  # │ Provider    │ Model                          │ Value                              │ Free",
        "  ──┼─────────────┼────────────────────────────────┼────────────────────────────────────┼─────",
    ]
    for i, m in enumerate(models, 1):
        free = "✓" if m.is_free else " "
        lines.append(
            f"  {i:>2} │ {m.provider:<11} │ {m.name:<30} │ {m.value:<34} │  {free}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Active model state — mutable at runtime via /model command
# ---------------------------------------------------------------------------

_active_model: Optional[str] = None
_active_provider: Optional[str] = None


def get_active_model() -> Tuple[Optional[str], Optional[str]]:
    """Return (model_value, provider) for the currently active override, or (None, None)."""
    return _active_model, _active_provider


def set_active_model(model_value: str, provider: str) -> None:
    """Set a runtime model override. Takes effect on the next agent turn."""
    global _active_model, _active_provider
    _active_model = model_value
    _active_provider = provider


def clear_active_model() -> None:
    """Clear the runtime override, falling back to .env defaults."""
    global _active_model, _active_provider
    _active_model = None
    _active_provider = None
