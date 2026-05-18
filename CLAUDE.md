# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run Django tests
python manage.py test

# Syntax-check all project Python files (no LLM)
python -m compileall campaigns core skills scripts mcp_server.py manage.py

# Apply migrations (SQLite, local only)
python manage.py migrate

# Run the interactive CLI (primary entry point)
python faultline.py
python faultline.py --target-dir /path/to/project --target-url http://localhost:8000 --mode hybrid

# Run against a target without a live server (static checks only)
python faultline.py --target-dir /path/to/project --mode pipeline

# Tool smoke test (verifies each LangChain tool in isolation)
python scripts/test_tools.py

# Start the Django REST control plane (headless / CI use)
python manage.py runserver
```

## Architecture

Faultline is an AI-assisted QA and chaos engineering platform. It follows a **7-step vision**: static syntax checks → deterministic analysis → AST dependency analysis → agentic API testing → semantic intent verification → E2E/load testing → security chaos engineering. Steps 1–4 and partial 7 are implemented.

### Three operating modes

| Mode | What runs |
|------|-----------|
| `pipeline` | Deterministic-only (Steps 1–3). No LLM. Fast CI baseline. |
| `agent` | LangGraph agent directly, no deterministic pre-flight. |
| `hybrid` | Pipeline first, then agent. Default for interactive use. |

### Key entry points

- **`faultline.py`** — Interactive CLI. Streams agent reasoning, shows HITL prompts for credentials/destructive actions, creates a timestamped run folder under `reports/`.
- **`core/pipeline.py` → `PipelineRunner`** — Deterministic runner. Orchestrates Steps 1–3 and writes `pipeline_report.md`. No LLM involved.
- **`core/agent.py` → `AegisAgent`** — LangGraph agent. Manages the `agent → tools → agent` loop, budget enforcement, context windowing, and phase tracking.
- **`campaigns/`** — Django REST API (`POST /api/v1/campaign/start/`) for headless/CI triggering. Persists campaigns, findings, and tool runs in SQLite.
- **`mcp_server.py`** — Exposes Faultline tools as MCP tools for use in Cursor/Claude Desktop.

### Core module responsibilities

| Module | Responsibility |
|--------|---------------|
| `core/agent.py` | LangGraph graph definition, `AegisAgent`, `BudgetConfig`, `ParallelToolNode`, `LLMRateLimiter`, `CampaignState` |
| `core/tools.py` | All `@tool`-decorated LangChain tool definitions. Imports skills and wraps them for the agent. |
| `core/pipeline.py` | `PipelineRunner` — deterministic Steps 1–3 without LLM. |
| `core/content_manager.py` | Three-tier context windowing: Tier 1 (latest cycle, full), Tier 2 (last N cycles, large results summarised), Tier 3 (historical bullet summary). Keeps state["messages"] within the model's token limit. |
| `core/progress_tracker.py` | `ProgressTracker` dataclass. Injected as a `SystemMessage` each turn so the agent knows its phase, token budget, checklist, and stuck/momentum status. |
| `core/model_registry.py` | `_CATALOG` of all supported models with context window sizes. `get_model_info()` used for dynamic context limit calculation. |
| `core/prompts.py` | `SYSTEM_PROMPT` and `VISION_REMINDER` (re-injected every 2+ turns to re-anchor the agent). |
| `core/input_handler.py` | Async keyboard listener for HITL pause/resume/abort during runs. |

### Skills (standalone Python, wrapped as tools in `core/tools.py`)

| Skill | Role |
|-------|------|
| `skills/deterministic_checker.py` | Syntax parsing, import checks, ruff/pip/pytest integration, dependency root-cause propagation |
| `skills/ast_grapher.py` | AST-based project mapper; extracts classes, functions, Django routes, DRF serializers |
| `skills/qa_engineer.py` | Writes and executes temporary pytest scripts; happy/sad path; persists results to `generated_tests.json` |
| `skills/attacker.py` | Async HTTP chaos engine (`SiegeEngine`); injects `X-Aegis-Request-ID` for log correlation |
| `skills/log_correlator.py` | Watchdog-based log monitor; correlates crashes to request IDs |
| `skills/semantic_indexer.py` | FAISS-backed doc indexer (optional heavy deps: faiss-cpu, torch) |
| `skills/container_grapher.py` | Modularity/independence scoring for project components |
| `skills/deprecation_guard.py` | Detects deprecated API usage patterns |
| `skills/target_discovery.py` | Endpoint discovery and `api_test_data.json` management |
| `skills/file_reader.py` | Sandboxed file reader; blocks traversal outside target dir |

### Context window management

`core/content_manager.py::build_tiered_context()` is called before every LLM invocation. It estimates tokens using `CHARS_PER_TOKEN=3` (conservative for code/JSON). The `context_limit` in `core/agent.py` is calculated as `min(context_window * context_ratio, context_window - max_output_tokens)` to always reserve room for output. When over budget: Tier 3 is dropped first, then Tier 2 cycles are pruned oldest-first.

### Budget and phase system

`BudgetConfig` (in `core/agent.py`) controls LLM call caps, tool call caps, output token limits, and reasoning profiles (`fast`/`normal`/`deep`). `BudgetConfig.__post_init__` is **provider-aware**: when the resolved provider is Codex, `max_llm_calls` defaults to `40` instead of `120` (an explicit `FAULTLINE_MAX_LLM_CALLS`/`FAULTLINE_MAX_TURNS` always wins). `ProgressTracker` enforces per-phase caps (`PHASE_CAPS`) across four phases: `discovery → test → chaos → report`. Phase advancement is inferred from tool names via `_PHASE_SIGNALS`.

### Parallel tool execution

`ParallelToolNode` (in `core/agent.py`) runs all tool calls emitted in a single LLM response concurrently. One LLM turn that emits N independent tool calls therefore executes N tasks in parallel for the cost of **one** LLM call — the system prompt mandates this batching. Concurrency is bounded by a semaphore (`FAULTLINE_MAX_PARALLEL_TOOLS`, default 8). `asyncio.gather` uses `return_exceptions=True` so a single failing tool does not cancel its independent siblings; only the failed call surfaces an error `ToolMessage`. Oversized parallel results (>50k est. tokens) are auto-offloaded to `content_store/` via the Governor, using the `run_folder` threaded from `CampaignState`.

### Run folder layout

Every run creates `reports/<project>_<YYYYMMDD>_<HHMMSS>/` containing:
- `pipeline_report.md` — deterministic findings + production-readiness score (0–100, no LLM)
- `live_report.md` / `agent_report.md` — agent-authored vulnerability report
- `campaign_agent.log` — full agent reasoning trace
- `llm_calls.log` — per-LLM-call log (status, elapsed, token estimate)
- `findings.jsonl` — structured finding records
- `api_test_data.json` — discovered endpoints and test coverage status
- `generated_tests.json` — ledger of all executed test scripts
- `memory.json` / `memory.md` — session knowledge index (ref_ids for stored tool outputs)
- `content_store/` — full tool outputs stored when they exceed 5k token threshold
- `testcases/` — deployed boilerplate test scripts (agent edits these in place)

### Authentication (Vault)

`vault/` is a Django app. `AuthFlow` models define either `static` token injection or `login` endpoint flows (with dot-notation `token_extraction_path`). Session headers acquired by the Vault are injected into `CampaignState["session_headers"]` and passed to all tool calls.

## Adding a new skill

1. Create `skills/my_skill.py` with a plain Python class.
2. Add a `@tool`-decorated function in `core/tools.py` that instantiates the class and calls it.
3. Append the new tool to `FAULTLINE_TOOLS` in `core/tools.py`.
4. (Optional) Add an MCP wrapper in `mcp_server.py`.

Tool docstrings are critical — the LLM uses them to decide when to call a tool.

## Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `FAULTLINE_PROVIDER` | `openrouter` | LLM backend: `openrouter`, `openai`, `anthropic`, `google`, `claude_cli`, `gemini_cli`, `codex_cli` |
| `OPENROUTER_API_KEY` | — | Required for default provider |
| `FAULTLINE_MAX_LLM_CALLS` | `120` (`40` for codex) | Hard cap on agent LLM turns. Provider-aware: auto-defaults to `40` when provider resolves to Codex. Explicit value overrides all defaults. |
| `FAULTLINE_CODEX_MAX_LLM_CALLS` | `40` | Codex-only LLM-call default (used only when `FAULTLINE_MAX_LLM_CALLS`/`FAULTLINE_MAX_TURNS` are unset and provider is codex) |
| `FAULTLINE_MAX_PARALLEL_TOOLS` | `8` | Max independent tool calls run concurrently within a single LLM turn (`ParallelToolNode` semaphore) |
| `FAULTLINE_CODEX_SANDBOX` | `read-only` | Codex CLI sandbox mode |
| `FAULTLINE_MAX_OUTPUT_TOKENS` | profile default | Output tokens per LLM call |
| `FAULTLINE_CONTEXT_RATIO` | `0.8` | Fraction of model context window used for input |
| `FAULTLINE_MAX_RPM` | `36` | Requests per minute rate limit |
| `FAULTLINE_REASONING_LEVEL` | `normal` | `fast` / `normal` / `deep` — controls output token budget and system prompt |
| `FAULTLINE_TARGET_VENV` | — | Path to target project's venv (used by `DependencyChecker`) |
| `FAULTLINE_SUMMARY_THRESHOLD` | `5000` | Token threshold above which tool results are stored to disk instead of inlined |
| `FAULTLINE_TIER2_CYCLES` | `5` | Number of recent cycles kept at full fidelity in context |
