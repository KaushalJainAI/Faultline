# Context Management

Faultline keeps storage lossless while keeping model requests compact.

## Mental Model

- **Stored history** is the full campaign transcript and tool output retained on disk.
- **Request context** is the compact prompt sent to the model for one LLM call.
- Stored history may grow beyond the request context limit. That is expected.
- Request context should stay within the per-call model budget after compaction.

## What Is Stored

Each run folder may contain:

| Path | Purpose |
|------|---------|
| `checkpoint.json` | Full resumable agent state. |
| `transcript.txt` | Human-readable conversation transcript. |
| `campaign_agent.log` | Agent/tool/debug event log. |
| `history_vault/*.txt` | Exact archived prior messages. |
| `history_index.md` | Compact index of archived messages. |
| `content_store/*.txt` | Large tool/file outputs stored by reference. |
| `memory.md` / `memory.json` | Compact ref ledger for stored tool/file outputs. |
| `live_report.md` | Operator-facing status, plan, and running notes. |

## What Goes Into The Model

The agent prompt normally includes:

- System prompt and budget rules.
- Target config and session headers.
- Compact progress/status block.
- Tail of `live_report.md` as the current plan.
- `memory.md` and `history_index.md` references.
- Recent operator steering messages.
- The latest AI/tool cycle in fuller detail.

Older content is represented by summaries and refs. The model can retrieve exact content with:

```text
retrieve_history_message(run_folder, message_id)
retrieve_stored_content(run_folder, ref_id)
```

## UI Labels

The terminal progress panel separates two numbers:

```text
Request  148k/180k (82%)  (compacted prompt)
History  440k/1000k (44%) (stored raw)
```

`Request` is the estimated compacted prompt size. `History` is lifetime stored/raw campaign usage.

## Endgame Behavior

Faultline reserves the end of the LLM call budget for closure:

- `FAULTLINE_REPORTING_RESERVE_CALLS` defaults to `10`.
- `FAULTLINE_FINAL_WALKTHROUGH_CALLS` defaults to `1`.

When the reserve is reached, exploratory tools are pruned. The final call is text-only walkthrough mode: no tools, no new testing, just completed work, incomplete work, artifacts, and next steps.
