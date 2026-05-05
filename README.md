# Faultline

**Date**: 2026-05-01
**Description**: The primary entry point and overview for the Faultline AI-assisted QA and chaos engineering platform.

Faultline is an early-stage control plane for AI-assisted QA and chaos engineering. It combines static project mapping, documentation indexing, functional test generation, adversarial HTTP payload execution, log correlation, and proposed patch generation.

The primary way to use Faultline is the **interactive CLI** — `python faultline.py` — which streams agent reasoning, tool calls, and findings to your terminal in real time, and pauses to ask for credentials or permission when the agent needs them. A Django REST control plane is also available for headless / CI use.

## Interactive CLI (recommended)

```bash
# Run with prompts for missing args
python faultline.py

# Or specify everything
python faultline.py --target-dir /path/to/project --target-url http://localhost:8000 --mode hybrid
```

You see the agent thinking, every tool call and result, every file it writes, and a yellow panel any time it needs you to approve a destructive action or supply a credential. See [docs/CLI.md](docs/CLI.md) for the full reference.

## Project Layout

- `config/`: Django project configuration.
- `campaigns/`: REST API for starting campaigns and generating project maps.
- `vault/`: Authentication management system for acquiring and injecting session credentials.
- `core/`: LangGraph agent orchestration, prompts, and LangChain tool bindings.
- `skills/`: Reusable capabilities for mapping, testing, attacking, log correlation, patch proposals, and semantic indexing.
- `scripts/`: Local smoke-test and campaign runner scripts.
- `docs/`: API, agent, and skills documentation.
- `reports/`: Generated vulnerability and campaign reports.

## Quick Start

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Run tests:

   ```bash
   python manage.py test
   python -m compileall campaigns core skills scripts mcp_server.py manage.py
   ```

3. Apply database migrations:

   ```bash
   python manage.py migrate
   ```

4. Configure an LLM provider.

   OpenRouter is the default API route:

   ```bash
   set OPENROUTER_API_KEY=your_key_here
   ```

   To use subscription-backed CLIs instead, install and log in to the CLI, then set one of:

   ```bash
   set FAULTLINE_PROVIDER=claude_cli
   set FAULTLINE_PROVIDER=gemini_cli
   set FAULTLINE_PROVIDER=codex_cli
   ```

5. Start the control plane:

   ```bash
   python manage.py runserver
   ```

6. Start a campaign:

   ```bash
   curl -X POST http://localhost:8000/api/v1/campaign/start/ ^
     -H "Content-Type: application/json" ^
     -d "{\"target_path\":\"C:/path/to/project\",\"target_url\":\"http://127.0.0.1:9000\",\"start_command\":\"python manage.py runserver 9000\",\"log_file\":\"server.log\"}"
   ```

## Agent Configuration

The default LangGraph agent uses OpenRouter through `langchain-openai`:

```bash
set OPENROUTER_API_KEY=your_key_here
```

You can also choose direct API providers:

```bash
set FAULTLINE_PROVIDER=openai
set OPENAI_API_KEY=your_key_here

set FAULTLINE_PROVIDER=anthropic
set ANTHROPIC_API_KEY=your_key_here

set FAULTLINE_PROVIDER=google
set GOOGLE_API_KEY=your_key_here
```

For subsidized subscription usage, set `FAULTLINE_PROVIDER` to `claude_cli`, `gemini_cli`, or `codex_cli`. Those modes delegate the campaign prompt to the authenticated local CLI instead of requiring OpenRouter API spend.

The CLI commands are run in non-interactive prompt mode:

```bash
claude -p "<prompt>"
gemini -p "<prompt>" --skip-trust
codex exec "<prompt>" --cd "<target_dir>" --sandbox read-only
```

You can add provider-specific flags without changing code:

```bash
set FAULTLINE_CLAUDE_BINARY=C:\path\to\claude.cmd
set FAULTLINE_CLAUDE_CLI_ARGS=--permission-mode plan
set FAULTLINE_GEMINI_BINARY=gemini
set FAULTLINE_GEMINI_CLI_ARGS=--model gemini-2.5-pro
set FAULTLINE_CODEX_BINARY=codex
set FAULTLINE_CODEX_SANDBOX=workspace-write
set FAULTLINE_CODEX_CLI_ARGS=--model gpt-5.2
```

Without a configured API key or authenticated CLI, lower-level tools and project mapping still work, but `POST /api/v1/campaign/start/` returns a configuration error.

## Useful Commands

Run the interactive CLI (preferred):

```bash
python faultline.py                                                              # interactive prompts
python faultline.py --target-dir C:/path/to/project --mode pipeline              # static checks only
python faultline.py --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --mode hybrid
python faultline.py --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --mode hybrid --no-hitl   # unattended
```

Run the tool smoke test:

```bash
python scripts/test_tools.py
```

Legacy non-interactive scripts (still supported):

```bash
python scripts/run_campaign.py --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --log-file C:/path/to/project/server.log
python scripts/faultline_cli.py --mode hybrid --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --log-file C:/path/to/project/server.log
```

Modes:

- `pipeline`: deterministic checks first, no target server required.
- `agent`: model-led investigation with tools for listing and reading project files.
- `hybrid`: deterministic baseline first, then agent-led API/chaos investigation.

## Documentation

Use this README as the binding index for the documentation set:

| Document | Use it for |
|----------|------------|
| [Interactive CLI](docs/CLI.md) | Running `faultline.py`, reading terminal progress, HITL prompts, output folders. |
| [Operator Commands](docs/OPERATOR_COMMANDS.md) | Steering Room commands, especially `/status` and `/wrapup`. |
| [Context Management](docs/CONTEXT_MANAGEMENT.md) | Request context vs stored history, compaction, memory refs, budget endgame. |
| [Agent Workflow](docs/AGENT.md) | LangGraph loop, tool orchestration, budget/wrap-up behavior. |
| [Architecture Guide](docs/ARCHITECTURE.md) | System design and data flow. |
| [Pipeline Vision](docs/VISION.md) | The 7-step architectural roadmap for Faultline. |
| [Tutorial](docs/TUTORIAL.md) | Step-by-step guide to running your first campaign. |
| [Vault Authentication](docs/VAULT.md) | Configuring and using dynamic authentication. |
| [Pipeline Mode](docs/PIPELINE.md) | Deterministic, non-AI testing pipeline. |
| [LLM Providers](docs/PROVIDERS.md) | API and local CLI adapter configuration. |
| [MCP Integration](docs/MCP.md) | Using Faultline tools in IDEs like Cursor. |
| [Skills Library](docs/SKILLS.md) | Catalog of testing and analysis tools. |
| [API Guide](docs/API.md) | REST API documentation for the control plane. |
| [Testing Guide](docs/TESTING_GUIDE.md) | Agentic boilerplate testing and debugging. |
| [Boilerplates](docs/BOILERPLATES.md) | Test template inventory and copy/edit workflow. |
| [Dependencies](docs/DEPENDENCIES.md) | Optional test dependencies and setup checks. |
| [Dry Run Notes](docs/DRY_RUN.md) | Earlier dry-run architecture notes. |
| [Contributing](docs/CONTRIBUTING.md) | Extending Faultline with new skills or tools. |

## Current State

Implemented:

- **Operator Steering Room commands** - press `Esc` and use `/status` for a factual progress snapshot or `/wrapup` to force final report + walkthrough within a few calls.
- **Lossless context storage with compact request windows** - full history is retained in run artifacts while model calls receive compact working context and queryable refs.
- **Budget-aware endgame** - the agent reserves final calls for report synthesis and a no-tools operator walkthrough; Faultline writes a fallback `vulnerability_report.md` if the LLM budget expires first.
- Interactive CLI (`faultline.py`) with rich live streaming, HITL credential and permission prompts, and a `request_user_input` tool the agent can call mid-campaign.
- **Per-run isolated output folder** — every run creates `reports/<project>_<YYYYMMDD>_<HHMMSS>/` so runs are auditable and comparable over time.
- **Production-readiness score** — deterministic 0–100 score (no LLM text) shown at the top of every pipeline report with severity table and next-steps checklist.
- **Agent turn counter, animations, and ETA** — terminal shows `[ Agent turn N ]` with real-time status spinners and ETA estimates so operators can track progress during long-running LLM cycles.
- **Automated "Edit-Run" Workflow** — boilerplate scripts are automatically deployed into the project at startup. The agent directly edits these pre-existing templates rather than generating or copying them manually, significantly reducing token costs and hallucination.
- **Interactive 3D Dependency Graph** — generated report including a self-contained Dash app for exploring AST relationships, root-cause nodes, and failure chains in 3D.
- Django REST control plane.
- Database-backed campaign, finding, and tool-run persistence.
- Dynamic Vault authentication system for static tokens and login endpoints.
- AST-based Python project mapper.
- Pipeline-first deterministic scanner for syntax, imports, dependency conflicts, pytest collection, Ruff findings, and dependency failure propagation.
- Agent-first file listing and bounded file-reading tools.
- Multi-provider LLM support: OpenRouter, OpenAI, Anthropic, Google, and one-shot CLI delegates (Claude Code, Gemini CLI, Codex).
- LangChain tools and MCP wrappers.
- Async HTTP attack engine with request ID tracing.
- Watchdog-based log correlation.
- Pytest-based functional test runner.
- Safe patch proposal writer.
- FAISS-backed semantic documentation indexer.

Known next steps:

- Add first-class target process lifecycle controls to direct CLI runs.
- Add richer Django/DRF endpoint schema extraction from serializers and routers.
- Extend `SemanticIndexer` to embed function docstrings for full Step 5 contract verification.
- Add integration tests against a disposable demo target application.
