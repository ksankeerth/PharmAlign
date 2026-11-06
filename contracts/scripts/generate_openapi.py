"""Write one OpenAPI file per component from the contract models. `--check` fails if a file is out of date."""
import sys
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import Body, Depends, FastAPI, Header, Path as PathParam
from fastapi.openapi.utils import get_openapi
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pharmalign_contracts.agents import knowledge_assistant as ka  # noqa: E402
from pharmalign_contracts.agents.run_agents import RUN_AGENTS, RunAgent, poll_example  # noqa: E402
from pharmalign_contracts.regulator import REGULATOR_API  # noqa: E402
from pharmalign_contracts.server.routes import SERVER_API  # noqa: E402
from pharmalign_contracts.spec import build_openapi  # noqa: E402
from pharmalign_contracts.common import (  # noqa: E402
    CONTRACT_HEADER,
    CONTRACT_VERSION,
    RUN_ID_PATTERN,
    Health,
    Problem,
)

OUT = ROOT / "openapi"
RunIdParam = Annotated[str, PathParam(pattern=RUN_ID_PATTERN)]


async def contract_header(
    version: Annotated[str, Header(alias=CONTRACT_HEADER, description="Contract version. Answered with the same header.")],
) -> str:
    return version


COMMON = [Depends(contract_header)]


def _problem(description: str) -> dict:
    return {"model": Problem, "description": description, "content": {"application/problem+json": {}}}


def _add_health(app: FastAPI) -> None:
    @app.get("/health", response_model=Health, summary="Liveness", tags=["health"])
    async def health(): ...


def build_run_app(agent: RunAgent) -> FastAPI:
    rules = "\n".join(f"- {r}" for r in agent.caller_rules)
    tools = ", ".join(f"`{t}`" for t in agent.tools)
    description = f"""{agent.summary}

**Flow.** `POST {agent.path}` accepts a run and returns `202` at once. `GET {agent.path}/{{run_id}}` returns its status, and the result once finished. `DELETE` cancels it.

**Idempotency.** The same `run_id` and body returns the same run in its current state: `200` once it has finished, `202` while it is still running. The same `run_id` with a different body returns `409`. An unknown `run_id` returns `404`: the caller treats the run as lost and sends it again. Runs are kept for 24 hours, and the caller stores each result on first fetch.

**Honest failures.** A cut-off answer (`length`) or invalid output is `failed`, never a result. A failed run's `error.code` is one of `invalid_output`, `output_truncated`, `internal_error` or `budget_exceeded`. Tool failures are listed in `tool_errors`.

**Data.** The agent reads only through its tools, which code scopes to this run: {tools}. It has no web tool, and no way to write, send or change state. Document text is data, never instructions.

**What the caller does with the result.**
{rules}
"""
    app = FastAPI(title=f"PharmAlign {agent.title} agent", version=CONTRACT_VERSION, description=description)
    Run, Request = agent.run, agent.request
    example = poll_example(agent)
    ok = {"application/json": {"example": example}}

    @app.post(
        agent.path, response_model=Run, status_code=202, tags=["runs"], dependencies=COMMON,
        summary="Start a run", openapi_extra={"x-agent-tools": list(agent.tools)},
        responses={
            200: {"model": Run, "description": "The same run, in its current state: this run_id and body were sent before, and the run has finished."},
            202: {"model": Run, "description": "The same run, in its current state: this run_id and body were sent before, and the run is still running."},
            409: _problem("This run_id already exists with a different body."),
            422: _problem("The request breaks the contract."),
            500: _problem("The run wasn't accepted. Retry with the same run_id."),
        },
    )
    async def start(body: Annotated[Request, Body(openapi_examples={"example": {"value": agent.request_example}})]): ...

    @app.get(
        f"{agent.path}/{{run_id}}", response_model=Run, tags=["runs"], dependencies=COMMON,
        summary="Run status, and the result once finished",
        responses={200: {"content": ok, "description": "Status."}, 404: _problem("No run with this id, or it has expired.")},
    )
    async def poll(run_id: RunIdParam): ...

    @app.delete(
        f"{agent.path}/{{run_id}}", response_model=Run, tags=["runs"], dependencies=COMMON,
        summary="Cancel a run, such as one a newer run has replaced",
        responses={404: _problem("No run with this id, or it has expired.")},
    )
    async def cancel(run_id: RunIdParam): ...

    _add_health(app)
    return app


def build_assistant_app() -> FastAPI:
    tools = ", ".join(f"`{t}`" for t in ka.TOOLS)
    knowledge = ", ".join(f"`{t}`" for t in ka.KNOWLEDGE_TOOLS)
    description = f"""Answers questions from the knowledge base for anyone: laws, guidelines and the country's pack. Given a case, it also knows that case. It explains, and doesn't decide.

**Flow.** `POST /sessions` creates a session scoped to one person. With a `case_id` it is also scoped to that case. Without one it is knowledge-only. `POST /sessions/{{agent_session_id}}/turns` answers one message as server-sent events. Each event is one line, `data: {{json}}`, followed by a blank line, and the JSON is an `AssistantEvent` that carries its own `type`. There is no `event:` line. The stream ends when the agent closes it. Errors before the stream starts are problem JSON.

**Budget.** One turn may use {ka.TURN_MAX_MODEL_CALLS} model calls, {ka.TURN_MAX_TOOL_CALLS} tool calls and {ka.TURN_SECONDS} seconds. Past any of these the turn ends with a retryable `error` event (`budget_exceeded` or `deadline_exceeded`).

**Answers.** A `message` cites passages by anchor and quote. The caller checks each quote and labels it with the document title and legal status. When nothing qualifies, the agent sends `no_source` with the searches it tried, and doesn't answer from memory. It may suggest follow-up questions.

**Rules.** It says when something wasn't checked or wasn't saved. It can't change a status, waive a check, send anything or advise promoting. `draft_comment` is posted only when a person confirms it. Sessions live in memory, so after a restart the caller starts a new one.

**Data.** It reads only through its tools, which code scopes to this person, and to the case if there is one. A knowledge-only session gets only {knowledge}. A case session gets all of: {tools}.
"""
    app = FastAPI(title="PharmAlign Knowledge Assistant agent", version=CONTRACT_VERSION, description=description)

    @app.post(
        ka.SESSION_ROUTE, response_model=ka.SessionCreated, status_code=201, tags=["sessions"], dependencies=COMMON,
        summary="Create a session", openapi_extra={"x-agent-tools": list(ka.TOOLS)},
        responses={422: _problem("The request breaks the contract.")},
    )
    async def create(body: Annotated[ka.SessionCreate, Body(openapi_examples={"example": {"value": ka.SESSION_EXAMPLE}})]): ...

    @app.post(
        ka.TURN_ROUTE, tags=["sessions"], dependencies=COMMON,
        summary="One turn, as server-sent events", response_class=StreamingResponse,
        responses={
            200: {
                "description": "A stream of `AssistantEvent`.",
                "content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/AssistantEvent"}}},
            },
            404: _problem("Unknown session. Start a new one."),
            422: _problem("The request breaks the contract."),
        },
    )
    async def turn(agent_session_id: str, body: Annotated[ka.TurnRequest, Body(openapi_examples={"example": {"value": ka.TURN_EXAMPLE}})]): ...

    _add_health(app)
    return app


def spec_for(app: FastAPI, with_events: bool = False) -> dict:
    spec = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    for path in spec["paths"].values():
        for op in path.values():
            for code, response in op["responses"].items():  # errors are problem JSON only
                content = response.get("content", {})
                if "application/problem+json" in content and "application/json" in content:
                    content["application/problem+json"] = content.pop("application/json")
                elif code == "422" and "application/json" in content:  # FastAPI's default validation error
                    response["description"] = "The request breaks the contract."
                    response["content"] = {
                        "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                    }
    for unused in ("HTTPValidationError", "ValidationError"):
        spec["components"]["schemas"].pop(unused, None)
    if with_events:
        schema = ka.EVENT_ADAPTER.json_schema(ref_template="#/components/schemas/{model}")
        spec["components"]["schemas"].update(schema.pop("$defs"))
        schema["description"] = "One server-sent event."
        spec["components"]["schemas"]["AssistantEvent"] = schema
    return spec


def build_all() -> dict[str, str]:
    files = {f"agents/{a.name}.yaml": spec_for(build_run_app(a)) for a in RUN_AGENTS}
    files["agents/knowledge-assistant.yaml"] = spec_for(build_assistant_app(), with_events=True)
    files["server.yaml"] = build_openapi(SERVER_API)
    files["regulator.yaml"] = build_openapi(REGULATOR_API)
    header = "# Generated from pharmalign_contracts by scripts/generate_openapi.py. Do not edit.\n"
    return {
        name: header + yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=110)
        for name, spec in files.items()
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    built = build_all()
    if "--check" in sys.argv:
        stale = [n for n, text in built.items() if not (OUT / n).exists() or (OUT / n).read_text(encoding="utf-8") != text]
        if stale:
            print("Out of date:", ", ".join(stale))
            return 1
        print(f"{len(built)} specs up to date")
        return 0
    for name, text in built.items():
        (OUT / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"Wrote {len(built)} specs to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
