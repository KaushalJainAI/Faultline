# Faultline Interactive CLI

Faultline ships with an interactive command-line agent — `faultline.py` — that runs the full testing pipeline directly in your terminal with live agent reasoning, real-time tool call visibility, and human-in-the-loop (HITL) prompts when the agent needs credentials or permission.

It is the recommended way to use Faultline locally. The Django REST control plane remains available for headless / CI use.

---

## Quick Start

```bash
# Install (once)
pip install -r requirements.txt

# Configure an LLM provider (one of):
set OPENROUTER_API_KEY=your_key
# or
set FAULTLINE_PROVIDER=claude_cli   # uses your Claude Code subscription
set FAULTLINE_PROVIDER=gemini_cli   # uses Gemini CLI subscription
set FAULTLINE_PROVIDER=codex_cli    # uses Codex CLI

# Run interactively (will prompt for missing args)
python faultline.py

# Or specify everything up front
python faultline.py --target-dir /path/to/project --target-url http://localhost:8000 --mode hybrid
```

---

## Modes

| Mode       | What it runs                                                    | Server required? |
|------------|------------------------------------------------------------------|------------------|
| `pipeline` | Deterministic checks, AST graph, semantic indexing (Steps 1-3)   | No               |
| `agent`    | LLM-led investigation, test generation, and chaos campaign       | Yes              |
| `hybrid`   | Pipeline first, then agent (recommended)                         | Yes              |

Pick the mode interactively at startup, or pass `--mode pipeline|agent|hybrid`.

---

## What You See in the Terminal

```
+------------------------- FAULTLINE interactive cli -------------------------+
|  Target Dir: /path/to/project                                              |
|  Target URL: http://localhost:8000                                         |
|  Mode: hybrid                                                              |
+----------------------------------------------------------------------------+

Pipeline phase
  o Deterministic Checks
  + Deterministic Checks - 4 finding(s)
  o AST Dependency Graph
  + AST Dependency Graph - 47 files, 122 dependencies
  o Semantic Indexing
  + Semantic Indexing

Agent phase
  I will start by listing the project files...
  -> list_project_files(target_dir=/path/to/project, glob=**/*.py)
  <- {"files": ["app/views.py", ...]}
  -> read_project_file(target_dir=..., relative_path=app/views.py)
  <- {"content": "def login(request): ..."}
  -> request_user_input(question=Bearer token for /api/auth, input_type=credential)
  +-------------------- Credential Request --------------------+
  | Needed: Bearer token for /api/auth                          |
  +-------------------------------------------------------------+
    Enter Bearer token for /api/auth: ********
  <- (returned to agent)
  ...
  -> execute_chaos_campaign(target_url=..., payloads_json=[...12 payloads])
  +-------------------- HITL Permission Request -----------------+
  | Action: execute_chaos_campaign                              |
  | Will fire 12 HTTP attack payload(s) at http://localhost:8000|
  +-------------------------------------------------------------+
    Approve this action? [y/n] (n): y
  <- {"total_executed": 12, "total_crashes_found": 1, ...}
+-------------------- Finding: MEDIUM ---------------------+
| SQL injection on /api/products?id=                       |
+----------------------------------------------------------+

  [+] reports/campaign_cli.md

+----------------------------------------------------------------+
|  Campaign complete.                                            |
|  Report: reports/campaign_cli.md                               |
|  Tool calls observed: 14                                       |
+----------------------------------------------------------------+
```

The renderer surfaces:

- **Banner** — target dir, URL, mode, and the run folder path
- **Pipeline steps** — `o` running, `+` done, `x` error, `-` skipped
- **Agent turns** — `[ Agent turn N ]` counter in dim cyan at the start of each LLM iteration
- **Agent thinking** — dim italic text (truncated at 600 chars with line count)
- **Tool calls** — `-> tool_name(args summary)` in cyan
- **Tool results** — `<- result` in green (truncated at 400 chars)
- **Phase timing** — elapsed seconds after each phase completes
- **Findings** — color-coded panels by severity (CRITICAL red, HIGH orange, MEDIUM yellow, LOW blue)
- **File generation** — `[+] path/to/file` when reports / patches / tests are written
- **HITL pauses** — yellow panels announcing the prompt before it blocks

---

## Human-in-the-Loop (HITL)

HITL is **on by default** in CLI mode. Two kinds of prompts can occur:

### 1. Credential Requests

The agent has a `request_user_input` tool. When it encounters an authentication challenge or missing API key, it calls:

```python
request_user_input(question="Bearer token for /api/auth", input_type="credential")
```

You see a yellow panel and a masked input prompt. Whatever you type is returned to the agent and used in subsequent requests. Sensitive input is hidden.

### 2. Destructive-Action Permission

Before running `execute_chaos_campaign` (the HTTP attack tool), the CLI pauses and asks:

```
+-------------------- HITL Permission Request -----------------+
| Action: execute_chaos_campaign                              |
| Fire 12 HTTP attack payload(s) at http://localhost:8000     |
+-------------------------------------------------------------+
  Approve this action? [y/n] (n):
```

- `y` — proceeds with the attack
- `n` — vetoes the campaign; the tool returns immediately with `status: "vetoed_by_operator"` and the agent continues with the next step

### Disabling HITL

For unattended runs (CI, scheduled jobs):

```bash
python faultline.py --target-dir . --mode hybrid --target-url ... --no-hitl
```

With `--no-hitl`, all permission prompts auto-approve and `request_user_input` returns an empty string (the agent should detect this and either skip the action or record a configuration finding).

---

## CLI Flags

| Flag              | Type     | Description                                                          |
|-------------------|----------|----------------------------------------------------------------------|
| `--target-dir`    | required | Path to the target project directory (prompted if missing)            |
| `--target-url`    | required for agent / hybrid | Base URL of the running target application       |
| `--mode`          | choice   | `pipeline`, `agent`, or `hybrid` (prompted if missing)                |
| `--log-file`      | optional | Path to the target server log file (default: `server.log`)            |
| `--prompt`        | optional | Override the initial agent prompt                                     |
| `--campaign-id`   | optional | Identifier used in the agent log filename (default: `cli`)            |
| `--no-hitl`       | flag     | Disable HITL prompts; auto-approve all destructive actions            |
| `--no-semantic`   | flag     | Skip FAISS semantic indexing in the pipeline phase                    |

Run `python faultline.py --help` for the full list.

---

## Where Output Goes

Each run creates a unique timestamped folder so runs are isolated and comparable:

```
reports/
  <project>_<YYYYMMDD>_<HHMMSS>/     ← shown in banner after startup
    pipeline_report.md               ← deterministic: syntax, imports, deps, score
    campaign_agent.log               ← full agent reasoning (debug trail)
    agent_report.md                  ← AI-authored vulnerability findings
    testcases/
      api_test_<HHMMSS>.py           ← boilerplate copied + edited by the agent
      model_test_<HHMMSS>.py
```

| File                              | Contents                                                    |
|-----------------------------------|-------------------------------------------------------------|
| `pipeline_report.md`              | Production-readiness score, severity table, findings by category, next steps checklist |
| `campaign_agent.log`              | Full step-by-step agent reasoning (debug trail for reviewers) |
| `agent_report.md`                 | AI-authored vulnerability summary (written via `save_vulnerability_report`) |
| `testcases/*.py`                  | Test scripts copied from boilerplates and edited for this project |
| `.aegis_patches/<file>`           | Proposed code patches written by `propose_code_patch`        |

The terminal output is a real-time view; the run folder is the durable, auditable record. Re-run Faultline after fixing issues to see the production-readiness score improve.

---

## Troubleshooting

**`Provider not configured`** — Set `OPENROUTER_API_KEY` (or another provider's key) before running, or set `FAULTLINE_PROVIDER=claude_cli` and ensure you are logged into the Claude Code CLI.

**`Django setup failed`** — Run `python manage.py migrate` once before the first CLI run so the SQLite DB exists.

**No colors in Windows `cmd.exe`** — Use Windows Terminal or a modern PowerShell. Rich auto-detects ANSI support.

**Agent never asks for credentials** — Make the agent's task explicit in `--prompt` (e.g. *"You will likely need a bearer token for /api/auth — call request_user_input when you do."*). Some models default to skipping auth.

---

## Comparison to the REST API

| Aspect              | CLI (`faultline.py`)        | REST (`POST /api/v1/campaign/start/`) |
|---------------------|------------------------------|----------------------------------------|
| Entry point         | `python faultline.py`         | Django runserver + curl                |
| Output              | Live rich terminal            | Database records + log file            |
| HITL                | Yes (interactive prompts)     | No (fully autonomous)                  |
| Credentials         | Prompted on-demand by agent   | Configured up front via Vault          |
| Best for            | Local exploration, debugging  | Automation, CI, multi-campaign queues  |

Both paths share the same `AegisAgent`, `PipelineRunner`, and tool set — the CLI is purely additive.
