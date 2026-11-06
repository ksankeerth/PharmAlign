# Coordinator

Read this first. It says what this agent does, the rules it keeps, and where to change it.

## What it does

One brief per run. The request carries each check's status and summary, the fact conflicts, open comments and the options for the recommendation, all found by code. The agent writes **prose and drafts only**: it chooses one of the offered options, summarises, and drafts questions to the company in each output language.

It works from findings only. It never invents a finding, never changes a status, and never sends a question: a person does. Its result carries the request's `input_fingerprint` unchanged, so the caller can discard a stale brief.

## The contract

`POST /briefs`, then poll `GET /briefs/{run_id}`, `DELETE` to cancel. Models: `BriefRequest`, `BriefResult` in `contracts/` (don't edit them here).

## Tools (from the server's MCP endpoint, nothing else)

| Tool | Returns |
|---|---|
| `get_findings(status)` | The case's current check results with quotes |
| `get_conflicts()` | Facts with different values across files and checks |
| `get_previous_run()` | The previous run's results and the earlier recommendation |
| `read_clause`, `lookup_term` | Law a finding names, with legal status |

## Result rules (`app/rules.py`, checked before a result leaves the agent)

A broken rule sends the reason back to the model once. A second break fails the run.

| Rule | Mirrors |
|---|---|
| `input_fingerprint` equals the request's | Server discards a stale brief |
| `recommendation.choice` is one of `recommendation_options` | Server refuses a choice not offered |
| `recommendation.reason_evidence` lists check ids from `results` | Server refuses a reason that cites nothing it was given |
| Every draft question's `check_ids` are checks in `results`; texts in exactly the output languages | A question is tied to a finding |
| Numbers in the summary, reason and questions appear in a finding, comment or tool result | No invented facts |
| Evidence (none today) passes the shared anchor and quote check | Server's citation check |

## Files

| Path | What it is |
|---|---|
| `app/agent.py` | Instruction, `SPEC`, `root_agent`, `app` |
| `app/rules.py` | First message and result rules |
| `app/coordinator.py` | `write_brief()`: where a run meets the shared runner |
| `app/fast_api_app.py` | ADK's routes with the contract routes in front, one `/health` |
| `tests/` | Rules and whole runs, with the fake model and a fake MCP server with the server's tool shapes |

## Run it

```bash
cp .env.example .env
uv sync
uv run pytest -q          # no network, no keys
uv run uvicorn app.fast_api_app:app --port 8000 --reload
```

Settings: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE`, `PHARMALIGN_MODEL_COORDINATOR` (default `MODEL` in `app/agent.py`), `PHARMALIGN_OTEL_TO_CLOUD`, and the Google credentials for the mode.

All test data is synthetic.
