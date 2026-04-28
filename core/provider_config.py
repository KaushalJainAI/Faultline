import os
from typing import Optional, Tuple

from core.cli_provider import ProviderManager


API_PROVIDERS = {"openai", "openrouter", "anthropic", "google"}
CLI_PROVIDERS = {
    "claude_cli": "claude",
    "claude-code": "claude",
    "claude": "claude",
    "gemini_cli": "gemini",
    "gemini": "gemini",
    "codex_cli": "codex",
    "codex": "codex",
}


def get_provider() -> str:
    return os.environ.get("FAULTLINE_PROVIDER", "openrouter").lower().strip()


def get_cli_provider_name(provider: Optional[str] = None) -> Optional[str]:
    return CLI_PROVIDERS.get((provider or get_provider()).lower().strip())


def get_config_status(target_dir: str = ".") -> Tuple[bool, str]:
    provider = get_provider()

    if provider == "openrouter":
        if os.environ.get("OPENROUTER_API_KEY"):
            return True, "OpenRouter API key configured."
        return False, "OPENROUTER_API_KEY is required when FAULTLINE_PROVIDER=openrouter."

    if provider == "openai":
        if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY"):
            return True, "OpenAI-compatible API key configured."
        return False, "OPENAI_API_KEY or OPENROUTER_API_KEY is required when FAULTLINE_PROVIDER=openai."

    if provider == "anthropic":
        if os.environ.get("ANTHROPIC_API_KEY"):
            return True, "Anthropic API key configured."
        return False, "ANTHROPIC_API_KEY is required when FAULTLINE_PROVIDER=anthropic."

    if provider == "google":
        if os.environ.get("GOOGLE_API_KEY"):
            return True, "Google API key configured."
        return False, "GOOGLE_API_KEY is required when FAULTLINE_PROVIDER=google."

    cli_name = get_cli_provider_name(provider)
    if cli_name:
        status = ProviderManager(target_dir=target_dir).get_status().get(cli_name)
        if status and status["installed"] and status["auth_ok"]:
            return True, status["message"]
        if status and not status["installed"]:
            return False, f"{cli_name} CLI is not installed. Install and log in, or choose an API provider."
        return False, f"{cli_name} CLI is installed but not authenticated: {status['message'] if status else 'unknown status'}"

    supported = ", ".join(sorted(API_PROVIDERS | set(CLI_PROVIDERS)))
    return False, f"Unknown FAULTLINE_PROVIDER '{provider}'. Supported providers: {supported}."
