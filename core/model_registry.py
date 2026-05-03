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
    # ─── OpenRouter ─────────────────────────────────────────────────────────────

    # ── Tier 1: Frontier / Max Intelligence (AA Score 50+) ───────────────────────
    # AA #1 overall; 256K ctx; $0.74/$3.49; 1.94T tokens/week on OR [web:page]
    ModelEntry("Kimi K2.6",                  "moonshotai/kimi-k2.6",                      "openrouter", False, True),
    # AA #3; 1M ctx; $10; best Anthropic reasoning; OR #4 weekly [web:page]
    ModelEntry("Claude Opus 4.7",            "anthropic/claude-opus-4.7",                 "openrouter", False, True),
    # AA #4; 1M ctx; $4.50 blended; fastest frontier; 123 tok/s [web:page]
    ModelEntry("Gemini 3.1 Pro Preview",     "google/gemini-3-1-pro-preview",             "openrouter", False, True),
    # AA #10; 1M ctx; $1.56; 199 tok/s; fastest top-10; OR launch today [web:page]
    ModelEntry("Grok 4.3",                   "x-ai/grok-4.3",                             "openrouter", False, True),
    # AA #8; 1M ctx; $1.50 blended; 54 score; OR top-10 weekly [web:page]
    ModelEntry("MiMo V2.5 Pro",              "xiaomi/mimo-v2-5-pro",                      "openrouter", False, True),
    # Qwen flagship; 256K ctx; $2.92; AA score 52 [web:page]
    ModelEntry("Qwen3.6 Max Preview",        "qwen/qwen3-6-max-preview",                  "openrouter", False, True),
    # AA score 52; 1M ctx; $6.00; top Sonnet; OR #3 weekly [web:page]
    ModelEntry("Claude Sonnet 4.6",          "anthropic/claude-sonnet-4.6",               "openrouter", False, True),
    # AA score 52; 1M ctx; $2.17; deep reasoning toggle; OR #5 weekly [web:page]
    ModelEntry("DeepSeek V4 Pro",            "deepseek/deepseek-v4-pro",                  "openrouter", False, True),
    # AA score 51; 200K ctx; $2.15; SWE-bench #1 open-source 58.4% [web:page]
    ModelEntry("GLM-5.1",                    "zhipuai/glm-5-1",                           "openrouter", False, True),
    # AA score 50; 205K ctx; $0.53; incredible value at this tier [web:page]
    ModelEntry("MiniMax M2.7",               "minimax/minimax-m2.7",                      "openrouter", False, True),

    # ── Tier 2: Strong Mid-tier (AA Score 39–49) ────────────────────────────────
    # AA score 49; 1M ctx; $1.50; 62 tok/s; Xiaomi open-weight flagship [web:page]
    ModelEntry("MiMo V2 Pro",                "xiaomi/mimo-v2-pro",                        "openrouter", False, True),
    # AA score 47; 1M ctx; $0.17; thinking toggle; 83 tok/s [web:page]
    ModelEntry("DeepSeek V4 Flash",          "deepseek/deepseek-v4-flash",                "openrouter", False, True),
    # AA score 46; 1M ctx; $1.13; 166 tok/s; reasoning + non-reasoning [web:page]
    ModelEntry("Gemini 3 Flash",             "google/gemini-3-flash",                     "openrouter", False, True),
    # AA score 50; 200K ctx; $1.55; Z.AI flagship MoE [web:page]
    ModelEntry("GLM-5",                      "zhipuai/glm-5",                             "openrouter", False, True),
    # AA score 45; 256K ctx; $0.00; free; omni-modal (text+img+video+audio) [web:page]
    ModelEntry("MiMo V2 Omni",              "xiaomi/mimo-v2-omni",                        "openrouter", False, True),
    # AA score 39; 2M ctx; $0.28; 126 tok/s; xAI fast tool-calling [web:page]
    ModelEntry("Grok 4.1 Fast",              "x-ai/grok-4.1-fast",                        "openrouter", False, True),
    # AA score 39; 256K ctx; $3.00; 165 tok/s; Mistral best mid-tier [web:page]
    ModelEntry("Mistral Medium 3.5",         "mistralai/mistral-medium-3.5",              "openrouter", False, True),
    # AA score 38; 200K ctx; $3.50; OpenAI strong reasoning [web:page]
    ModelEntry("o3",                         "openai/o3",                                 "openrouter", False, True),
    # AA score 50; 1M ctx; $1.13; Qwen3.6 thinking; OR popular [web:page]
    ModelEntry("Qwen3.6 Plus",               "qwen/qwen3-6-plus",                         "openrouter", False, True),
    # AA score 46; 262K ctx; $1.35; strong 27B dense [web:page]
    ModelEntry("Qwen3.6 27B",               "qwen/qwen3-6-27b",                           "openrouter", False, True),
    # AA score 45; 262K ctx; $1.35; 397B MoE A17B; deep reasoning [web:page]
    ModelEntry("Qwen3.5 397B A17B",          "qwen/qwen3-5-397b-a17b",                   "openrouter", False, True),
    # AA score 35; 1M ctx; $3.44; 2M ctx + multimodal; top visual [web:page]
    ModelEntry("Gemini 2.5 Pro",             "google/gemini-2.5-pro",                     "openrouter", False, True),
    # OR #5 weekly; V3 successor; thinking toggle; $0.252/$0.378 [web:page]
    ModelEntry("DeepSeek V3.2",              "deepseek/deepseek-v3.2",                    "openrouter", False, True),

    # ── Tier 3: Fast / Cheap / High-value Agents (AA Score 27–38) ───────────────
    # OR #6 weekly; 1M ctx; $0.50/$3.00; 123 tok/s; thinking levels [web:page]
    ModelEntry("Gemini 3 Flash Preview",     "google/gemini-3-flash-preview",             "openrouter", False, True),
    # OR #7 weekly; 256K; $0.00; 142 tok/s; AA score 38; free [web:page]
    ModelEntry("Step 3.5 Flash",             "stepfun/step-3.5-flash",                    "openrouter", True,  True),
    # AA score 37; 200K ctx; $2.00; 111 tok/s; fast Haiku w/ thinking [web:page]
    ModelEntry("Claude Haiku 4.5",           "anthropic/claude-haiku-4-5",                "openrouter", False, True),
    # AA score 36; 1M ctx; $0.41; 185 tok/s; NVIDIA open-weight [web:page]
    ModelEntry("NVIDIA Nemotron 3 Super",    "nvidia/nemotron-3-super-120b-a12b",         "openrouter", False, True),
    # AA score 35; 1M ctx; $0.30/$2.50; configurable thinking; OR top 10 [web:page]
    ModelEntry("Gemini 2.5 Flash",           "google/gemini-2.5-flash",                   "openrouter", True,  True),
    # AA score 28; 256K ctx; $0.26; 146 tok/s; new Mistral small [web:page]
    ModelEntry("Mistral Small 4",            "mistralai/mistral-small-4",                 "openrouter", False, True),
    # AA score 27; 128K ctx; $2.75; Mistral reasoning flagship [web:page]
    ModelEntry("Magistral Medium 1.2",       "mistralai/magistral-medium-2509",           "openrouter", False, True),
    # AA score 33; 128K ctx; $0.38; 821 tok/s — fastest model overall [web:page]
    ModelEntry("Mercury 2",                  "inception/mercury-2",                       "openrouter", False, True),
    # AA score 32; 1M ctx; $0.35; 200 tok/s; xAI cheap reasoning [web:page]
    ModelEntry("Grok 3 Mini Reasoning",      "x-ai/grok-3-mini",                          "openrouter", False, True),
    # OR weekly top 20; 262K; $0.56; 193 tok/s; MoE 35B A3B [web:page]
    ModelEntry("Qwen3.6 35B A3B",            "qwen/qwen3-6-35b-a3b",                      "openrouter", False, True),
    # AA score 42; 262K ctx; $1.10; 147 tok/s; thinking [web:page]
    ModelEntry("Qwen3.5 122B A10B",          "qwen/qwen3-5-122b-a10b",                   "openrouter", False, True),
    # AA score 27; 262K ctx; $1.88; 169 tok/s; Qwen3 reasoning model [web:page]
    ModelEntry("Qwen3 Next 80B",             "qwen/qwen3-next-80b-a3b",                  "openrouter", False, True),
    # AA score 28; 256K ctx; $0.60; 163 tok/s; Qwen dedicated coder [web:page]
    ModelEntry("Qwen3 Coder Next",           "qwen/qwen3-coder-next",                     "openrouter", False, True),
    # 1M ctx; $0.47; 1M ctx MoE; multimodal; Meta open [web:page]
    ModelEntry("Llama 4 Maverick",           "meta-llama/llama-4-maverick",               "openrouter", False, True),
    # 10M ctx; $0.29; 127 tok/s; ultra-long context tasks [web:page]
    ModelEntry("Llama 4 Scout",              "meta-llama/llama-4-scout",                  "openrouter", False, True),
    # AA score 36; Amazon Nova mid-tier reasoning; 1M ctx; $0.85 [web:page]
    ModelEntry("Amazon Nova 2 Lite",         "amazon/nova-2-0-lite",                      "openrouter", False, True),
    # AA score 23; 256K ctx; $0.75; 49 tok/s; strong open Mistral [web:page]
    ModelEntry("Mistral Large 3",            "mistralai/mistral-large-3",                 "openrouter", False, True),
    # AA score 22; free; 256K; Mistral open coding agent v2 [web:page]
    ModelEntry("Devstral 2",                 "mistralai/devstral-2",                      "openrouter", True,  True),
    # AA score 36; 1M ctx; $0.85; 191 tok/s; Amazon omni fast [web:page]
    ModelEntry("Amazon Nova 2 Omni",         "amazon/nova-2-0-omni",                      "openrouter", False, True),
    # AA score 33; 131K ctx; $0.26; 214 tok/s; OpenAI OSS 120B [web:page]
    ModelEntry("GPT-OSS 120B",               "openai/gpt-oss-120b",                       "openrouter", False, True),
    # AA score 14; 128K ctx; $0.68; 90 tok/s; solid cheap open [web:page]
    ModelEntry("Llama 3.3 70B",              "meta-llama/llama-3-3-70b-instruct",         "openrouter", False, True),
    # AA score 27; 128K ctx; $0.15; 134 tok/s; excellent edge model [web:page]
    ModelEntry("Ministral 3 8B",             "mistralai/ministral-3b-instruct",           "openrouter", False, True),

    # ── Free Tier (Agent-usable, high rate limits) ───────────────────────────────
    # OR #1 free weekly (2925% growth!); 256K; Tencent MoE; thinking; AA score 42 [web:page]
    ModelEntry("Tencent Hy3 (Free)",         "tencent/hy3-preview:free",                  "openrouter", True,  True),
    # OR #10 weekly overall; 1M ctx; NVIDIA 120B MoE; AA score 36 [web:page]
    ModelEntry("Nemotron 3 Super (Free)",    "nvidia/nemotron-3-super-120b-a12b:free",     "openrouter", True,  True),
    # 1M ctx; OpenRouter's own; $0; designed for agentic loops [web:page]
    ModelEntry("Owl Alpha (Free)",           "openrouter/owl-alpha",                       "openrouter", True,  True),
    # Free Mistral coding agent; 256K; $0; AA score 22 [web:page]
    ModelEntry("Devstral Small 2 (Free)",    "mistralai/devstral-small-2:free",            "openrouter", True,  True),
    # InclusionAI 1T MoE; 262K; AA score 34; $0 (limited time) [web:page]
    ModelEntry("Ling 2.6 1T (Free)",         "inclusionai/ling-2.6-1t:free",              "openrouter", True,  True),
    # 262K; 210 tok/s; InclusionAI fast reasoning; $0.15 (or free tier) [web:page]
    ModelEntry("Ling 2.6 Flash",             "inclusionai/ling-2-6-flash",                "openrouter", True,  True),
    # Poolside flagship coding agent; 128K; $0 [web:page]
    ModelEntry("Laguna M.1 (Free)",          "poolside/laguna-m.1:free",                  "openrouter", True,  True),
    # $0; lightweight Google; 256K [web:page]
    ModelEntry("Gemma 4 31B (Free)",         "google/gemma-4-31b-it:free",                "openrouter", True,  True),
    # MiniMax free; good high-volume fallback [web:page]
    ModelEntry("MiniMax M2.5 (Free)",        "minimax/minimax-m2.5:free",                 "openrouter", True,  True),
    # $0; 256K; Xiaomi omni-modal; 119 tok/s; AA score 45 [web:page]
    ModelEntry("MiMo V2 Omni (Free)",        "xiaomi/mimo-v2-omni:free",                  "openrouter", True,  True),

    # ─── Direct OpenAI ──────────────────────────────────────────────────────────
    # GPT-5.5 flagship; $5/$30; 922K ctx
    ModelEntry("GPT-5.5 (Direct)",           "gpt-5.5",                                   "openai",     False, True),
    # Best API agentic model; 1M ctx; $2/$8
    ModelEntry("GPT-4.1 (Direct)",           "gpt-4.1",                                   "openai",     False, True),
    # Fast cheap GPT; 400K ctx; $0.40/$1.60
    ModelEntry("GPT-4.1 Mini (Direct)",      "gpt-4.1-mini",                              "openai",     False, True),
    # Cheapest OpenAI; 400K ctx; $0.10/$0.40
    ModelEntry("GPT-4.1 Nano (Direct)",      "gpt-4.1-nano",                              "openai",     False, True),
    # Best reasoning/cost; $1.10/$4.40
    ModelEntry("o4-mini (Direct)",           "o4-mini",                                   "openai",     False, True),
    # Strong reasoning; $3.50; 200K ctx
    ModelEntry("o3 (Direct)",                "o3",                                        "openai",     False, True),

    # ─── Direct Anthropic ───────────────────────────────────────────────────────
    ModelEntry("Claude Sonnet 4.6 (Direct)", "claude-sonnet-4-6",                         "anthropic",  False, True),
    ModelEntry("Claude Opus 4.7 (Direct)",   "claude-opus-4-7",                           "anthropic",  False, True),
    ModelEntry("Claude Haiku 4.5 (Direct)",  "claude-haiku-4-5",                          "anthropic",  False, True),

    # ─── Direct NVIDIA NIM ──────────────────────────────────────────────────────
    # Nemotron Super 49B — recommended for tool-calling; free via NIM API
    ModelEntry("Llama-3.3 Nemotron Super 49B (NVIDIA)", "nvidia/llama-3.3-nemotron-super-49b-v1", "nvidia", False, True),
    # Nemotron 70B — high-quality, Llama-3.1 base
    ModelEntry("Llama-3.1 Nemotron 70B (NVIDIA)",       "nvidia/llama-3.1-nemotron-70b-instruct", "nvidia", False, True),
    # Nemotron-3 Super 120B — high-intelligence flagship; recommended for complex agentic tasks
    ModelEntry("Nemotron-3 Super 120B (NVIDIA)",         "nvidia/nemotron-3-super-120b-a12b",      "nvidia", False, True),
    # Nemotron Mini 4B — fast, lightweight
    ModelEntry("Nemotron Mini 4B (NVIDIA)",              "nvidia/nemotron-mini-4b-instruct",       "nvidia", False, True),

    # ─── Direct Google ──────────────────────────────────────────────────────────
    ModelEntry("Gemini 3.1 Pro (Direct)",    "gemini-3.1-pro-preview",                    "google",     False, True),
    ModelEntry("Gemini 3 Flash (Direct)",    "gemini-3-flash",                            "google",     False, True),
    ModelEntry("Gemini 3 Flash Preview (Direct)", "gemini-3-flash-preview",               "google",     False, True),
    ModelEntry("Gemini 2.5 Flash (Direct)",  "gemini-2.5-flash",                          "google",     False, True),
    ModelEntry("Gemini 2.5 Flash Lite (Direct)", "gemini-2.5-flash-lite",                 "google",     False, True),
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
