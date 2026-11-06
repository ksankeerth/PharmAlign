"""One case through the whole chain, with real agent services and no model key:
server, the 8 agent services over HTTP (scripted model), the server's MCP tools, then a person's decision.

Run it with the agents up (`scripts/run-local-agents.sh start`, from the repo root):
    uv run pytest -m chain -s tests/live/test_chain.py

It makes its own database (`pharmalign_chain_test`) and starts its own server on port 8000 in http agent mode.
To use a server that is already running (for example the compose stack, seeded with `python -m app.seed --reset`), set
PHARMALIGN_CHAIN_SERVER_URL and PHARMALIGN_CHAIN_DATABASE_URL.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import httpx
import pytest
from pharmalign_contracts.agents.run_agents import RUN_AGENTS
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.chain

SERVER_DIR = Path(__file__).resolve().parents[2]
CASE = "CASE-1042"
AGENT_PORTS = {"dossier-reader": 8101, "checklist-assessor": 8102, "sameness": 8103, "challenger": 8104,
               "coordinator": 8105, "reliance-watch": 8106, "gap-analyst": 8107, "knowledge-assistant": 8108}
CONTRACT = {"X-PharmAlign-Contract": "1.4.0"}
CHAIN_DB = "pharmalign_chain_test"
EXPECTED = {
    "SAFE-01": "pass", "DOOR-01": "pass", "DOOR-02": "pass", "QUAL-03": "pass", "ROUTE-01": "pass", "WATCH-01": "pass",
    "SAME-01": "look", "QUAL-07": "held", "LBL-02": "person_confirms", "LEGAL-01": "person_confirms", "PV-01": "person_confirms",
}
QUESTIONS = ("Which guidance applies to the presentation of a patient leaflet?",
             "What does the law require for exporting veterinary vaccines for fish farms?")


def agent_url(name: str) -> str:
    return f"http://127.0.0.1:{AGENT_PORTS[name]}"


def agents_up() -> bool:
    try:
        return all(httpx.get(agent_url(n) + "/health", timeout=2).status_code == 200 for n in AGENT_PORTS)
    except httpx.HTTPError:
        return False


def sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _fresh_database(url: str) -> None:
    parsed = make_url(url)
    admin = await asyncpg.connect(user=parsed.username, password=parsed.password, host=parsed.host, port=parsed.port, database="postgres")
    await admin.execute(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)')
    await admin.execute(f'CREATE DATABASE "{parsed.database}"')
    await admin.close()


def query(url: str, sql: str, *args) -> list[asyncpg.Record]:
    async def go():
        conn = await asyncpg.connect(sync_url(url))
        try:
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(go())


@pytest.fixture(scope="module")
def stack():
    """(server base URL, database URL). Starts and stops the server unless one is given."""
    if not agents_up():
        pytest.skip("the 8 agents are not up on ports 8101 to 8108 (scripts/run-local-agents.sh start)")
    given = os.environ.get("PHARMALIGN_CHAIN_SERVER_URL")
    if given:
        yield given.rstrip("/"), os.environ["PHARMALIGN_CHAIN_DATABASE_URL"]
        return
    base = make_url(os.environ.get("PHARMALIGN_TEST_DATABASE_URL", "postgresql+asyncpg://pharmalign:pharmalign@localhost:5544/pharmalign_test"))
    url = base.set(database=CHAIN_DB).render_as_string(hide_password=False)
    asyncio.run(_fresh_database(url))
    env = {**os.environ, "PHARMALIGN_ENV": "simulation", "PHARMALIGN_EMBEDDER": "hash", "PHARMALIGN_DATABASE_URL": url, "PHARMALIGN_AGENT_MODE": "http"}
    env.pop("VIRTUAL_ENV", None)
    for name in AGENT_PORTS:
        env["PHARMALIGN_AGENT_URL_" + name.upper().replace("-", "_")] = agent_url(name)
    for step in ([sys.executable, "-m", "alembic", "upgrade", "head"], [sys.executable, "-m", "app.seed", "--reset"]):
        subprocess.run(step, cwd=SERVER_DIR, env=env, check=True, capture_output=True)
    log = open(SERVER_DIR / ".chain-server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "app.main:create_app", "--port", "8000"], cwd=SERVER_DIR, env=env, stdout=log, stderr=log)
    try:
        for _ in range(60):
            try:
                if httpx.get("http://127.0.0.1:8000/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            pytest.fail("the server did not start; see server/.chain-server.log")
        yield "http://127.0.0.1:8000", url
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        log.close()


class Api:
    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, who: str, body=None, expect: int = 0) -> dict:
        r = httpx.request(method, f"{self.base}/api/v1{path}", headers={"Authorization": f"Bearer {who}", "Idempotency-Key": uuid.uuid4().hex}, json=body, timeout=60)
        if expect:
            assert r.status_code == expect, r.text
        else:
            assert r.status_code < 300, f"{method} {path}: {r.status_code} {r.text[:500]}"
        return r.json() if r.content else {}

    def wait(self, what: str, done, seconds: float = 120):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if (found := done()):
                return found
            time.sleep(0.5)
        pytest.fail(f"timed out waiting for {what}")


def detail(api: Api, who: str, check_id: str) -> dict:
    items = api.call("GET", f"/cases/{CASE}/checks", who)["items"]
    return api.call("GET", f"/check-results/{next(c['id'] for c in items if c['check_id'] == check_id)}", who)


def test_one_case_through_real_agents(stack):
    server, db = stack
    api = Api(server)
    started = time.monotonic()

    # The pipeline: every agent call goes over HTTP to a service, and its tools come from the server's MCP endpoint.
    run = api.call("POST", f"/cases/{CASE}/pipeline-runs", "p-alex", {"reason": "chain test"})
    api.wait("the pipeline", lambda: api.call("GET", f"/pipeline-runs/{run['id']}", "p-alex")["status"] == "completed")
    checks = {c["check_id"]: c["status"] for c in api.call("GET", f"/cases/{CASE}/checks", "p-alex")["items"]}
    assert checks == EXPECTED

    rows = query(db, "SELECT id, agent, status, (doc->'usage'->>'tool_calls')::int AS tool_calls FROM agent_run WHERE case_id = $1 AND generation = $2 AND is_current",
                 CASE, run["generation"])
    assert {r["agent"] for r in rows} == {"dossier-reader", "checklist-assessor", "sameness", "challenger", "coordinator"}
    assert all(r["status"] == "completed" for r in rows), [(r["id"], r["status"]) for r in rows]
    paths = {a.name: a.path for a in RUN_AGENTS}
    tool_calls = 0
    for r in rows:
        logged = query(db, "SELECT tool, outcome FROM agent_tool_log WHERE run_id = $1", r["id"])
        assert logged and all(x["outcome"] == "ok" for x in logged), f"{r['id']} has no tool calls from its own token"
        assert len(logged) == r["tool_calls"], f"{r['id']}: the agent counted {r['tool_calls']} tool calls, the log has {len(logged)}"
        tool_calls += len(logged)
        # The agent service itself holds the run: a replayed answer would not.
        held = httpx.get(agent_url(r["agent"]) + f"{paths[r['agent']]}/{r['id']}", headers=CONTRACT, timeout=5)
        assert held.status_code == 200 and held.json()["status"] == "completed", r["id"]
        assert held.json()["agent"]["version"] != "replay-1"

    # The brief: from the Coordinator, with the input fingerprint of the results it was asked about.
    recommendation = next(x for x in api.call("GET", f"/cases/{CASE}/recommendations", "p-alex")["items"] if x["is_current"])
    brief = next(r for r in rows if r["agent"] == "coordinator")
    assert recommendation["choice"] == "hold_ask_company" and recommendation["input_fingerprint"].startswith("sha256:")
    assert "-brief-" in brief["id"]

    # People: a Case Officer confirms and promotes, a Decision Maker (who did not waive) signs.
    for check_id in ("LBL-02", "LEGAL-01", "PV-01", "SAME-01"):
        c = detail(api, "p-alex", check_id)
        api.call("POST", f"/check-results/{c['id']}/confirm", "p-alex", {"fingerprint": c["fingerprint"], "note": "Read the evidence."})
    held_check = detail(api, "p-alex", "QUAL-07")
    api.call("POST", f"/check-results/{held_check['id']}/waive", "p-dana", {"fingerprint": held_check["fingerprint"], "reason": "Accepted for this demonstration."})
    api.call("POST", "/threads/th-1042-1/resolve", "p-alex", {"note": "Settled."})
    api.call("POST", "/questions/q-1042-1/close", "p-alex", {"reason": "Not needed now."})

    def ready():
        check = api.call("GET", f"/cases/{CASE}/promotion-check", "p-alex")
        return check if check["can_promote"] else None

    promotion = api.wait("the case to be ready to promote (a new brief follows each confirmation)", ready, seconds=60)
    api.call("POST", f"/cases/{CASE}/promote", "p-alex", {"fingerprint": promotion["fingerprint"]})
    package = api.call("GET", f"/cases/{CASE}/signing-package", "p-noor")
    assert not [g for g in package["guards"] if not g["passed"]]
    decision = api.call("POST", f"/cases/{CASE}/decisions", "p-noor", {
        "type": "grant", "fingerprint": package["fingerprint"], "recommendation_version": package["recommendation"]["version"],
        "accepts_recommendation": False, "reason": "The open points were settled by people.",
    })

    def recorded():
        d = api.call("GET", f"/decisions/{decision['id']}", "p-noor")
        return d if d["record_status"] in ("acknowledged", "recorded") else None

    done = api.wait("the decision to be recorded", recorded, seconds=60)
    key = f"idem-{decision['id']}"
    assert len(query(db, "SELECT id FROM mock_regulator_proposal WHERE idempotency_key = $1", key)) == 1, "recorded once at the mock regulator"

    # The Knowledge Assistant, knowledge-only, through the real agent.
    session = api.call("POST", "/assistant/sessions", "p-alex", {})
    assert session["case_id"] is None

    def ask(text: str) -> list[dict]:
        r = httpx.post(f"{server}/api/v1/assistant/sessions/{session['id']}/turns", headers={"Authorization": "Bearer p-alex", "Idempotency-Key": uuid.uuid4().hex},
                       json={"message": text}, timeout=60)
        assert r.status_code == 200, r.text
        return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]

    cited, none = ask(QUESTIONS[0]), ask(QUESTIONS[1])
    assert not [e for e in cited + none if e["type"] == "error"]
    message = next(e for e in cited if e["type"] == "message")
    assert message["citations"] and message["citations"][0]["anchor"] == "E-S06:v1:c0001" and message["citations"][0]["legal_status"] == "in_force"
    assert any(e["type"] == "no_source" for e in none) and not any(e["type"] == "message" for e in none)
    assistant_calls = len(query(db, "SELECT id FROM agent_tool_log WHERE run_id = $1", session["id"]))
    assert assistant_calls >= 3

    total = time.monotonic() - started
    report = {"agent_runs": len(rows) + 1, "tool_calls": tool_calls + assistant_calls, "seconds": round(total, 1),
              "pipeline_runs": len(rows), "pipeline_tool_calls": tool_calls, "assistant_session_tool_calls": assistant_calls}
    print("\nchain:", json.dumps(report))
    out = SERVER_DIR.parent / ".run"
    out.mkdir(exist_ok=True)
    (out / "chain-report.json").write_text(json.dumps(report, indent=2) + "\n")
