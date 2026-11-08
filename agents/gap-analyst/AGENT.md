# Gap Analyst

Read this first. It says what this agent does, the rules it keeps, and where to change it.

## What it does

One analysis per run. Code counts signals (empty slots, questions with no source, local practice rules, repeated waivers and overrides). The agent groups them into **candidates**: a title, a kind (`policy` or `standards`), the signal ids behind it, a description, and examples from other sources.

It proposes and never edits a pack. Nothing it returns is a confirmed gap. Each candidate has a `pack_rule` (a check id), or null with "no pack rule" in the description, said only after looking.

## The contract

`POST /gap-analyses`, then poll `GET /gap-analyses/{run_id}`, `DELETE` to cancel. Models: `GapAnalysisRequest`, `GapAnalysisResult` in `contracts/` (don't edit them here).

## Tools (from the server's MCP endpoint, nothing else)

| Tool | Returns |
|---|---|
| `get_gap_signals(kinds)` | The signals counted by code |
| `get_pack_rule(check_id)` | The pack's checks, or one check in full |
| `search_kb`, `read_clause` | Law and guidance for `global_examples`, with legal status |

## Result rules (`app/rules.py`, checked before a result leaves the agent)

A broken rule sends the reason back to the model once. A second break fails the run.

| Rule | Mirrors |
|---|---|
| Every `signal_ids` entry was returned by `get_gap_signals` in this run and is in the request | Server keeps only the signals it asked about |
| No signal is in two candidates | One candidate per gap |
| `pack_rule` is a check id `get_pack_rule` returned, or null after a `get_pack_rule` call | Candidate cites the rule it concerns |
| `global_examples` come from `search_kb` or `read_clause`; anchors and quotes check out; the source is in force; a quote has at least eight words | Server's citation check |
| The description names an example's status ("in force") | A person sees what kind of source it is |
| `pipeline_node` and `readiness_dimension` are among the lists in the request | Contract |

The first message carries the budget and `plan_within`, what a good run needs. The spec turns on `repeat_answer` (a repeated call is
answered from the run, not sent) `max_model_calls=8` and `force_answer` (after a repeated call, and on the last model call, function calling is off,
so the model answers). The agent isn't given `verify_quote`, which the contract allows: Gemini asked for it in a loop instead of
answering, and the result rules check every quote against the tools' text anyway.

## Golden set

`samples/golden/` runs the real agent on Vertex AI against a fake server with the server's tool shapes. See its README.

## Files

| Path | What it is |
|---|---|
| `app/agent.py` | Instruction, `SPEC`, `root_agent`, `app` |
| `app/rules.py` | First message and result rules |
| `app/analyst.py` | `analyse()`: where a run meets the shared runner |
| `app/fast_api_app.py` | ADK's routes with the contract routes in front, one `/health` |
| `tests/` | Rules and whole runs, with the fake model and a fake MCP server with the server's tool shapes |

## Run it

```bash
cp .env.example .env
uv sync
uv run pytest -q          # no network, no keys
uv run uvicorn app.fast_api_app:app --port 8000 --reload
```

Settings: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE`, `PHARMALIGN_MODEL_GAP_ANALYST` (default `MODEL` in `app/agent.py`), `PHARMALIGN_OTEL_TO_CLOUD`, and the Google credentials for the mode.

All test data is synthetic.
