# Reliance Watch

Read this first. It says what this agent does, the rules it keeps, and where to change it.

## What it does

One notice per run. Code has already matched candidates and alerted a person; this run **explains** and can't delay that alert. The agent reads the notice, assesses every candidate (`yes`, `no`, `unclear`, with a reason), picks the change type, and suggests a severity.

Severity can only be **raised** above the request's floor, never lowered. A notice that matches nothing is reported as one `none` candidate, never dropped. `additional_checks` can only add to a re-run.

## The contract

`POST /notice-assessments`, then poll `GET /notice-assessments/{run_id}`, `DELETE` to cancel. Models: `NoticeAssessmentRequest`, `NoticeAssessmentResult` in `contracts/` (don't edit them here).

## Tools (from the server's MCP endpoint, nothing else)

| Tool | Returns |
|---|---|
| `read_notice(notice_id)` | The notice at its latest revision, with the anchor to quote |
| `list_recent_notices(days, regulator)` | Recent notices without text |
| `get_relied_on(approval_ref, regulator)` | Licences and cases that rely on a foreign approval |
| `get_licence(licence_id)` | The medicine, substances, strength, form, holder, status |
| `search_kb`, `read_clause` | Law on reliance, with legal status |
| `verify_quote`, `compute` | Quote check, exact arithmetic |

## Result rules (`app/rules.py`, checked before a result leaves the agent)

A broken rule sends the reason back to the model once. A second break fails the run.

| Rule | Mirrors |
|---|---|
| `change_type` and `suggested_severity` are among the offered values; severity is not below the floor | Server keeps severity at or above the floor |
| Every request candidate is kept; an empty list is refused (use a `none` candidate) | Server stores the notice even if nothing matches |
| A target the code didn't list must have been returned by a tool | No invented licences |
| `additional_checks` are among `open_case_checks` | Server decides the base list |
| A `yes` match, or a raise above the floor, cites the notice text returned by `read_notice` | Match rests on the notice |
| Every anchor was returned by a tool, every quote is in its text | Server's citation check |

## Files

| Path | What it is |
|---|---|
| `app/agent.py` | Instruction, `SPEC`, `root_agent`, `app` |
| `app/rules.py` | First message and result rules |
| `app/watch.py` | `assess_notice()`: where a run meets the shared runner |
| `app/fast_api_app.py` | ADK's routes with the contract routes in front, one `/health` |
| `tests/` | Rules and whole runs, with the fake model and a fake MCP server with the server's tool shapes |

## Run it

```bash
cp .env.example .env
uv sync
uv run pytest -q          # no network, no keys
uv run uvicorn app.fast_api_app:app --port 8000 --reload
```

Settings: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE`, `PHARMALIGN_MODEL_RELIANCE_WATCH` (default `MODEL` in `app/agent.py`), `PHARMALIGN_OTEL_TO_CLOUD`, and the Google credentials for the mode.

All test data is synthetic.
