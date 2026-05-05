# Aegis-Breaker Agent

**Date**: 2026-05-05
**Description**: Detailed explanation of the Aegis-Breaker agent architecture, runtime flow, and tool orchestration using LangGraph.

Aegis-Breaker is the LangGraph orchestration layer for Faultline. Its job is to combine normal QA verification with adversarial chaos testing.

## Runtime Flow

The current graph is intentionally simple:

1. **Authentication**: If an `AuthFlow` is configured, Faultline pre-flights the login endpoint (or reads static tokens) via the Vault and acquires session credentials.
2. `agent`: Calls the configured chat model with campaign context and the Faultline tool list.
3. `tools`: Executes any requested tool calls.
4. Loop back to `agent` until the model returns a message without tool calls.

The model receives:

- Target directory, base URL, log file, run folder, and testcases folder.
- Session credentials (headers/cookies) injected by the Vault.
- The system prompt from `core/intelligence/prompts.py`.
- A compact progress/status block.
- The tail of `live_report.md` as the current plan.
- `memory.md` and `history_index.md` reference ledgers.
- Recent operator steering and the latest AI/tool cycle.

It does not receive the full raw campaign history every turn. Older messages and large tool outputs are stored on disk and retrieved by reference when needed.

## Available Tools

- `list_project_files`: Lists project files for agent-first exploration.
- `read_project_file`: Reads specific segments of a target file.
- `run_deterministic_checks`: Runs syntax, imports, and pipeline linters.
- `analyze_project_structure`: AST map of Python files, classes, functions, and imports.
- `index_project_documentation`: Indexes Markdown documentation into FAISS.
- `query_knowledge_base`: Searches indexed documentation.
- `validate_python_code`: Checks generated Python snippets for syntax and missing imports.
- `run_functional_test`: Writes and runs a temporary pytest file.
- `execute_chaos_campaign`: Sends adversarial HTTP payloads and correlates log crashes.
- `propose_code_patch`: Writes proposed fixes into `.aegis_patches`.
- `save_vulnerability_report`: Saves Markdown reports into `reports/`.
- `execute_claude_code_task`: Delegates complex refactoring or multi-file architectural changes to Claude Code.
- `execute_gemini_cli_task`: Delegates deep reasoning and exploration to the Gemini CLI.
- `execute_codex_cli_task`: Delegates restricted or coding-focused sandbox tasks to the Codex CLI.
- `generate_dependency_graph`: Generates an interactive 3D dependency visualization (Dash app) showing imports, calls, and inheritance.
- `calculate_project_quality`: Uses the Visualizer to calculate endpoint risk and global quality scores.
- `generate_campaign_visuals`: Uses the Visualizer to create Plotly failure-rate and vulnerability maps.

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

1. Authenticate using the Vault (if configured).
2. Discover structure and documentation intent.
3. Automatically deploy and verify boilerplates using the "Edit-Run" methodology.
4. Delegate deep analysis to CLI providers if needed.
5. Generate adversarial payloads from discovered endpoint context.
6. Execute attacks against the target URL.
7. Correlate crashes from logs using `X-Aegis-Request-ID`.
8. Calculate quality scores and generate 3D/2D visual reports.
9. Save a report and propose patches when useful.

Campaigns now persist status, tool runs, findings, and Markdown report paths in the Django database. Richer endpoint schema extraction remains a planned next step.

## 💾 Context Window Management

To maintain efficiency during long-running campaigns with large codebases, Faultline implements a **Queryable Reference** system:
- **Automatic Summarization**: Tool outputs exceeding 5,000 tokens are automatically summarized inline in the agent's context.
- **Persistent Storage**: The full, original outputs are saved to disk in the run-specific folder with a `[REF:<id>]` marker.
- **On-Demand Retrieval**: The agent can use the `retrieve_stored_content` tool to fetch the original, unsummarized data using the reference ID at any time.

This hybrid approach ensures the agent remains responsive and cost-effective without losing access to granular technical data when it's needed for final reporting or patch generation.

## Current Context And Budget Behavior

Faultline now stores full history while sending only compact working context to the model:

- Full messages are retained in `checkpoint.json`, session JSONL, `transcript.txt`, and `history_vault/`.
- Exact archived messages are indexed in `history_index.md` and retrieved with `retrieve_history_message`.
- Large tool/file outputs are stored in `content_store/`, indexed in `memory.md`, and retrieved with `retrieve_stored_content`.
- Each model call receives a compact working set from `core.intelligence.content_manager.build_tiered_context`, not the full raw transcript.
- The terminal progress panel labels this as `Request` for compacted prompt size and `History` for raw stored campaign history.

Faultline reserves the end of the LLM call budget for closure:

- `FAULTLINE_REPORTING_RESERVE_CALLS` controls when exploratory tools are pruned.
- `FAULTLINE_FINAL_WALKTHROUGH_CALLS` controls the final no-tools walkthrough window.
- `/wrapup` in the Steering Room forces final synthesis in a few calls.
- If the LLM budget ends before `vulnerability_report.md` is saved, Faultline writes a factual fallback report from known artifacts.

See [Context Management](CONTEXT_MANAGEMENT.md) and [Operator Commands](OPERATOR_COMMANDS.md).

## Internal Refactor Notes (May 2026)

The codebase is undergoing a readability-first refactor with a strict "no behavior change" goal.

- `core/agent.py` has started moving large inline setup blocks into focused helper methods (live report setup, boilerplate seeding, resume-state reconstruction, transcript writing, progress tracker initialization).
- Token estimation now uses a shared utility module: `core/token_utils.py`.
- Module-specific estimation behavior is preserved:
  - `core/intelligence/content_manager.py` keeps its conservative context-protection ratio.
  - `core/intelligence/progress_tracker.py` keeps its UI/budget-awareness ratio and minimum non-empty token floor.

This preserves existing campaign behavior while reducing duplicate logic and making future module splitting safer.

## Core Folder Grouping

To make reviews easier, `core/` is now grouped by responsibility with compatibility shims at legacy paths:

- `core/orchestration/`: runtime orchestration and execution flow (`pipeline`, `checkpoint`, `context`, `input_handler`, `live_report`, `session_store`, `run_context`, `cli_ui`)
- `core/providers/`: provider and model integration (`cli_provider`, `provider_config`, `model_registry`, `credential_store`)
- `core/intelligence/`: prompts, context intelligence, and progress logic (`prompts`, `content_manager`, `progress_tracker`, `index_state`, `api_knowledge`)
- `core/harness/components/`: harness architecture primitives (loop/context/registry/subagents/skills/persistence/prompt/hooks/permissions)

Use the grouped imports directly, such as `core.intelligence.content_manager` and `core.orchestration.checkpoint`.

## Harness Architecture Map (9 Components)

Faultline now includes a dedicated harness package at `core/harness/` to mirror standard coding-agent design patterns:

1. While Loop (Iteration Engine): `core/harness/iteration_engine.py`
2. Context Management & Compaction: `core/harness/context_compaction.py`
3. Tools & Skills Registry: `core/harness/registry.py`
4. Sub-Agent Management: `core/harness/sub_agents.py`
5. Built-in Skills Catalog: `core/harness/built_in_skills.py`
6. Session Persistence / Memory: `core/harness/persistence.py`
7. System Prompt Assembly: `core/harness/prompt_assembly.py`
8. Lifecycle Hooks (Pre/Post Tool): `core/harness/hooks.py`
9. Permissions & Safety Layer: `core/harness/permissions.py`

Composition root: `core/harness/runtime.py` (`HarnessRuntime`), which is now instantiated by `AegisAgent`.
