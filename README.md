# Faultline: Autonomous Chaos Engineering Platform

Faultline is a generalized AI-driven testing and debugging platform designed to autonomously identify vulnerabilities, crash points, and logic flaws in software projects.

## Project Structure

- `config/`: Django project configuration.
- `campaigns/`: API app for orchestrating chaos campaigns.
- `core/`: LangGraph-powered agentic logic and state management.
- `skills/`: Reusable tool library (Medic, Cartographer, Attacker, etc.).
- `db/`: Local storage for vector embeddings (FAISS) and campaign reports.
- `reports/`: Generated vulnerability and post-mortem reports.

## 📖 Documentation

- [**API Guide**](docs/API.md): Endpoint definitions and request/response schemas.
- [**Skills Library**](docs/SKILLS.md): Deep dive into the Medic, Cartographer, and Siege Engine.
- [**Agent Workflow**](docs/AGENT.md): How the LangGraph orchestration works.

## 🚀 Quick Start

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Control Plane**:
   ```bash
   python manage.py runserver
   ```

3. **Access API**:
   The API is available at `http://localhost:8000/api/v1/campaign/`.

## Current Progress: Phase 1-4 (Complete)

- [x] **Project Initialization**: Basic directory structure and VENV setup.
- [x] **The Medic**: Implemented process monitoring, health checks, and auto-restart capabilities.
- [x] **The Cartographer**: AST-based static analysis to map project structure.
- [x] **Semantic Indexer**: Vector-based documentation-to-code mapping using FAISS.
- [x] **Control Plane**: Initial Django REST framework shell for triggering tasks.
- [x] **Guardrail Validator**: Implementing import/signature checking for AI-generated code.
- [x] **The Forge**: LLM-driven adversarial payload generation prompt setup.
- [x] **The Siege Engine**: Async HTTPX attacker for high-concurrency stress testing.
- [x] **The Coroner**: Watchdog-based log correlator for capturing tracebacks matching attack requests.
- [x] **The Brain**: LangGraph state machine orchestrating the complete attack and reporting flow.

## Next Steps

- Integrate an actual LLM instance (e.g. ChatOpenAI or local Ollama) into `agent.py`.
- Add more sophisticated syntax-tree based adversarial generation models.
- Run an end-to-end chaos campaign against a live test application.