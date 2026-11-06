# agents

The PharmAlign AI agents, built with Google ADK. One uv project per agent, plus `common`, the shared runtime. Each agent is its own Cloud Run service.

| Folder | What it is |
|---|---|
| `common/` | `pharmalign-agent-common`: contract routes, session routes, ADK runner, output control, the tool log and evidence rules, shared ground rules for instructions, settings, and a test kit (fake model, fake MCP server, fake case data in the server's tool shapes) |
| `dossier-reader/` | The Dossier Reader. Its Dockerfile builds from the repository root, so `contracts/` and `common/` are included |
| `checklist-assessor/` | The Checklist Assessor: one check per run, a finding for an agent check (profile `quality`, `safety` or `label_language`), extracted values for a typed check |
| `sameness/` | Sameness: each field's value in the foreign reference approval, with quotes on both sides of a difference |
| `challenger/` | The Challenger: tries to disprove one finding; disputes only with a verified contradicting quote |
| `coordinator/` | The Coordinator: the brief for the person who decides (recommendation, summary, draft questions), from findings only |
| `reliance-watch/` | Reliance Watch: what a foreign notice means for licences and cases; severity can be raised above the floor, never lowered |
| `gap-analyst/` | The Gap Analyst: candidate pack-rule gaps from signals counted by code; proposes, never edits |
| `knowledge-assistant/` | The Knowledge Assistant: a chat agent with sessions and streamed turns, cited answers, and `no_source` when nothing in force answers |

Every agent's Dockerfile builds from the repository root, so `contracts/` and `common/` are included.

**How an agent works**
- The server starts a run over the agent's contract routes (`POST`, then poll with `GET`, `DELETE` to cancel). Runs are held in memory: after a restart a poll returns 404, which the server treats as lost.
- The agent's tools come only from the server's MCP endpoint (`PHARMALIGN_MCP_URL`). The run token from the request goes out as a bearer header, per run.
- The final answer must validate against the contract's result model. An invalid answer is retried once, then the run fails. A cut-off answer fails. Budgets in the request are enforced.
- The runner records what every tool returned in the run (`ToolLog`). Each agent's `app/rules.py` uses it: an anchor no tool returned, or a quote that isn't in the tool's text, is refused. A broken rule is sent back once with its reason.

**Rules**
- Agents never call the server except through MCP tools.
- Agent code has no database access. Data comes through the tools, which read through the server's limited views.
- It depends on `contracts/` and `common/`. It never imports from `server/`.
- No acting tools: an agent can read, search and check, never write, send or approve.

**Tests** run offline with `uv run pytest` in each project, using the fake model and fake MCP server from `pharmalign_agent_common.testing`.

**Session agents** (the Knowledge Assistant) serve `POST /sessions` and `POST /sessions/{id}/turns` (server-sent events) from `build_session_router(...)`. Sessions are in memory: after a restart a turn returns 404 and the caller opens a new one. A `SessionSpec` holds the instruction, the knowledge and case tool lists, the allowed event types and the turn rules.

**A new agent** needs an `agent_spec(...)` (instruction ending with `GROUND_RULES`, default model, result model, first message, result rules taking `(request, result, log)`), a thin function that calls `run_agent`, and `adk_app(...)` (or `build_router(...)`) for the service. `checklist-assessor/` shows instruction variants picked per request.

On Cloud Run, who may call an agent is set by the platform (invoker permission), not by the agent.

**Settings** every agent reads: `PHARMALIGN_MCP_URL`, `PHARMALIGN_MODEL_MODE` (`vertex`, `api_key` or `scripted`), `PHARMALIGN_MODEL_<AGENT>` (for example `PHARMALIGN_MODEL_DOSSIER_READER`, `PHARMALIGN_MODEL_CHECKLIST_ASSESSOR`, `PHARMALIGN_MODEL_SAMENESS`, `PHARMALIGN_MODEL_CHALLENGER`, `PHARMALIGN_MODEL_COORDINATOR`, `PHARMALIGN_MODEL_RELIANCE_WATCH`, `PHARMALIGN_MODEL_GAP_ANALYST`, `PHARMALIGN_MODEL_KNOWLEDGE_ASSISTANT`), `PHARMALIGN_OTEL_TO_CLOUD` (telemetry export to Google Cloud is off unless this is `true`), plus the Google credentials for the chosen mode.

**Scripted model** (`PHARMALIGN_MODEL_MODE=scripted`, no model key). For a request, the agent makes the real MCP tool calls listed in a script, so anchors and quotes come from real tool results and pass the agent's own rules, then answers from the recorded answer. Scripts are data in `demo/scripted/<agent>/<key>.json` (`PHARMALIGN_DEMO_DIR` points at the `demo` folder; the compose profile mounts it). The key is the check id for the Checklist Assessor, `challenge-<finding id>` for the Challenger, the agent's name otherwise; the Knowledge Assistant's scripts hold the question text. An answer can start from a file under `demo/replay/runs/` (`from`) with `patch` entries where the agent's rules ask for more, and `$request.<path>` echoes a request field. A request with no script fails with `internal_error` ("no script for this request"). A live request is refused (422 `scripted_not_for_live`), and so is starting on Cloud Run. `scripts/run-local-agents.sh start` runs all 8 on ports 8101 to 8108, and the compose profile `agents` does the same in containers.
