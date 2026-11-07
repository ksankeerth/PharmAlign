# server

`pharmalign-server`: plain code that owns all state. API, pipeline runner, output control, decisions, watch, outbox and ingestion jobs. In simulation it also hosts a mock regulator under `/mock`. The UI is served by the web container (or Vite in development), which forwards `/api` to the server.

**Rules**
- It starts agents and polls them. Agents never call it.
- It depends on `contracts/`. It never imports from `agents/`.
- Ingestion is part of the server, but runs as a background worker, because Docling is heavy and optional.
- Audit rows chain by hash, one chain per workspace. `audit_head` holds each chain's last row, updated with every append, so `GET /api/v1/audit/verify` also finds rows cut from the end.

**Layout**
- `app/knowledge/`: parsing, chunking, the quality gate, embedding, the ingestion worker and search. The routes are in `app/handlers/library.py`.
- `app/connector/`: the adapter to a government system of record (`ConnectorAdapter`, and `HttpConnector` for the regulator records API).
- `app/mock_regulator/`: a stand-in government system for simulation, mounted at `/mock`. It has its own tables and never reads the case tables.
- Optional extras in `pyproject.toml`: `fast-pdf` (PyMuPDF), `docling` (the parser for Arabic pages) and `bge-m3` (the local embedding model). The server runs without them: text and HTML files parse with the standard library, and simulation uses a small deterministic embedder.

## Run and test

Needs Docker and [uv](https://docs.astral.sh/uv/). Postgres runs from `compose.yaml`, on host port 5544.

```sh
docker compose up -d --wait db        # from the repo root
cd server
uv sync
uv run alembic upgrade head
PHARMALIGN_ENV=simulation uv run uvicorn --factory app.main:create_app --port 8000
uv run pytest                         # uses its own database, pharmalign_test
```

- The server refuses to start with demo identity unless `PHARMALIGN_ENV=simulation`. In simulation it seeds the demo people on startup.
- The UI calls `/api/...` at its own origin and signs in with a cookie. With no cookie, you are the default demo person.
- Non-person callers, such as the scheduler on `POST /internal/ticks`, send `Authorization: Bearer $PHARMALIGN_SERVICE_TOKEN`.
- A worker loop runs inside the server. `PHARMALIGN_WORKER_ENABLED=false` turns it off.

**Settings** (all `PHARMALIGN_*`, read in `app/config.py`)

| Name | Default | Meaning |
|---|---|---|
| `ENV` | `live` | `simulation`, `shadow` or `live`. Demo identity, the mock regulator and the seed need `simulation` |
| `DATABASE_URL` | `postgresql+asyncpg://pharmalign:pharmalign@localhost:5544/pharmalign` | The project database |
| `SERVICE_TOKEN` | unset | Bearer token for non-person callers. Unset refuses them all |
| `RUN_TOKEN_SECRET` | unset | Signs each agent run's tool scope. Required outside simulation |
| `DEFAULT_TENANT`, `DEFAULT_PERSON` | `ws-ae`, `p-alex` | The workspace and the person with no cookie (simulation) |
| `TWO_ROLES` | on in simulation, off elsewhere | The five views act as two role groups (`app/roles.py`). Tests set it off |
| `WORKER_ENABLED`, `WORKER_POLL_SECONDS`, `JOB_LEASE_SECONDS` | `true`, 1, 60 | The in-process job worker |

The agent, connector, mock regulator, watch and knowledge settings are listed in their sections below.

## Container

`server/Dockerfile` builds from the repo root (`docker build -f server/Dockerfile .`). The image runs `alembic upgrade head`, then uvicorn on port 8000, as a non-root user, with a healthcheck on `/health`. It has none of the optional extras unless built with `--build-arg EXTRAS=bge-m3` (`docker compose build --build-arg EXTRAS=bge-m3 server`), which also downloads the model at build time, so the container never fetches it at runtime. `compose.yaml` builds it as the `server` service.

## Schema and seed

- Migration `0002` holds every table. A tenant row has typed columns for what guards, filters and joins need, `tenant_id`, and `doc`, the contract model as JSONB. The same column list sits in `app/tables.py`, and a test compares the two. `app/stores.py` has one `DocStore` per model.
- A parent keeps its full object in `doc`. Child tables (comments, signers, notice revisions, waivers) hold the rows to query on, written in the same transaction.
- Chunks, snapshot items and approved packs cannot be changed by the database. A pack may only move to its next state.
- Agents connect as `pharmalign_agent` (development password in the migration; set a real one elsewhere). They read the `agent_read` views and insert into `agent_tool_log`, nothing else. The tool server scopes a run with `SELECT set_config('app.tenant_id', ..., true), set_config('app.case_id', ..., true)`; unset shows nothing. `kb_requirement_chunk` has in-force sources only.
- Demo data is `demo/seed/*.json`. Times are relative to the load. Load it into an empty database, or reset first (simulation only):

```sh
PHARMALIGN_ENV=simulation uv run python -m app.seed --reset
```

The server picks up unfinished runs when it starts, so start (or restart) it after seeding.

## Demo endpoints

Both are for Platform Admin only, and simulation only: outside simulation they are refused.

- `GET /api/v1/demo` returns the demo status: `loaded_at`, `with_library`, `resetting`, the workspaces, the number of demo people and the number of cases.
- `POST /api/v1/demo/reset` with `{"confirm": "RESET", "with_library": true}` replaces all data with the seed. It answers 202 with the same status. The load runs in the background, so poll `GET /api/v1/demo` until `resetting` is false. A reset that is already running answers 409.

**Real law text (`--with-library`, simulation only).** The seed has no document text beyond eighteen short excerpts that the demo cases and packs cite. With `--with-library`, after the normal seed, the files found in `knowledge/documents/<folder>/<id>__*` (`PHARMALIGN_LIBRARY_DIR` changes the folder) are ingested for A-01, A-03, A-12, the Arabic documents the demo uses and every document the seeded packs cite as a check source. Set `PHARMALIGN_LIBRARY_SCOPE=all` to ingest every file, which takes hours. That folder is not in git, and nothing from it is copied into `demo/` or `web/`.

- Each file goes through the server's own routes and worker: a new version of the seeded document, a trial, a review by a second demo person (`p-sam`, the uploader is `p-jordan`), the full run (parse, quality gate, chunks, embeddings).
- The new version keeps the legal status the document already had, so a repealed law stays repealed. A new snapshot is created and becomes the workspace's active one, and the demo cases move to it. Documents with cited excerpts keep those in force beside the full text, so citations still resolve.
- A missing file, or one the quality gate refuses, is skipped with a line, and the seed still succeeds. Arabic PDFs need `PHARMALIGN_ARABIC_TEXT_LAYER=true`, which `scripts/dev.sh` sets, and without it they are skipped.
- The seed ends by printing the readiness retrieval check for the new snapshot. It is information only: the question set and the pass line are not changed to suit the result.
- A knowledge chat opened with no case searches the active snapshot, so its answers cite the real text. A chat on a seeded case searches that case's snapshot.

## Knowledge

- A file is a version. A changed file is a new version with new chunks; an identical file (same hash) is refused with a 409, and the same file under another document is flagged with `duplicate_of`, not merged. Chunks and their anchors (`doc:vN:cNNNN`) never change, and the database refuses to change them.
- Legal status belongs to a country's adoption of a version (`tenant_document_adoption`), set by a person. A snapshot freezes the status each item had; a case searches its pinned snapshot, so a later adoption does not change it.
- Freezing a draft country's library makes that snapshot its active one. Sign-off makes the snapshot it names the active one. After sign-off a new freeze does not change the active library.
- Ingestion is a chain of short job steps under the usual lease: `parse`, `assess` (chunk and quality gate), then for a full run `commit` and `embed`, a batch at a time. A step that dies leaves nothing behind and is taken up again from the same step. A trial has no effect on search. A full run is refused until a second person approves the version and the gate has not failed. Failed or held versions are never indexed.
- The gate counts letter errors in Arabic text, not only script ranges; scans for hidden text (an instruction in it holds the file for a person, hidden text alone is flagged and removed); and splits chunks longer than the embedder can read. The limits are in `app/knowledge/quality.py`.
- Parsers sit behind one interface (`app/knowledge/parsers.py`). Arabic pages need the Docling parser; without it the page fails with a clear message rather than being read by the fast parser.
- Search filters first (tenant, snapshot, status, review and quality state, shelf, any document or topic limit), then ranks the survivors with pgvector. If nothing qualifies, or nothing is similar enough, the answer is `no_source_found` with the searches tried. Agents rank through the `agent_read` views (`agent_search`).
- With `PHARMALIGN_RERANKER=bge-reranker-v2-m3`, a question in either language also runs keyword search over stemmed words (`chunk_terms`, BM25) and merges it with vector search, and then the reranker reads the best `PHARMALIGN_RERANK_CANDIDATES` (15) passages. `PHARMALIGN_RERANK_FLOOR` is the least reranker score that counts as a source. With the reranker off, questions use vector search alone. `scripts/dev.sh` turns the reranker on. It is about 1 GB and downloads on the first search.
- `PHARMALIGN_EMBEDDER` is `auto` (bge-m3 if installed, else the deterministic embedder in simulation), `hash` or `bge-m3`. `PHARMALIGN_SIMILARITY_FLOOR` overrides the least similarity that counts as a source. `PHARMALIGN_EMBED_BATCH` is the chunks embedded per step.
- The server logs the embedder it uses at startup, and the readiness retrieval result names it. A weak lexical hit (such as "closure") can pass the similarity floor, so a no-source probe must be checked against the library: `uv run python -m scripts.probe_absent <term>...` prints how many chunks contain each term.
- bge-m3: `uv sync --extra bge-m3` installs it; a plain `uv sync` removes it again. The demo uses bge-m3 with `--with-library`. Tests always use the deterministic embedder.

## Agents

The server knows agents only through `pharmalign_contracts`. `app/pipeline/runner.py` holds what handlers call: `start_pipeline`, `rerun`, `evidence_changed`, `cancel`, and `tick` for the scheduler. Each runs in the caller's transaction, except `tick`, and no transaction stays open during an agent call.

- `PHARMALIGN_AGENT_MODE` is `http` or `replay`. It defaults to `replay` in simulation and `http` elsewhere. Replay answers from `demo/replay/`, and a run with no stored answer fails and shows Error.
- `PHARMALIGN_AGENT_URL_<AGENT>` gives each agent's URL, such as `PHARMALIGN_AGENT_URL_CHECKLIST_ASSESSOR`. `PHARMALIGN_AGENT_TOKEN` is an optional bearer token for agent calls. It is unset by default, and then no `Authorization` header is sent (agents have no sign-in of their own). To change how tokens are made, pass another `token_provider` to `HttpAgentClient`.
- `PHARMALIGN_RUN_TOKEN_SECRET` signs each run's and assistant session's run token (see Agent tools). It is required outside simulation.
- `PHARMALIGN_AGENT_POLL_SECONDS` is how often a running agent is polled. It defaults to 1 in simulation and 5 elsewhere.
- A blocking check an agent finds met gets a second reading from the Challenger (`runs/<case>/challenge-<check result id>.json` in replay). Only a dispute with a verified quote that differs from the claim's own changes the check, and then only to Person confirms.
- The Knowledge Assistant is reached through `app/pipeline/assistant.py`: `PHARMALIGN_AGENT_URL_KNOWLEDGE_ASSISTANT` in `http` mode, stored answers in `replay`, from the seeded knowledge base and, for a chat opened on a case, that case's own records. A chat with no case reads only the knowledge base. What it says is stored, and never changes a status.
- Signing a grant first reads the foreign watch sources through a `WatchFetcher` (`app/fetcher.py`). The one in use is `watch.sources.SourceFetcher`, which reads by a source's `kind`.

## Agent tools

Agents read through tools the server serves over MCP (streamable HTTP, stateless, JSON responses) at `POST /mcp`. Code in `app/mcp/`.

- **On or off:** `PHARMALIGN_MCP_ENABLED`. It defaults to on in simulation and off elsewhere. Outside simulation it needs `PHARMALIGN_RUN_TOKEN_SECRET` and `PHARMALIGN_AGENT_DATABASE_URL` (the `pharmalign_agent` login); in simulation the login defaults to the database URL with the development password.
- **Auth:** every request sends `Authorization: Bearer <run_token>`, the token from the run request (or the assistant's `SessionCreate`). An ADK agent connects with `McpToolset(connection_params=StreamableHTTPConnectionParams(url="<server>/mcp", headers={"Authorization": f"Bearer {run_token}"}))`.
- **The token** (`app/mcp/token.py`, the one format for runs and sessions) is `pa1.<claims>.<HMAC-SHA256>`, signed with `PHARMALIGN_RUN_TOKEN_SECRET`. Claims: kind (`run` or `session`), tenant, case (none for a knowledge-only session, or a Reliance Watch or Gap Analyst run for the whole workspace), agent, run or session id, snapshot, tool-call budget, expiry. A bad signature gets a 401.
- **Scope:** the token alone sets it. No tool takes a tenant or a case. Each call checks the run is still current (not superseded, cancelled, or finished past `PHARMALIGN_AGENT_DEADLINE_GRACE_SECONDS`), then reads the `agent_read` views on the agent login, in a read-only transaction after `set_config('app.tenant_id' | 'app.case_id' | 'app.snapshot_id', ..., true)`. With a case, knowledge comes from its pinned snapshot; without one, from the snapshot in the token.
- **Limits:** `PHARMALIGN_MCP_MAX_ROWS` (50) rows and `PHARMALIGN_MCP_MAX_CHARS` (12000) characters per result; a cut result has a `truncated` note. Calls per run come from the run's budget (`PHARMALIGN_ASSISTANT_MAX_TOOL_CALLS` for a session). A refusal or failure is a tool error (`isError`, a contract `ToolError`), never an exception.
- **Log:** every call, refused or not, writes an `agent_tool_log` row with the agent login: agent, run, tool, hashes of the arguments and result, result size, outcome, hits, time taken. A call that can't be logged returns no result.
- `tools/list` shows an agent only the tools its contract names:

| Agent | Tools |
|---|---|
| dossier-reader | `list_documents`, `read_page`, `read_table`, `search_dossier`, `verify_quote`, `lookup_term` |
| checklist-assessor | `search_kb`, `read_clause`, `get_doc_status`, `read_page`, `read_table`, `search_dossier`, `get_facts`, `get_site`, `verify_quote`, `compute`, `lookup_term` |
| sameness | `read_reference`, `read_page`, `get_facts`, `verify_quote`, `compute`, `lookup_term` |
| reliance-watch | `read_notice`, `list_recent_notices`, `get_relied_on`, `get_licence`, `search_kb`, `read_clause`, `verify_quote`, `compute` |
| challenger | `read_clause`, `search_kb`, `get_doc_status`, `read_page`, `read_reference`, `verify_quote`, `compute` |
| coordinator | `get_findings`, `get_conflicts`, `get_previous_run`, `read_clause`, `lookup_term` |
| gap-analyst | `get_gap_signals`, `get_pack_rule`, `search_kb`, `read_clause`, `verify_quote` |
| knowledge-assistant | `search_kb`, `get_doc_status`, `read_clause`, `lookup_term`, `read_notice`; with a case also `get_facts`, `get_findings`, `get_conflicts`, `get_previous_run`, `get_check`, `compare_pack`, `read_page`, `read_reference`, `get_run_trace`, `draft_comment` |

## Foreign watch and notifications

- Every notice comes in through `watch.notices.ingest`, from a source fetch or a staged simulation event. In one transaction it stores the notice, matches it, creates the alert and acts on the cases it touches. No agent is called, so the alert fires with every agent down. A correction is a new revision of the same notice, and the same content again changes nothing.
- The pack's severity floor is the least a notice can have. An explanation (`apply_assessment`) can raise it, never lower it. Many licence matches from one notice make one incident.
- A match on an open case runs `rules.notice_gate` and re-runs the foreign-status check. A match on a signed case not yet recorded pauses its proposal (`hold_for_notice`). A person dismisses a match with a reason, or opens a re-review, which is a case of its own.
- A source is unhealthy when its fetch errors (`failing`), when it yields nothing for `PHARMALIGN_WATCH_SILENCE_DAYS` (14, `zero_yield`) or when its layout changed. A grant that relies on one needs the signer's reason. The `watch` tick reads sources that are due. In simulation a source reads the items staged for it; a real feed or page isn't fetched yet.
- A notification is an outbox event (`domain_event`) and a delivery (`notification_delivery`), written with the change that caused them. Handlers read deliveries. The worker marks each sent once. An urgent alert not acknowledged in 60 minutes escalates once, from a job scheduled with it and from the `escalations` tick.

## Government records

- Signing writes the proposal and its job together. The job (`decisions.send_proposal`, in `app/handlers/records.py`) sends outside any transaction, with the proposal's idempotency key on every attempt. `rules.next_record_step` decides each step.
- A timeout, a dropped connection or a 5xx is an unknown outcome. The next attempt first reads the result back by key, and sends again only if the government system holds nothing for it. After five attempts the proposal stays `unknown`, and a person settles it with the manual outcome.
- The licence and case change only on an acknowledged outcome. The applicant is told only after it (`rules.may_tell_applicant`).
- A decision on hold is never sent. `hold_for_notice` pauses it, and reconfirm or revoke ends the hold.
- `PHARMALIGN_CONNECTOR_BASE_URL` and `PHARMALIGN_CONNECTOR_TOKEN` name the system of record. In simulation they default to the built-in mock at `http://127.0.0.1:8000/mock`. `PHARMALIGN_CONNECTOR_TIMEOUT_SECONDS` (10) and `PHARMALIGN_CONNECTOR_RETRY_SECONDS` (5, doubling) tune the calls.
- The mock regulator mounts in simulation only. `PHARMALIGN_MOCK_REGULATOR=true` anywhere else stops the server. For a fault, `POST /mock/test-controls/failure-mode` with `normal`, `applied_but_timed_out`, `timeout_before_applying`, `reject_next`, `unavailable` or `slow`. The one-shot faults apply to the next new proposal.

## Add an operation

Routes, status codes and models come from `pharmalign_contracts`, so a handler only supplies behaviour. An unbound operation answers 501. Add the handler to `app/handlers/<domain>.py`, and import that file in `app/handlers/__init__.py`:

```python
CONFLICTS = DocStore(ConflictRow, m.Conflict, columns=lambda c: {"case_id": c.case_id})

@op("POST", "/api/v1/conflicts")
async def declare_conflict(req, ctx):
    if not req.body.party_id and not req.body.case_id:
        invalid("A conflict needs a party or a case.")
    c = m.Conflict(**req.body.model_dump(), id=ctx.id("coi"), declared_at=ctx.now())
    CONFLICTS.add(ctx, c)
    ctx.audit(action="conflict_declared", object_type="conflict", object_id=c.id, after=c, reason=c.reason, case_id=c.case_id)
    return c
```

| What a handler uses | How |
| --- | --- |
| The request | `req.path.x`, `req.query.x`, `req.body.x`, `req.if_match` (`req.query["from"]` for a keyword) |
| Stored data | a table in `app/tables.py` with a migration, through `DocStore` or `ctx.db` |
| Who is calling | `ctx.person_id`, `ctx.view`, `ctx.workspace_id`, `ctx.person`, `ctx.tenant` |
| Time and ids | `ctx.now()` (a datetime), `ctx.id("x")` |
| Records | `ctx.audit(...)`, `ctx.timeline(...)`, `ctx.notify(...)`, with keyword arguments |
| Later work | `ctx.schedule(kind, payload, ctx.after(delay_ms))`, and `@task("kind") async def f(payload, ctx)` in `app/jobs.py`'s registry |
| Refusals | `not_found`, `invalid`, `forbidden`, `problem`, `enforce_guards`, `guard`, `require_reason` |
| Paging | `paginate(items, req.query)`, `await paginate_select(ctx.db, stmt, req.query)` or `await STORE.page(ctx, req.query)` |

Handlers are async and return a dict or the contract's model. The wrapper authenticates, runs `authz.can_act`, validates the request, applies `Idempotency-Key`, runs the handler, checks the result against the contract, writes audit, and commits. Any exception rolls all of it back. Don't commit in a handler.

When a guard depends on a row you read, lock it: `await STORE.require(ctx, id, for_update=True)`.
