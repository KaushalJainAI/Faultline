# Operator Commands

During an interactive CLI campaign, press `Esc` to open the Steering Room. Commands typed there control the running agent.

## Core Commands

| Command | Alias | Behavior |
|---------|-------|----------|
| `/status` | `/s` | Shows current campaign counters: turn, findings, LLM calls, tool calls, compacted request context, elapsed time, and active model. |
| `/wrapup` | `/wrap`, `/finish` | Forces the agent to stop testing and finish in a few LLM calls with a final report and walkthrough. |
| `/steer <msg>` | none | Injects structured operator guidance into the next agent turn. Raw text without a slash is also treated as steering. |
| `/save` | none | Saves a checkpoint immediately. |
| `/resume` | `/r`, `/c` | Leaves the Steering Room and continues the campaign. |
| `/quit` | `/q`, `/exit` | Saves a checkpoint and exits. Resume with `python faultline.py --resume <run_folder>`. |
| `/model <name>` | `/m` | Switches model mid-campaign. Without an argument, shows/selects available models. |
| `/set <var> <value>` | none | Changes selected runtime controls. |
| `/vars` | none | Shows current runtime controls. |
| `/findings` | `/f` | Lists findings known to the running agent. |
| `/skip` | none | Skips the current phase. |
| `/help` | `/h` | Shows command help. |

## `/status`

Use `/status` when you want a quick factual snapshot without changing agent behavior.

It reports:

- agent turn
- findings count
- elapsed time
- LLM calls used / allowed
- tool calls used / allowed
- compacted request context estimate
- active model

## `/wrapup`

Use `/wrapup` when you want the campaign to finish now, even if testing is incomplete.

Faultline will:

1. Stop new discovery and testing.
2. Constrain remaining LLM calls to a small closeout window.
3. Ask the agent to synthesize completed work.
4. Save `vulnerability_report.md` in the run folder.
5. Use the final call for a concise operator walkthrough:
   - what was completed
   - what remains incomplete
   - artifacts generated
   - confidence/completion estimate
   - recommended next steps

If the LLM budget ends before the report is saved, Faultline writes a factual fallback `vulnerability_report.md` from known run artifacts.
