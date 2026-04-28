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

If `langchain-openai` or `OPENROUTER_API_KEY` is unavailable, the agent returns a configuration message instead of running a real campaign. The campaign start API also rejects missing `OPENROUTER_API_KEY` before creating a background run.

## Intended Campaign Behavior

A complete campaign should:

1. Discover structure and documentation intent.
2. Verify expected behavior with generated functional tests.
3. Generate adversarial payloads from discovered endpoint context.
4. Execute attacks against the target URL.
5. Correlate crashes from logs using `X-Aegis-Request-ID`.
6. Save a report and propose patches when useful.

Campaigns now persist status, tool runs, findings, and Markdown report paths in the Django database. Richer endpoint schema extraction remains a planned next step.
