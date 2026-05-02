---
name: node-debugging
description: Use when debugging a Harness-Stata node's behavior, inspecting LLM/tool events from a previous run, or replaying a single node in isolation from a fixture instead of the full workflow.
---

# Node Debugging via .harness/runs/

## Overview

`.harness/runs/<run_id>/` is the persistent JSONL trace produced by every `harness-stata run` and `harness-stata node-run`. Read it with `Read`/`Grep` directly — **do not route to LangSmith Studio**, that UI is for humans and is invisible to Claude. The `node-run` CLI re-executes a single whitelisted node from a fixture so a code change in `data_cleaning` can be validated without re-running upstream `data_probe` / `data_download`.

## When to Use

- A user reports a node's behavior is wrong (e.g. "data_cleaning 合并字段对不上", "data_probe 选错表")
- Need to inspect the actual LLM messages or tool call args/results from a past run
- Iterating on a prompt or code path inside `data_probe` or `data_cleaning` and want to re-test without burning tokens on upstream
- Diagnosing an interrupt-resume timeline

Do NOT use for nodes outside the whitelist (run full pipeline first) or when no `.env` / MCP services are configured.

## Quick Reference

### Re-run a single node

CLI-runnable nodes: `data_probe`, `data_cleaning`. Run `uv run harness-stata node-run --help` for full flag list.

```bash
# default = load input from .harness/latest's run
uv run harness-stata node-run data_cleaning

# explicit fixture from downloads/fixtures/<subdir>/input_state.json
uv run harness-stata node-run data_cleaning --from-fixture 01_capital_structure_roa

# explicit historical run id
uv run harness-stata node-run data_probe --from-run 20260502T103500Z-7f3a2c1b
```

### Trace layout (read these files directly)

```
.harness/latest                          plain text, contains run_id
.harness/runs/<run_id>/
  meta.json                              status, mode, fixture_source, llm_model
  timeline.jsonl                         node-level enter/exit/error events
  nodes/<node>/
    input.json                           entry state
    update.json                          node's returned delta
    output.json                          post-merge full state
    events.jsonl                         LLM/tool event summaries (≤200-char preview)
    sub_nodes/<child>/...                recursive for subgraph nodes (e.g. data_probe → planning_agent)
  raw/<raw_id>.json                      full LLM messages or tool args/result; <raw_id> is taken verbatim from events.jsonl, e.g. evt_000042
```

## Debugging Flow

1. Re-run the node (or look at the latest `run` if a full-pipeline trace is needed).
2. `cat .harness/latest` → `.harness/runs/<id>/`.
3. Skim `timeline.jsonl` to find the failing node and its sequence number.
4. Compare `nodes/<node>/input.json` ↔ `output.json` to see the state transition.
5. Read `nodes/<node>/events.jsonl` to find the suspicious LLM/tool event; note its `raw_id`.
6. Read `raw/<raw_id>.json` for the full payload (LLM messages, tool result).
7. Edit code; re-run from step 1.

## Whitelist Scope & Extension

Only `data_probe` and `data_cleaning` are CLI-runnable in isolation in this revision. Nodes outside the whitelist (`requirement_analysis` / `model_construction` / `data_download` / `descriptive_stats` / `regression` / `hitl`) need a full `harness-stata run` first; their trace still appears under `.harness/runs/<id>/nodes/<node>/`.

To add a new node: edit `src/harness_stata/observability/registry.py` (`NODE_REGISTRY` + `REQUIRED_FIELDS`). No business-code change required — `observability/` is a one-way overlay (`cli > observability > graph > nodes > subgraphs > clients`).

## Common Mistakes

- **Suggesting LangSmith Studio or `langgraph dev`** — these are human-only UIs; `langgraph dev` deliberately does not persist trace because of session-sharing of the compiled graph (see `studio.py` docstring).
- **Reusing a stale `run_id` from a prior session** — always `cat .harness/latest` for the freshest pointer.
- **Stopping at `events.jsonl`** — the 200-char preview is for scanning; pull `raw/<raw_id>.json` for the actual messages.
- **Running full `harness-stata run` to test a code change in `data_cleaning`** — wastes upstream LLM/MCP calls; `node-run --from-fixture` is the correct path.
