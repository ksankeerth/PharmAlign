# Checklist Assessor

Read this first. It says what this agent does, the rules it keeps, and where to change it.

## What it does

One check per run. A country's checklist asks questions about a dossier ("Is long-term stability
data given at 30 C / 65% RH for 12 months?"). For each one:

- **`agent` check**: the agent reads the law (knowledge base) and the dossier, and reports a
  **finding**: `met`, `attention`, `missing` or `cannot_tell`, with quotes from both. It never
  approves: a person decides the check, and `met` may still need a person to confirm.
- **`typed` check**: the agent only **extracts** the listed values with quotes. It gives no
  verdict. Code applies the rule to the values.

The request's `profile` picks the instruction for an agent check: `quality`, `safety` or
`label_language`. A typed check uses the `typed` instruction. All of them are in `app/agent.py`
(`VARIANTS`), and the run's `prompt_hash` covers every one.

## The contract

`POST /assessments`, then poll `GET /assessments/{run_id}`, `DELETE` to cancel. Models:
`AssessmentRequest`, `AssessmentResult` in `contracts/` (don't edit them here).

## Tools (from the server's MCP endpoint, nothing else)

| Tool | Returns |
|---|---|
| `search_kb(query, purpose, top_k)` | Law passages from the pinned snapshot, or `no_source_found` with `searches_tried` |
| `read_clause(anchor \| document_id + article)` | Clauses with their legal status |
| `get_doc_status(document_id)` | Each version's legal status, and `in_force` |
| `lookup_term(term)` | Where the snapshot defines or uses a term |
| `get_facts(keys)`, `get_site()` | Facts already extracted from the dossier, with quotes |
| `search_dossier`, `read_page`, `read_table` | Dossier text with anchors |
| `verify_quote(anchor, quote)` | Whether the quote is at the anchor |
| `compute(expression)` | Exact arithmetic |

## How a run goes (the aim: few model calls, because each call sends the whole conversation again)

The first message holds the check, `budget` (so the model can plan), `law_sources` (the only law the tools can read: where a person moved
the check, that law and not `check.source`, then the supporting sources) and the known facts. The prompt asks for steps, not single calls:

1. In one step, all the independent calls: `read_clause` for each law source, `get_facts`, and a `search_dossier` for each thing to find.
2. In one step, whatever is still missing (a page no hit showed, a table, every `compute`).
3. The answer. `verify_quote` only for a quote in doubt.

A search hit holds the whole text of its page, and a clause holds its whole text, so neither is read again by anchor. Numbers keep the unit
the quote uses ("2 years" is not turned into months). A typed check asks `get_facts` first: a value already in `facts` is used with its quote.
The golden set in `samples/golden/` measures this on a real model (see its README).

## Result rules (`app/rules.py`, checked before a result leaves the agent)

A broken rule sends the reason back to the model once. A second break fails the run.

| Rule | Mirrors |
|---|---|
| Typed check answers `extraction`, agent check answers `finding` | Contract |
| Every anchor was returned by a tool in this run, and every quote is in that tool's text (or verified) | Server's citation check |
| `met` needs a quote from the law (`search_kb`, `read_clause`, `lookup_term`) | Server: no `met` without a law quote |
| Law quotes come only from `moved_source` (a person moved the check) or else the check's `source` document, plus its `supporting_sources` | Server: law the check names |
| Any status but `cannot_tell` needs evidence | No evidence, no claim |
| `no_source` needs a law search in this run that found nothing; status `cannot_tell` | Contract, server's gap signal |
| Numbers in `summary` appear in a cited quote (for `met`, quotes only) | Server's number check |
| A law quote comes from a version in force (`legal_status`), never a superseded or draft one | Server: `not_in_force` |
| No quote from a page with hidden text (`suspect`) | Server: `suspect_page` |
| A quote is a full sentence of about eight words, or a number with its unit and what it measures | Server: `quote_too_short` |
| A quote does not also stand on another page of the same file that this run read (once if short, twice if long) | Server: `quote_repeated` |
| Typed: exactly the asked keys, each once; a found value appears in its own quote | Server's value check |

## Files

| Path | What it is |
|---|---|
| `app/agent.py` | Instruction, profile variants, `SPEC`, `root_agent`, `app` |
| `app/rules.py` | First message, variant choice, result rules |
| `app/assessor.py` | `assess()`: where a run meets the shared runner |
| `app/fast_api_app.py` | ADK's routes with the contract routes in front, one `/health` |
| `tests/` | Rules and whole runs, with the fake model and a fake MCP server with the server's tool shapes |
| `samples/golden/` | 28 scenarios run on a real model against fake tools that behave like the server's, and a runner for a real server (not part of `pytest`) |

## Run it

```bash
cp .env.example .env
uv sync
uv run pytest -q          # no network, no keys
uv run uvicorn app.fast_api_app:app --port 8000 --reload
```

Settings: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE`, `PHARMALIGN_MODEL_CHECKLIST_ASSESSOR`
(default `MODEL` in `app/agent.py`), `PHARMALIGN_OTEL_TO_CLOUD`, and the Google credentials for the mode.

All test data is synthetic.
