# Sameness

Read this first. It says what this agent does, the rules it keeps, and where to change it.

## What it does

A generic product here can rely on a product approved abroad (the reference approval). For each
field the server asks about (shelf life, manufacturing site, ...), the agent finds the value in the
reference approval's documents and quotes it, next to the dossier's value.

- `exact` and `number` fields: the agent reports the reference value. **Code** decides same or different.
- `meaning` fields: the agent also gives a view (`same`, `different`, `unclear`).
- A difference is reported with evidence on both sides: `reference_evidence` and `dossier_evidence` are separate lists.
- It never calls a difference acceptable. The country's rules and a person decide.

## The contract

`POST /sameness-comparisons`, then poll `GET /sameness-comparisons/{run_id}`, `DELETE` to cancel.
Models: `SamenessRequest`, `SamenessResult` in `contracts/`.

## Tools

| Tool | Returns |
|---|---|
| `read_reference()` | The reference approvals on record, with the pages of the documents they name, each with its anchor |
| `read_page(doc_id, page)` | One page of a case file, with its anchor |
| `get_facts(keys)` | The dossier's facts, each with quotes |
| `verify_quote(anchor, quote)` | Whether the quote is at the anchor |
| `compute(expression)` | Exact arithmetic |
| `lookup_term(term)` | Where the knowledge snapshot defines a term |

There is no search over the reference documents, so the agent reads them page by page.

## Result rules (`app/rules.py`)

A broken rule sends the reason back to the model once. A second break fails the run.

- Each requested field once, and no others.
- Every anchor was returned by a tool in this run; every quote is in that tool's text, or verified.
- `found` needs a quote from a reference document, and the value or number must appear in it.
  A number field needs `there_number`.
- `agent_view` only on `meaning` fields, and required when one is found. Never `same` or
  `different` without a found value.
- `different` needs both `reference_evidence` and `dossier_evidence`. Reference quotes only in the first, case-file quotes only in the second.
- `searches_tried` says what was read.
- `not_found` has no value, and needs at least one read in the run. `ambiguous` quotes each passage.

## Files

`app/agent.py` (instruction, `SPEC`, `root_agent`, `app`), `app/rules.py`, `app/comparer.py`
(`compare()`), `app/fast_api_app.py`, `tests/`.

## Run it

```bash
cp .env.example .env
uv sync
uv run pytest -q          # no network, no keys
uv run uvicorn app.fast_api_app:app --port 8000 --reload
```

Settings: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE`, `PHARMALIGN_MODEL_SAMENESS`,
`PHARMALIGN_OTEL_TO_CLOUD`, and the Google credentials for the mode. All test data is synthetic.
