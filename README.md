# PharmAlign

An AI-assisted review platform for medicines regulators, configured per country. Agents check a submission against that country's law and guidance, and a named person makes every decision. All data in this repository is synthetic.

## Layout

| Folder | Holds |
|---|---|
| `server/` | API, pipeline, gate checks, decisions, watch, ingestion, and the mock regulator (simulation) |
| `web/` | The UI (React, Vite). It always talks to the server |
| `contracts/` | Request and result models shared by server and agents |
| `agents/` | The 8 agents (Google ADK), one service each, and their shared runtime |
| `knowledge/` | Manifests, country packs, and where the public documents go (they are not stored here) |
| `demo/` | Synthetic seed data, recorded agent answers, and the scripts the scripted model follows, as JSON |
| `infra/` | Cloud Run service files for the agents (not deployed from here) |
| `scripts/` | `dev.sh`: the whole local setup in one command; `run-local-agents.sh`: the 8 agents as local processes |
| `compose.yaml` | Database, server and web; the `agents` profile adds the 8 agent services |

## Run

**Everything with one command.** Starts the database, the server, the 8 agents (scripted model, no key) and the web UI, and seeds the database the first time. Logs are in `.run/`.

```sh
scripts/dev.sh start             # http://localhost:5180
scripts/dev.sh status
scripts/dev.sh stop              # add --all to stop the database too
scripts/dev.sh restart --reseed  # empty the database and load the demo data again
scripts/dev.sh start --no-agents # the server answers from recordings instead
scripts/dev.sh logs [server|web|<agent name>]
```

The demo is reset from Platform Admin › Demo in the app, or with `scripts/dev.sh restart --reseed`.

The steps below do the same by hand.

The UI needs the server: use `scripts/dev.sh start`, or the steps below.

**Local server and UI.** Needs [uv](https://docs.astral.sh/uv/), Docker (for the database) and Node 24.

```sh
docker compose up -d --wait db
cd server && uv sync && uv run alembic upgrade head
PHARMALIGN_ENV=simulation uv run python -m app.seed --reset
PHARMALIGN_ENV=simulation uv run uvicorn --factory app.main:create_app --port 8000
cd ../web && npm install && npm run dev   # http://localhost:5180, forwards /api to port 8000
```

**Real law text.** The seed's law chunks are short paraphrases. No document is stored in this repository: [knowledge/SOURCES.md](knowledge/SOURCES.md) lists every one and where to download it. When the source files are in `knowledge/documents/`, seed with `--with-library` to ingest the real text of the core documents, so the Knowledge Assistant and the readiness check work on the law itself. A missing file is skipped and the seed still succeeds. Details are in `server/README.md`.

```sh
PHARMALIGN_ENV=simulation uv run python -m app.seed --reset --with-library
```

**Containers.** The web UI is at http://127.0.0.1:5190, the server at http://127.0.0.1:8000.

```sh
docker compose up -d --build
docker compose run --rm server python -m app.seed --reset   # one-off, not on every start
docker compose restart server                               # picks up the seeded runs
```

**Real agent services, no model key.** With `PHARMALIGN_MODEL_MODE=scripted` an agent follows a script from `demo/scripted/` instead of calling Gemini: it makes real tool calls against the server's `/mcp`, then answers from the recorded answer. It is refused for live requests and on Cloud Run. Scripts cover CASE-1042, its brief, and two Knowledge Assistant questions.

```sh
# Local processes on ports 8101 to 8108, then one case end to end (the test seeds its own database and starts its own server on port 8000)
docker compose up -d --wait db
scripts/run-local-agents.sh start
cd server && uv run pytest -m chain -s tests/live/test_chain.py && cd ..
scripts/run-local-agents.sh stop

# Containers: the agents profile, and the server calling them over HTTP
docker compose --env-file agents.env --profile agents up -d --build
docker compose exec server python -m app.seed --reset
cd server && PHARMALIGN_CHAIN_SERVER_URL=http://127.0.0.1:8000 PHARMALIGN_CHAIN_DATABASE_URL=postgresql+asyncpg://pharmalign:pharmalign@localhost:5544/pharmalign uv run pytest -m chain -s tests/live/test_chain.py
docker compose --profile agents stop
```

Without `agents.env` the profile starts the agents but the server stays in replay mode. The tokens in `compose.yaml` are development defaults: set `PHARMALIGN_SERVICE_TOKEN` and `PHARMALIGN_RUN_TOKEN_SECRET` in the environment to change them.

**Set up a new country.** The files in [demo/new-country/README.md](demo/new-country/README.md) set up Bahrain through the app's own forms, one step at a time.

## Scope of the UI

The UI shows the UAE only, and two roles: **Case Officer** and **Platform Admin**. The other workspaces and new-country setup stay on the server, not in the UI. The server keeps its five views. In simulation they work as two groups (`server/app/roles.py`): Case Officer and Decision Maker; Platform Admin, Process Owner and Auditor. A person can use any view in their group, the audit log records the view used, and separation-of-duty guards still apply: nobody signs a case they assessed or promoted. Outside simulation, or with `PHARMALIGN_TWO_ROLES=false`, each view gets only its own operations. The knowledge pages are open to everyone; only the Platform Admin role can change them.

## Tests

| Component | Command |
|---|---|
| server | `cd server && uv run pytest` (needs the database; uses its own `pharmalign_test`) |
| contracts | `cd contracts && python -m pytest tests -q`, after `pip install -e ".[dev]"` (the tests need the `dev` extras: fastapi, pyyaml, openapi-spec-validator, pytest) |
| web | `cd web && npm test`, `npm run typecheck`, `npm run check:copy`, `npm run build` |
| web, in a browser against a server | seed and start the server first, then `npm run smoke`, `npm run e2e` (the scenarios) or `npm run e2e:all` (all four specs, in two runs) |
| server, real agent services | `scripts/run-local-agents.sh start`, then `cd server && uv run pytest -m chain -s tests/live/test_chain.py` (skipped when the agents are down; left out of a plain `pytest`) |
| web, against the containers | `BASE_URL=http://127.0.0.1:5190 npm run e2e` |

Each scenario file resets the demo data through the server when it starts, so no manual reseed is needed before a run. In replay mode (`--no-agents`) you can also rerun without restarting. With scripted agents, restart them before running the scenarios again: they remember run ids, and the reset brings the same ids back. The reset leaves out the real law text, to keep it quick.

See [NOTICE](NOTICE) for the license and the third-party documents.
