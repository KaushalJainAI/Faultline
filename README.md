# Faultline

Faultline is an early-stage control plane for AI-assisted QA and chaos engineering. It combines static project mapping, documentation indexing, functional test generation, adversarial HTTP payload execution, log correlation, and proposed patch generation.

The current implementation is intentionally developer-facing: you point it at a target project, start or connect to the target application, and let the Aegis-Breaker agent inspect, test, attack, and summarize what it finds.

## Project Layout

- `config/`: Django project configuration.
- `campaigns/`: REST API for starting campaigns and generating project maps.
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

4. Configure the LLM key:

   ```bash
   set OPENROUTER_API_KEY=your_key_here
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

The LangGraph agent uses OpenRouter through `langchain-openai`. `OPENROUTER_API_KEY` is required before starting a campaign:

```bash
set OPENROUTER_API_KEY=your_key_here
```

Without a configured key, lower-level tools and project mapping still work, but `POST /api/v1/campaign/start/` returns a configuration error.

## Useful Commands

Run the tool smoke test:

```bash
python scripts/test_tools.py
```

Run the agent directly:

```bash
python scripts/run_campaign.py --target-dir C:/path/to/project --target-url http://127.0.0.1:9000 --log-file C:/path/to/project/server.log
```

## Documentation

- [API Guide](docs/API.md)
- [Skills Library](docs/SKILLS.md)
- [Agent Workflow](docs/AGENT.md)

## Current State

Implemented:

- Django REST control plane.
- Database-backed campaign, finding, and tool-run persistence.
- AST-based Python project mapper.
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
