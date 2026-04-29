# Faultline Skills Library

Skills are modular Python components that provide the core functionality of the Faultline platform. They are wrapped as LangChain tools for use by the agent and as MCP tools for use by external IDEs.

## Execution Modes

-   **Pipeline-first**: Runs deterministic checks before model-led analysis. This is the dependable CLI debugging mode.
-   **Agent-first**: Lets the LLM decide the investigation path, with explicit tools for listing and reading files.
-   **Hybrid**: Runs the deterministic baseline, then hands the target to the agent for generated tests, chaos payloads, and reporting.

## 0. File Reader (`skills/file_reader.py`)
**Project-local Source Inspection**
-   **Capabilities**:
    -   Lists target files with glob support.
    -   Reads bounded line ranges from project-local files.
    -   Blocks traversal outside the target and skips heavy/sensitive directories such as `.git`, `venv`, caches, and `node_modules`.

## 0. Deterministic Checker (`skills/deterministic_checker.py`)
**Pipeline-first Static and Runtime Baseline**
-   **Capabilities**:
    -   Parses Python files for syntax errors.
    -   Detects missing imports and definite literal division-by-zero hazards.
    -   Runs `ruff check`, `pip check`, and `pytest --collect-only` when available.
    -   Uses the AST dependency graph to estimate which failed files cause downstream failures.

## 1. Medic (`skills/medic.py`)
**Process Management & Lifecycle**
-   **Capabilities**:
    -   Starts the target application using a user-provided shell command.
    -   Monitors process health via a specific health-check URL.
    -   Recursively terminates process trees (including child processes) to ensure a clean state between campaigns.
    -   `resurrect`: Attempts to restart the target if it crashes during a chaos assault.

## 2. Cartographer (`skills/ast_grapher.py`)
**AST-based Project Mapping**
-   **Capabilities**:
    -   Recursively parses Python files into Abstract Syntax Trees (AST).
    -   Extracts classes, methods, functions, and imports.
    -   **Django/DRF Hints**: Automatically detects URL routes, viewsets, and serializers to map the API attack surface.
    -   **Safe Scanning**: Ignores `venv`, `__pycache__`, and `.git` directories.

## 3. Siege Engine (`skills/attacker.py`)
**Adversarial Chaos Testing**
-   **Capabilities**:
    -   Executes high-concurrency HTTP assaults using `httpx`.
    -   Supports `GET`, `POST`, `PUT`, and `DELETE` methods.
    -   **Session Injection**: Automatically injects session headers or cookies acquired by the Vault Authenticator.
    -   **Tracing**: Injects `X-Aegis-Request-ID` headers to allow the Log Correlator to map crashes back to specific payloads.
    -   **Dynamic Payloads**: Supports JSON and form-data injection for fuzzing.

## 4. Coroner (`skills/log_correlator.py`)
**Log Analysis & Fault Attribution**
-   **Capabilities**:
    -   Uses the `watchdog` library to monitor target application log files in real-time.
    -   Detects Python tracebacks, database integrity errors, and generic 500-level logs.
    -   **Correlation**: If the target application logs the `X-Aegis-Request-ID` header, this skill matches the error exactly to the attack payload that caused it.

## 5. QA Engineer (`skills/qa_engineer.py`)
**Functional Verification**
-   **Capabilities**:
    -   **Dynamic Test Runner**: Generates and executes temporary Pytest scripts within the target project's environment.
    -   Verifies that core functionality (e.g., login, CRUD) still works before and after chaos campaigns.
    -   Automatically cleans up generated test artifacts after execution.

## 6. Semantic Indexer (`skills/semantic_indexer.py`)
**Documentation & Intent Search**
-   **Capabilities**:
    -   Indexes Markdown documentation files using Qwen embeddings.
    -   Enables semantic search for the agent to understand developer intent and business logic.
    -   Uses **FAISS** for high-performance vector retrieval.

## 7. Visualizer (`skills/visualizer.py`)
**Reporting & Analytics**
-   **Capabilities**:
    -   **Dependency Graphs**: Generates Mermaid.js diagrams showing file-level dependencies.
    -   **Vulnerability Maps**: Creates Plotly charts showing finding density across the codebase.
    -   **Quality Scoring**: Calculates a global "Quality Score" (0-100) based on finding severity and functional test pass rates.
    -   **Intent Correlation**: Visualizes documentation coverage vs. implementation reality.

## 8. Guardrail Validator (`skills/guardrails.py`)
**Safety & Sandboxing**
-   **Capabilities**:
    -   Analyzes LLM-generated code snippets for security risks before execution.
    -   Blocks access to sensitive files like `.env`, `.git`, or SSH keys.
    -   Verifies that imported modules are safe and present in the environment.

## 9. CLI Provider Delegation (`core/tools.py`)
**Local Agent Task Offloading**
-   **Capabilities**:
    -   **Claude Code**: `execute_claude_code_task` delegates multi-file refactoring and architectural changes to the `claude` CLI.
    -   **Gemini CLI**: `execute_gemini_cli_task` delegates deep reasoning or analysis to the `gemini` CLI.
    -   **Codex CLI**: `execute_codex_cli_task` delegates code generation or sandbox-restricted tasks to the `codex` CLI.
    -   Allows the main LangGraph agent to leverage user-authenticated local CLIs without consuming OpenRouter API credits.
