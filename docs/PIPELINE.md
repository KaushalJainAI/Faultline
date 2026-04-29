# Faultline Pipeline Mode

The `PipelineRunner` (`core/pipeline.py`) provides a deterministic, fast, and repeatable baseline for analyzing target projects without engaging the LangGraph LLM agent. 

This is an execution mode you can choose when starting a campaign or using the CLI (`python scripts/faultline_cli.py --mode pipeline`).

## Why Pipeline Mode?

> [!NOTE]
> The `pipeline` mode implements **Steps 1 through 3** of the [Faultline Pipeline Vision](VISION.md) (Syntax, Deterministic Checks, and Dependency Analysis). Steps 4-7 are handled by the `agent` or `hybrid` execution modes.

While the `agent` mode allows an LLM to dynamically explore, read files, generate tests, and execute chaos attacks, the `pipeline` mode is designed for immediate, predictable feedback. It uses purely static and deterministic analysis.

This makes it ideal for:
- CI/CD integration.
- Fast local smoke-testing.
- Getting a baseline of simple errors before spending API credits on deep LLM analysis.

## Pipeline Execution Stages

When a pipeline campaign runs, it executes the following stages in order:

### 1. Deterministic Checks (`skills/deterministic_checker.py`)
This is the core of the pipeline mode. It performs several rigorous, non-AI static analysis passes:
- **Syntax Parsing**: Tries to parse all Python files to find `SyntaxError`s.
- **Import Validation**: Checks for missing imports or hallucinated modules.
- **Linter Integrations**: If available in the environment, runs `ruff check` and `pip check`.
- **Test Collection**: Runs `pytest --collect-only` to ensure tests can be gathered without crashing.
- **Hazard Detection**: Looks for obvious runtime hazards (like definite literal division-by-zero).
- **Dependency Root-Cause Analysis**: Uses the AST map to determine if a broken file is causing a chain reaction of failures in other files that import it.

### 2. Project Mapping (`skills/ast_grapher.py`)
Generates an Abstract Syntax Tree (AST) map of the target, counting the total number of files and dependencies.

### 3. Semantic Indexing (`skills/semantic_indexer.py`)
If Markdown documentation files are found in the target directory, they are indexed into the FAISS semantic knowledge base. (This can be disabled by passing `include_semantic=False`).

## Pipeline Report

After executing these stages, the runner generates a local Markdown report (`reports/pipeline_report.md`). 

This report includes:
- A summary of the target and the total number of findings.
- A breakdown of high/critical severity issues.
- A detailed list of all deterministic findings, complete with file paths, line numbers, categories, and suggested fixes.
- A section identifying the "Root Causes" of dependency failures (e.g., "`utils.py` impacts 5 dependent files").
