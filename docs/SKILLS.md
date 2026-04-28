# Faultline Skills

Skills are standalone Python modules used by the agent and exposed through LangChain tools.

## Medic (`skills/medic.py`)

Manages the target application process.

- Starts the target with `start_command`.
- Optionally checks a `health_url`.
- Kills the target process and child processes when a campaign ends.
- Can restart the target through `resurrect`.

## Cartographer (`skills/ast_grapher.py`)

Builds a structural map of Python projects without importing target code.

- Records top-level classes, methods, functions, and imports.
- Skips virtual environments, caches, Git metadata, and generated patch folders.
- Resets its graph on every `analyze_project` call so repeated calls are stable.
- Adds lightweight Django/DRF hints for simple `path(...)`, `router.register(...)`, view classes, and serializer classes.

## Siege Engine (`skills/attacker.py`)

Executes adversarial HTTP requests.

- Uses `httpx.AsyncClient` and `asyncio.gather` for concurrent requests.
- Supports `GET`, `POST`, `PUT`, and `DELETE`.
- Injects `X-Aegis-Request-ID` into every request.
- Skips malformed attack definitions instead of failing the whole run.

## Coroner (`skills/log_correlator.py`)

Watches a log file during attacks and records correlated errors.

- Uses `watchdog` for file modification events.
- Looks for tracebacks, exceptions, and errors.
- Associates log lines with `X-Aegis-Request-ID` when the target logs that header.

## Guardrail Validator (`skills/guardrails.py`)

Checks generated Python snippets before they are used.

- Parses snippets with `ast`.
- Verifies imported modules exist in the environment or target project.
- Optionally runs `ruff` against a file when available.
- Blocks obviously sensitive paths such as `.env`, `.git`, private keys, databases, and virtual environments.

## QA Engineer (`skills/qa_engineer.py`)

Runs generated functional tests and saves proposed patches.

- Writes temporary pytest files inside the target project.
- Deletes generated test files after execution.
- Writes proposed code fixes under `.aegis_patches`.
- Rejects patch paths that escape the target directory.

## Semantic Indexer (`skills/semantic_indexer.py`)

Indexes Markdown documentation for semantic retrieval.

- Uses Qwen embeddings.
- Stores vectors in FAISS HNSW.
- Stores document metadata beside the index.

This component can download and load a large embedding model on first use.
