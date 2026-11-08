# Knowledge Assistant

Read this first. It says what this agent does, the rules it keeps, and where to change it.

## What it does

A chat agent. It answers questions about the law, guidance and the country's pack from the knowledge base, and in a **case session** also about that case. It explains and never decides: it doesn't advise approving anything, changes nothing, and offers a draft comment only for a person to post.

- Every factual sentence is backed by a citation: an anchor a tool returned in this session and a quote from it. The caller adds the title and legal status.
- Law that is not in force is cited only when the person asks about history, and the text says its status.
- When the search finds nothing in force, the agent sends a `no_source` event listing the searches it ran, and never answers from memory.
- A session opened without a `case_id` is knowledge-only: it gets no case tools.

## The contract

`POST /sessions` (201, `{agent_session_id}`), then `POST /sessions/{id}/turns`, streamed as server-sent events (`data: {event json}` per event). Models in `contracts/pharmalign_contracts/agents/knowledge_assistant.py` (don't edit them here). The contract header is required on both. Sessions are in memory: after a restart a turn returns 404, and the caller starts a new session.

Each turn the model answers with `{"events": [...]}` holding only `message`, `no_source`, `not_available` or `draft_comment`. Anything else is refused. The answer is checked first, then streamed: each message as `text_delta` pieces, then the `message`, then `usage`. A failed turn ends with an `error` event and no half answer.

The session's run token goes out as a bearer header on every tool call of that session, and nowhere else.

## Two paths

- **Knowledge-only sessions** run the chat pipeline (`app/chat`: language gate, classify, retrieve, answer, check) when `PHARMALIGN_KA_PIPELINE=on`. The cloud sets it (`infra/terraform/agents.tf`). A turn takes about 10 s and ends inside `PHARMALIGN_KA_TURN_BUDGET` (50 s). A question about whether a law is still in force is searched three ways, and the chips under an answer stay on the question's subject (`app/chat/topics.py` when no model wrote any).
- **Case sessions** always use the model-driven agent. The server gives up on a stream that is silent for 60 s, so the agent sends a comment line every 10 s, thinks briefly in each round, is told to answer after 5 rounds or 40 s, does not repeat a call within a turn, cancels a model call that hangs for 25 s, and ends a turn that takes 75 s with a plain "took too long" error. These controls are opt-in settings of `SessionSpec` in `../common`.

## Tools (from the server's MCP endpoint, nothing else)

| Session | Tools |
|---|---|
| Knowledge-only | `search_kb`, `get_doc_status`, `read_clause`, `lookup_term`, `read_notice` |
| On a case, also | `get_facts`, `get_findings`, `get_conflicts`, `get_previous_run`, `get_check`, `compare_pack`, `read_page`, `read_reference`, `get_run_trace`, `draft_comment` |

## Result rules (`app/rules.py`, checked before anything is streamed)

A broken rule sends the reason back to the model once. A second break ends the turn with an error event.

| Rule | Mirrors |
|---|---|
| Only the four allowed event types | The contract's closed set |
| Every citation's anchor was returned by a tool in this session, and its quote is in that tool's text | Server's citation check |
| A message has at least one citation; with none, the answer is `no_source` | No source, no answer |
| Citing law not in force needs a history question and the status named in the text | Legal status shown |
| `no_source` needs a search in this turn that found nothing, and lists them all | Contract |
| `draft_comment` only in a case session; `trace_ids` only from `get_run_trace` | Drafts are for a person to post |

## Files

| Path | What it is |
|---|---|
| `app/agent.py` | Instruction, knowledge and case variants, `SPEC`, `root_agent`, `app` |
| `app/answer.py` | What the model answers each turn, and the allowed event types |
| `app/rules.py` | First message of a turn and the answer rules |
| `app/fast_api_app.py` | ADK's routes with the session routes in front, one `/health` |
| `tests/` | Rules and whole turns, with the fake model and a fake MCP server with the server's tool shapes |

The session router is in `../common` (`pharmalign_agent_common.sessions`).

## Run it

```bash
cp .env.example .env
uv sync
uv run pytest -q          # no network, no keys
uv run uvicorn app.fast_api_app:app --port 8000 --reload
```

Settings: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE`, `PHARMALIGN_MODEL_KNOWLEDGE_ASSISTANT` (default `MODEL` in `app/agent.py`), `PHARMALIGN_OTEL_TO_CLOUD`, and the Google credentials for the mode.

All test data is synthetic.
