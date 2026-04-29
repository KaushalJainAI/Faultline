# Faultline

Faultline is an early-stage control plane for AI-assisted QA and chaos engineering. It combines static project mapping, documentation indexing, functional test generation, adversarial HTTP payload execution, log correlation, and proposed patch generation.

The current implementation is intentionally developer-facing: you point it at a target project, start or connect to the target application, and let the Aegis-Breaker agent inspect, test, attack, and summarize what it finds.

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

Run the tool smoke test:

```bash
python scripts/test_tools.py
```

Run the agent directly:

```bash
python scripts/run_campaign.py --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --log-file C:/path/to/project/server.log
```

Run the newer CLI pipeline:

```bash
python scripts/faultline_cli.py --mode pipeline --target-dir C:/path/to/project
python scripts/faultline_cli.py --mode agent --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --log-file C:/path/to/project/server.log
python scripts/faultline_cli.py --mode hybrid --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --log-file C:/path/to/project/server.log
```

Modes:

- `pipeline`: deterministic checks first, no target server required.
- `agent`: model-led investigation with tools for listing and reading project files.
- `hybrid`: deterministic baseline first, then agent-led API/chaos investigation.

## Documentation

- [Architecture Guide](docs/ARCHITECTURE.md) - Deep dive into system design and data flow.
- [Pipeline Vision](docs/VISION.md) - The 7-step architectural roadmap for Faultline.
- [Tutorial](docs/TUTORIAL.md) - Step-by-step guide to running your first campaign.
- [Vault Authentication](docs/VAULT.md) - How to configure and use the dynamic authentication system.
- [Pipeline Mode](docs/PIPELINE.md) - Using the deterministic, non-AI testing pipeline.
- [LLM Providers](docs/PROVIDERS.md) - Configuring API and local CLI adapters.
- [MCP Integration](docs/MCP.md) - Using Faultline tools in IDEs like Cursor.
- [Skills Library](docs/SKILLS.md) - Detailed catalog of testing and analysis tools.
- [API Guide](docs/API.md) - REST API documentation for the control plane.
- [Agent Workflow](docs/AGENT.md) - Explanation of the LangGraph execution loop.
- [Contributing](docs/CONTRIBUTING.md) - How to extend Faultline with new skills.

## Current State

Implemented:

- Django REST control plane.
- Database-backed campaign, finding, and tool-run persistence.
- Dynamic Vault authentication system for static tokens and login endpoints.
- AST-based Python project mapper.
- Pipeline-first deterministic scanner for syntax, imports, dependency conflicts, pytest collection, Ruff findings, and dependency failure propagation.
- Agent-first file listing and bounded file-reading tools.
- Basic Django/DRF route, view, and serializer hints.
- LangChain tools and MCP wrappers.
- Async HTTP attack engine with request ID tracing.
- Watchdog-based log correlation.
- Pytest-based functional test runner.
- Safe patch proposal writer.
- FAISS-backed semantic documentation indexer.
- Markdown reports under `reports/campaign_<id>.md`.

Known next steps:

- Add first-class target process lifecycle controls to direct CLI runs.
- Add richer Django/DRF endpoint schema extraction from serializers and routers.
- Add integration tests against a disposable demo target application.
