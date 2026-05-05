# Faultline Interactive CLI

**Date**: 2026-05-01
**Description**: Comprehensive guide to the `faultline.py` interactive terminal agent, covering modes, live feedback, HITL prompts, and run isolation.

Faultline ships with an interactive command-line agent â€” `faultline.py` â€” that runs the full testing pipeline directly in your terminal with live agent reasoning, real-time tool call visibility, and human-in-the-loop (HITL) prompts when the agent needs credentials or permission.

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

- **Banner** â€” target dir, URL, mode, and the run folder path
- **Pipeline steps** â€” `o` running, `+` done, `x` error, `-` skipped
- **Agent turn counter** â€” `[ Agent turn N ]` counter in dim cyan at the start of each LLM iteration.
- **Live Status & ETA** â€” real-time spinners (e.g., `o Agent is thinking... (ETA: 42s)`) that update while waiting for LLM or tool responses.
- **Agent thinking** â€” dim italic text (truncated at 600 chars with line count).
- **Tool calls** â€” `-> tool_name(args summary)` in cyan.
- **Tool results** â€” `<- result` in green (truncated at 400 chars).
- **Phase timing** â€” elapsed seconds after each phase completes.
- **Findings** â€” color-coded panels by severity (CRITICAL red, HIGH orange, MEDIUM yellow, LOW blue)
- **File generation** â€” `[+] path/to/file` when reports / patches / tests are written
- **HITL pauses** â€” yellow panels announcing the prompt before it blocks

---

## Progress Panel

The campaign progress panel separates three counters that used to be easy to confuse:

- `Request`: estimated compacted prompt size for the last/next model request.
- `History`: raw stored campaign history against the lifetime campaign budget.
- `LLM`: model calls used against the hard call limit.

Stored history can exceed the per-request context budget because Faultline archives old messages and large tool outputs to disk, then injects compact references into the model prompt.

---

## Steering Room Commands

Press `Esc` during the agent phase to pause and open the Steering Room.

| Command | What it does |
|---------|--------------|
| `/status` | Shows current progress, LLM calls, tool calls, compacted request context, findings, elapsed time, and active model. |
| `/wrapup` | Forces the agent to stop testing and finish with final report plus walkthrough in a few calls. |
| `/steer <msg>` | Redirects the agent's next action. Raw text also works as steering. |
| `/save` | Saves a checkpoint immediately. |
| `/quit` | Saves a checkpoint and exits. |

`/finish` remains available as an alias for `/wrapup`; `/wrap` is a short alias. See [Operator Commands](OPERATOR_COMMANDS.md) for the full list.

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

- `y` â€” proceeds with the attack
- `n` â€” vetoes the campaign; the tool returns immediately with `status: "vetoed_by_operator"` and the agent continues with the next step

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
  <project>_<YYYYMMDD>_<HHMMSS>/     â† shown in banner after startup
    pipeline_report.md               â† deterministic: syntax, imports, deps, score
    campaign_agent.log               â† full agent reasoning (debug trail)
    vulnerability_report.md          <- final AI/fallback vulnerability report
    live_report.md                   <- operator-facing live status and notes
    checkpoint.json                  <- resumable full agent state
    history_index.md                 <- index of archived prior messages
    history_vault/                   <- archived exact message contents
    content_store/                   <- archived large tool/file outputs
    testcases/
      api_test_boilerplate.py        â† automatically deployed at startup
      model_test_boilerplate.py       â† edited in-place by the agent
```

| File                              | Contents                                                    |
|-----------------------------------|-------------------------------------------------------------|
| `pipeline_report.md`              | Production-readiness score, severity table, findings by category, next steps checklist |
| `campaign_agent.log`              | Full step-by-step agent reasoning (debug trail for reviewers) |
| `vulnerability_report.md`         | Final vulnerability summary, written by the agent or fallback closeout |
| `live_report.md`                  | Current status, plan, progress notes, and synthesis sections |
| `checkpoint.json`                 | Full resumable state for `python faultline.py --resume <run_folder>` |
| `history_index.md` / `history_vault/` | Compact index plus exact archived messages |
| `content_store/` / `memory.md`    | Large tool/file outputs stored by reference |
| `testcases/*.py`                  | Test scripts copied from boilerplates and edited for this project |
| `.aegis_patches/<file>`           | Proposed code patches written by `propose_code_patch`        |

The terminal output is a real-time view; the run folder is the durable, auditable record. Re-run Faultline after fixing issues to see the production-readiness score improve.

Current runs use `vulnerability_report.md` as the final report artifact.

---

## Troubleshooting

**`Provider not configured`** â€” Set `OPENROUTER_API_KEY` (or another provider's key) before running, or set `FAULTLINE_PROVIDER=claude_cli` and ensure you are logged into the Claude Code CLI.

**`Django setup failed`** â€” Run `python manage.py migrate` once before the first CLI run so the SQLite DB exists.

**No colors in Windows `cmd.exe`** â€” Use Windows Terminal or a modern PowerShell. Rich auto-detects ANSI support.

**Agent never asks for credentials** â€” Make the agent's task explicit in `--prompt` (e.g. *"You will likely need a bearer token for /api/auth â€” call request_user_input when you do."*). Some models default to skipping auth.

---

## Comparison to the REST API

| Aspect              | CLI (`faultline.py`)        | REST (`POST /api/v1/campaign/start/`) |
|---------------------|------------------------------|----------------------------------------|
| Entry point         | `python faultline.py`         | Django runserver + curl                |
| Output              | Live rich terminal            | Database records + log file            |
| HITL                | Yes (interactive prompts)     | No (fully autonomous)                  |
| Credentials         | Prompted on-demand by agent   | Configured up front via Vault          |
| Best for            | Local exploration, debugging  | Automation, CI, multi-campaign queues  |

Both paths share the same `AegisAgent`, `PipelineRunner`, and tool set â€” the CLI is purely additive.

