# LLM Providers Guide

**Date**: 2026-05-18
**Description**: Configuration reference for supported LLM providers, including direct API keys and local CLI adapters (Claude, Gemini, Codex).

Faultline supports multiple LLM providers for its agentic workflows. You can choose between direct API integration (high latency, API costs) or local CLI adapters (lower latency, utilizes existing subscriptions).

## Direct API Providers

To use a direct API, set the `FAULTLINE_PROVIDER` environment variable and the corresponding API key.

### OpenRouter (Default)
OpenRouter is the default provider and offers access to a variety of models.
-   **Provider**: `openrouter` (or leave empty)
-   **API Key**: `OPENROUTER_API_KEY`

### Anthropic
-   **Provider**: `anthropic`
-   **API Key**: `ANTHROPIC_API_KEY`

### OpenAI
-   **Provider**: `openai`
-   **API Key**: `OPENAI_API_KEY`

### Google (Gemini API)
-   **Provider**: `google`
-   **API Key**: `GOOGLE_API_KEY`

---

## Local CLI Adapters (Subsidized Usage)

CLI adapters delegate agent prompts to local, authenticated CLI tools. This is ideal if you have a personal or professional subscription to these services (e.g., Claude Pro, Gemini Advanced) and want to avoid per-token API billing.

### 1. Claude CLI
Integrates with the `claude` command-line tool.
-   **Provider**: `claude_cli`
-   **Installation**: Follow the official Anthropic Claude CLI installation guide.
-   **Authentication**: Ensure you are logged in via `claude login`.
-   **Configuration**:
    -   `FAULTLINE_CLAUDE_BINARY`: Path to the `claude` executable (optional if in PATH).
    -   `FAULTLINE_CLAUDE_CLI_ARGS`: Extra flags (e.g., `--model claude-3-5-sonnet`).

### 2. Gemini CLI
Integrates with the `gemini` command-line tool.
-   **Provider**: `gemini_cli`
-   **Installation**: Follow the official Google Gemini CLI installation guide.
-   **Authentication**: Ensure you are authenticated and have run `gemini --skip-trust` at least once.
-   **Configuration**:
    -   `FAULTLINE_GEMINI_BINARY`: Path to the `gemini` executable.
    -   `FAULTLINE_GEMINI_CLI_ARGS`: Extra flags (e.g., `--model gemini-1.5-pro`).

### 3. Codex CLI — run on a ChatGPT Plus/Pro subscription
Integrates with the OpenAI `codex` command-line tool. This is the supported
way to run Faultline on a **ChatGPT Plus/Pro subscription without a metered
OpenAI API key**: the Codex CLI authenticates via "Sign in with ChatGPT" and
draws on your subscription's Codex quota.

> ChatGPT Plus does **not** grant raw OpenAI API access — those are separately
> billed products. The Codex CLI's subscription-OAuth path is what makes this
> work; quotas/rate limits still apply to long agentic runs.

-   **Provider**: `codex_cli`
-   **Installation**: Install the OpenAI Codex CLI, then run `codex login`
    and sign in with your ChatGPT account.
-   **Authentication**: Verify status via `codex login status`.
-   **Configuration**:
    -   `FAULTLINE_CODEX_BINARY`: Path to the `codex` executable.
    -   `FAULTLINE_CODEX_SANDBOX`: Sandbox mode (default: `read-only`).
    -   `FAULTLINE_CODEX_CLI_ARGS`: Extra flags (e.g., `--model gpt-5-codex`).
-   **LLM-call budget**: When the provider resolves to Codex, Faultline
    auto-defaults `FAULTLINE_MAX_LLM_CALLS` to **40** (instead of 120) —
    Codex reaches conclusions in far fewer turns and this conserves your
    subscription quota. Override the Codex-only default with
    `FAULTLINE_CODEX_MAX_LLM_CALLS`, or set `FAULTLINE_MAX_LLM_CALLS`
    explicitly to override all provider defaults.

#### Quick start
```bash
codex login                       # sign in with your ChatGPT Plus/Pro account
codex login status                # confirm authenticated
# .env:  FAULTLINE_PROVIDER=codex_cli
python faultline.py --target-dir /path/to/project --mode hybrid
```

## Troubleshooting

### CLI Not Found
If Faultline cannot find your CLI binary, specify the absolute path using the `*_BINARY` variables:
```bash
set FAULTLINE_CLAUDE_BINARY=C:\Users\Name\AppData\Local\bin\claude.cmd
```

### Permission Errors
CLI tools often prompt for permissions when reading files or running commands. Faultline attempts to skip these via flags like `--dangerously-skip-permissions`. If your CLI version does not support these, you may need to manually approve the first few requests or run the CLI in a more permissive mode.

### Timeouts
Local CLI calls can take several minutes for complex tasks. If you encounter timeouts, you can adjust the task duration via CLI-specific arguments if the tool supports it.
