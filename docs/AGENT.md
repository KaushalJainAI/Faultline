# Aegis-Breaker Agent

Aegis-Breaker is the LangGraph orchestration layer for Faultline. Its job is to combine normal QA verification with adversarial chaos testing.

## Runtime Flow

The current graph is intentionally simple:

1. `agent`: Calls the configured chat model with campaign context and the Faultline tool list.
2. `tools`: Executes any requested tool calls.
3. Loop back to `agent` until the model returns a message without tool calls.

The model receives:

- Target directory.
- Target base URL.
- Target log file.
- The system prompt from `core/prompts.py`.

## Available Tools

- `analyze_project_structure`: AST map of Python files, classes, functions, and imports.
- `index_project_documentation`: Indexes Markdown documentation into FAISS.
- `query_knowledge_base`: Searches indexed documentation.
- `validate_python_code`: Checks generated Python snippets for syntax and missing imports.
- `run_functional_test`: Writes and runs a temporary pytest file.
- `execute_chaos_campaign`: Sends adversarial HTTP payloads and correlates log crashes.
- `propose_code_patch`: Writes proposed fixes into `.aegis_patches`.
- `save_vulnerability_report`: Saves Markdown reports into `reports/`.

## Configuration

The default LLM client points at OpenRouter through `ChatOpenAI`. Set:

```bash
set OPENROUTER_API_KEY=your_key_here
```

Other API providers are selected with `FAULTLINE_PROVIDER=openai`, `anthropic`, or `google` plus the matching API key.

Subscription-backed CLIs are selected with:

```bash
set FAULTLINE_PROVIDER=claude_cli
set FAULTLINE_PROVIDER=gemini_cli
set FAULTLINE_PROVIDER=codex_cli
```

CLI modes require the matching local CLI to be installed and authenticated. They run the campaign prompt through that CLI, which lets Faultline use subscription allowances where available. API modes keep LangChain tool calling.

CLI provider commands:

```bash
claude -p "<prompt>"
gemini -p "<prompt>" --skip-trust
codex exec "<prompt>" --cd "<target_dir>" --sandbox read-only
```

If a CLI is not on `PATH`, set `FAULTLINE_CLAUDE_BINARY`, `FAULTLINE_GEMINI_BINARY`, or `FAULTLINE_CODEX_BINARY` to the executable path. Optional extra flags can be supplied with `FAULTLINE_CLAUDE_CLI_ARGS`, `FAULTLINE_GEMINI_CLI_ARGS`, and `FAULTLINE_CODEX_CLI_ARGS`. Codex sandbox mode defaults to `read-only` and can be changed with `FAULTLINE_CODEX_SANDBOX`.

If the selected provider is unavailable, the agent returns a configuration message instead of running a real campaign. The campaign start API also rejects missing provider configuration before creating a background run.

## Intended Campaign Behavior

A complete campaign should:

1. Discover structure and documentation intent.
2. Verify expected behavior with generated functional tests.
3. Generate adversarial payloads from discovered endpoint context.
4. Execute attacks against the target URL.
5. Correlate crashes from logs using `X-Aegis-Request-ID`.
6. Save a report and propose patches when useful.

Campaigns now persist status, tool runs, findings, and Markdown report paths in the Django database. Richer endpoint schema extraction remains a planned next step.
