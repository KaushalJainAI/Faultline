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
    context_window: int   # Context window size in tokens


# ---------------------------------------------------------------------------
_CATALOG: List[ModelEntry] = [
    # â”€â”€â”€ OpenRouter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    # â”€â”€ Tier 1: Frontier / Max Intelligence (AA Score 50+) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("Kimi K2.6",                  "moonshotai/kimi-k2.6",                      "openrouter", False, True, 256_000),
    ModelEntry("Claude Opus 4.7",            "anthropic/claude-opus-4.7",                 "openrouter", False, True, 1_000_000),
    ModelEntry("Gemini 3.1 Pro Preview",     "google/gemini-3-1-pro-preview",             "openrouter", False, True, 1_000_000),
    ModelEntry("Grok 4.3",                   "x-ai/grok-4.3",                             "openrouter", False, True, 1_000_000),
    ModelEntry("MiMo V2.5 Pro",              "xiaomi/mimo-v2-5-pro",                      "openrouter", False, True, 1_000_000),
    ModelEntry("Qwen3.6 Max Preview",        "qwen/qwen3-6-max-preview",                  "openrouter", False, True, 256_000),
    ModelEntry("Claude Sonnet 4.6",          "anthropic/claude-sonnet-4-6",               "openrouter", False, True, 1_000_000),
    ModelEntry("DeepSeek V4 Pro",            "deepseek/deepseek-v4-pro",                  "openrouter", False, True, 128_000),
    ModelEntry("GLM-5.1",                    "zhipuai/glm-5-1",                           "openrouter", False, True, 200_000),
    ModelEntry("MiniMax M2.7",               "minimax/minimax-m2.7",                      "openrouter", False, True, 200_000),

    # â”€â”€ Tier 2: Strong Mid-tier (AA Score 39â€“49) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("MiMo V2 Pro",                "xiaomi/mimo-v2-pro",                        "openrouter", False, True, 1_000_000),
    ModelEntry("DeepSeek V4 Flash",          "deepseek/deepseek-v4-flash",                "openrouter", False, True, 1_000_000),
    ModelEntry("Gemini 3 Flash",             "google/gemini-3-flash",                     "openrouter", False, True, 1_000_000),
    ModelEntry("GLM-5",                      "zhipuai/glm-5",                             "openrouter", False, True, 200_000),
    ModelEntry("MiMo V2 Omni",              "xiaomi/mimo-v2-omni",                        "openrouter", False, True, 256_000),
    ModelEntry("Grok 4.1 Fast",              "x-ai/grok-4.1-fast",                        "openrouter", False, True, 2_000_000),
    ModelEntry("Mistral Medium 3.5",         "mistralai/mistral-medium-3.5",              "openrouter", False, True, 256_000),
    ModelEntry("o3",                         "openai/o3",                                 "openrouter", False, True, 200_000),
    ModelEntry("Qwen3.6 Plus",               "qwen/qwen3-6-plus",                         "openrouter", False, True, 1_000_000),
    ModelEntry("Qwen3.6 27B",               "qwen/qwen3-6-27b",                           "openrouter", False, True, 262_000),
    ModelEntry("Qwen3.5 397B A17B",          "qwen/qwen3-5-397b-a17b",                   "openrouter", False, True, 262_000),
    ModelEntry("Gemini 2.5 Pro",             "google/gemini-2.5-pro",                     "openrouter", False, True, 1_000_000),
    ModelEntry("DeepSeek V3.2",              "deepseek/deepseek-v3.2",                    "openrouter", False, True, 128_000),

    # â”€â”€ Tier 3: Fast / Cheap / High-value Agents (AA Score 27â€“38) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("Gemini 3 Flash Preview",     "google/gemini-3-flash-preview",             "openrouter", False, True, 1_000_000),
    ModelEntry("Step 3.5 Flash",             "stepfun/step-3.5-flash",                    "openrouter", True,  True, 256_000),
    ModelEntry("Claude Haiku 4.5",           "anthropic/claude-haiku-4-5",                "openrouter", False, True, 200_000),
    ModelEntry("NVIDIA Nemotron 3 Super",    "nvidia/nemotron-3-super-120b-a12b",         "openrouter", False, True, 1_000_000),
    ModelEntry("Gemini 2.5 Flash",           "google/gemini-2.5-flash",                   "openrouter", True,  True, 1_000_000),
    ModelEntry("Mistral Small 4",            "mistralai/mistral-small-4",                 "openrouter", False, True, 256_000),
    ModelEntry("Magistral Medium 1.2",       "mistralai/magistral-medium-2509",           "openrouter", False, True, 128_000),
    ModelEntry("Mercury 2",                  "inception/mercury-2",                       "openrouter", False, True, 128_000),
    ModelEntry("Grok 3 Mini Reasoning",      "x-ai/grok-3-mini",                          "openrouter", False, True, 1_000_000),
    ModelEntry("Qwen3.6 35B A3B",            "qwen/qwen3-6-35b-a3b",                      "openrouter", False, True, 262_000),
    ModelEntry("Qwen3.5 122B A10B",          "qwen/qwen3-5-122b-a10b",                   "openrouter", False, True, 262_000),
    ModelEntry("Qwen3 Next 80B",             "qwen/qwen3-next-80b-a3b",                  "openrouter", False, True, 262_000),
    ModelEntry("Qwen3 Coder Next",           "qwen/qwen3-coder-next",                     "openrouter", False, True, 256_000),
    ModelEntry("Llama 4 Maverick",           "meta-llama/llama-4-maverick",               "openrouter", False, True, 512_000),
    ModelEntry("Llama 4 Scout",              "meta-llama/llama-4-scout",                  "openrouter", False, True, 10_000_000),
    ModelEntry("Amazon Nova 2 Lite",         "amazon/nova-2-0-lite",                      "openrouter", False, True, 1_000_000),
    ModelEntry("Mistral Large 3",            "mistralai/mistral-large-3",                 "openrouter", False, True, 256_000),
    ModelEntry("Devstral 2",                 "mistralai/devstral-2",                      "openrouter", True,  True, 256_000),
    ModelEntry("Amazon Nova 2 Omni",         "amazon/nova-2-0-omni",                      "openrouter", False, True, 1_000_000),
    ModelEntry("GPT-OSS 120B",               "openai/gpt-oss-120b",                       "openrouter", False, True, 131_000),
    ModelEntry("Llama 3.3 70B",              "meta-llama/llama-3-3-70b-instruct",         "openrouter", False, True, 128_000),
    ModelEntry("Ministral 3 8B",             "mistralai/ministral-3b-instruct",           "openrouter", False, True, 128_000),

    # â”€â”€ Free Tier (Agent-usable, high rate limits) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("Tencent Hy3 (Free)",         "tencent/hy3-preview:free",                  "openrouter", True,  True, 256_000),
    ModelEntry("Nemotron 3 Super (Free)",    "nvidia/nemotron-3-super-120b-a12b:free",     "openrouter", True,  True, 1_000_000),
    ModelEntry("Owl Alpha (Free)",           "openrouter/owl-alpha",                       "openrouter", True,  True, 1_000_000),
    ModelEntry("Devstral Small 2 (Free)",    "mistralai/devstral-small-2:free",            "openrouter", True,  True, 256_000),
    ModelEntry("Ling 2.6 1T (Free)",         "inclusionai/ling-2.6-1t:free",              "openrouter", True,  True, 262_000),
    ModelEntry("Ling 2.6 Flash",             "inclusionai/ling-2-6-flash",                "openrouter", True,  True, 262_000),
    ModelEntry("Laguna M.1 (Free)",          "poolside/laguna-m.1:free",                  "openrouter", True,  True, 128_000),
    ModelEntry("Gemma 4 31B (Free)",         "google/gemma-4-31b-it:free",                "openrouter", True,  True, 256_000),
    ModelEntry("MiniMax M2.5 (Free)",        "minimax/minimax-m2.5:free",                 "openrouter", True,  True, 200_000),
    ModelEntry("MiMo V2 Omni (Free)",        "xiaomi/mimo-v2-omni:free",                  "openrouter", True,  True, 256_000),

    # â”€â”€â”€ Direct OpenAI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("GPT-5.5 (Direct)",           "gpt-5.5",                                   "openai",     False, True, 922_000),
    ModelEntry("GPT-4.1 (Direct)",           "gpt-4.1",                                   "openai",     False, True, 1_000_000),
    ModelEntry("GPT-4.1 Mini (Direct)",      "gpt-4.1-mini",                              "openai",     False, True, 400_000),
    ModelEntry("GPT-4.1 Nano (Direct)",      "gpt-4.1-nano",                              "openai",     False, True, 400_000),
    ModelEntry("o4-mini (Direct)",           "o4-mini",                                   "openai",     False, True, 128_000),
    ModelEntry("o3 (Direct)",                "o3",                                        "openai",     False, True, 200_000),

    # â”€â”€â”€ Direct Anthropic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("Claude Sonnet 4.6 (Direct)", "claude-sonnet-4-6",                         "anthropic",  False, True, 1_000_000),
    ModelEntry("Claude Opus 4.7 (Direct)",   "claude-opus-4-7",                           "anthropic",  False, True, 1_000_000),
    ModelEntry("Claude Haiku 4.5 (Direct)",  "claude-haiku-4-5",                          "anthropic",  False, True, 200_000),

    # â”€â”€â”€ Direct NVIDIA NIM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("Llama-3.3 Nemotron Super 49B (NVIDIA)", "nvidia/llama-3.3-nemotron-super-49b-v1", "nvidia", False, True, 128_000),
    ModelEntry("Llama-3.1 Nemotron 70B (NVIDIA)",       "nvidia/llama-3.1-nemotron-70b-instruct", "nvidia", False, True, 128_000),
    ModelEntry("Nemotron-3 Super 120B (NVIDIA)",         "nvidia/nemotron-3-super-120b-a12b",      "nvidia", False, True, 210_000),
    ModelEntry("Kimi K2.6 (NVIDIA)",                     "moonshotai/kimi-k2.6",                   "nvidia", False, True, 256_000),
    ModelEntry("Nemotron Mini 4B (NVIDIA)",              "nvidia/nemotron-mini-4b-instruct",       "nvidia", False, True, 128_000),

    # â”€â”€â”€ Direct Google â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    ModelEntry("Gemini 3.1 Pro (Direct)",    "gemini-3.1-pro-preview",                    "google",     False, True, 1_000_000),
    ModelEntry("Gemini 3 Flash (Direct)",    "gemini-3-flash",                            "google",     False, True, 1_000_000),
    ModelEntry("Gemini 3 Flash Preview (Direct)", "gemini-3-flash-preview",               "google",     False, True, 1_000_000),
    ModelEntry("Gemini 2.5 Flash (Direct)",  "gemini-2.5-flash",                          "google",     False, True, 1_000_000),
    ModelEntry("Gemini 2.5 Flash Lite (Direct)", "gemini-2.5-flash-lite",                 "google",     False, True, 1_000_000),
]




def list_models(provider_filter: Optional[str] = None, free_only: bool = False) -> List[ModelEntry]:
    """Return models filtered by provider and/or free-tier status."""
    results = _get_merged_catalog()
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
    catalog = _get_merged_catalog()

    # Exact value match first
    for m in catalog:
        if m.value == q:
            return m

    # Exact name match (case-insensitive)
    for m in catalog:
        if m.name.lower() == q:
            return m

    # Partial match on value
    for m in catalog:
        if q in m.value.lower():
            return m

    # Partial match on name
    for m in catalog:
        if q in m.name.lower():
            return m

    return None


def get_model_info(model_value: str) -> Optional[ModelEntry]:
    """Retrieve ModelEntry for a specific model identifier."""
    catalog = _get_merged_catalog()
    for m in catalog:
        if m.value == model_value:
            return m
    return None


def format_model_list(models: Optional[List[ModelEntry]] = None) -> str:
    """Format the model list as a rich-printable table string."""
    models = models or _get_merged_catalog()
    lines = [
        "  # â”‚ Provider    â”‚ Model                          â”‚ Value                              â”‚ Context â”‚ Free",
        "  â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€",
    ]
    for i, m in enumerate(models, 1):
        free = "âœ“" if m.is_free else " "
        ctx = f"{m.context_window // 1000}k" if m.context_window < 1_000_000 else f"{m.context_window // 1_000_000}M"
        lines.append(
            f"  {i:>2} â”‚ {m.provider:<11} â”‚ {m.name:<30} â”‚ {m.value:<34} â”‚ {ctx:<7} â”‚  {free}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dynamic Overrides â€” loads from model_registry_overrides.json if present
# ---------------------------------------------------------------------------

def _get_merged_catalog() -> List[ModelEntry]:
    """Merge hardcoded _CATALOG with any overrides from local JSON."""
    overrides_path = os.path.join(os.getcwd(), "model_registry_overrides.json")
    if not os.path.exists(overrides_path):
        return _CATALOG

    try:
        import json
        with open(overrides_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, list):
                return _CATALOG
            
            merged = list(_CATALOG)
            # Replace existing or append new
            for entry in data:
                new_m = ModelEntry(**entry)
                found = False
                for idx, existing in enumerate(merged):
                    if existing.value == new_m.value:
                        merged[idx] = new_m
                        found = True
                        break
                if not found:
                    merged.append(new_m)
            return merged
    except Exception:
        return _CATALOG


# ---------------------------------------------------------------------------
# Active model state â€” mutable at runtime via /model command
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

