# Challenger

Read this first. It says what this agent does, the rules it keeps, and where to change it.

## What it does

The server sends it one important claim another agent made, with that claim's evidence, but
**not** the other agent's reasoning, so this reading stays independent. The Challenger reads the
sources itself and tries to disprove the claim.

| Verdict | Meaning |
|---|---|
| `disputed` | A verified quote contradicts the claim, and it isn't one of the claim's own quotes |
| `upheld` | Its own reading supports the claim, with evidence |
| `cannot_verify` | It could neither confirm nor contradict the claim |

It never overwrites a finding. A dispute that stands sets the check to Person confirms.

## The contract

`POST /challenges`, then poll `GET /challenges/{run_id}`, `DELETE` to cancel. Models:
`ChallengeRequest`, `ChallengeResult` in `contracts/`.

## Tools

`read_clause`, `search_kb`, `get_doc_status` (law), `read_page`, `search_dossier`, `read_reference`
(dossier and reference), `verify_quote`, `compute`. The request's `check_source` names the law the
check is set to; law is cited only from there.

## Result rules (`app/rules.py`)

- Every anchor was returned by a tool in this run; every quote is in that tool's text, or verified.
  Otherwise the reason goes back to the model once, and a second break fails the run.
- `upheld` needs evidence.
- Law quotes come only from `check_source`.
- `searches_tried` says what was read or searched.
- Numbers in `reason` appear in a cited quote or in the claim.
- `disputed` with no quote that `verify_quote` confirmed and that differs from the claim's own
  quotes is turned into `cannot_verify`, keeping the evidence, as the server would treat it.

## Files

`app/agent.py` (instruction, `SPEC`, `root_agent`, `app`), `app/rules.py`, `app/challenge.py`
(`challenge()`), `app/fast_api_app.py`, `tests/`.

## Run it

```bash
cp .env.example .env
uv sync
uv run pytest -q          # no network, no keys
uv run uvicorn app.fast_api_app:app --port 8000 --reload
```

Settings: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE`, `PHARMALIGN_MODEL_CHALLENGER` (the
contract expects a stronger model here), `PHARMALIGN_OTEL_TO_CLOUD`, and the Google credentials
for the mode. All test data is synthetic.
