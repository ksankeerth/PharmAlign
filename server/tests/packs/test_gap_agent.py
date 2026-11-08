"""The Gap Analyst is asked to run an analysis, and the code's candidates stand in when it can't answer.

In the seed, ws-ae has signals s-1 to s-5, and gap-01 to gap-03 cover s-3, s-2 and s-5, so s-1 and s-4 are fresh."""
from datetime import timedelta

import pytest
from pharmalign_contracts.agents.run_agents import GapAnalysisRequest
from pharmalign_contracts.common import Evidence
from sqlalchemy import delete, select

from app.db import make_sessions
from app.mcp.token import read
from app.pipeline import gap_run
from app.pipeline.client import reply_from
from app.pipeline.citations import CitationContext, check_quote
from app.pipeline.replay import ReplayScript
from app.pipeline.sources import kb_chunks
from app.tables import ROW, AuditEventRow, JobRow

from .conftest import headers, run_jobs, run_until_done

pytestmark = pytest.mark.seeded

SEED_GAPS = ["gap-01", "gap-02", "gap-03"]
FRESH = ["s-1", "s-4"]
SNAPSHOT = "snap-ae-14"
RULE = "Pack rule: LBL-02."


@pytest.fixture(autouse=True)
async def clean_gaps(engine):
    """Each test starts from the seed's three gaps, with no analysis, run or job left by another."""
    async with make_sessions(engine)() as db, db.begin():
        await db.execute(delete(ROW["gap"]).where(ROW["gap"].id.not_in(SEED_GAPS)))
        await db.execute(delete(ROW["gap_analysis"]))
        await db.execute(delete(ROW["agent_run"]).where(ROW["agent_run"].agent == "gap-analyst"))
        await db.execute(delete(JobRow).where(JobRow.kind.in_((gap_run.AGENT_STEP, gap_run.FINISH_ANALYSIS, gap_run.CANCEL_REMOTE))))


def agents(http):
    return http.app.state.rt.agents


async def start(http) -> str:
    started = await http.post("/api/v1/workspaces/ws-ae/gap-analyses", json={"signal_ids": []}, headers=headers("p-sam"))
    assert started.status_code == 202 and started.json()["status"] == "running"
    return started.json()["id"]


async def analyse(http, clock, seconds: int = 5) -> dict:
    return await run_until_done(http, clock, await start(http), seconds)


async def new_gaps(http) -> list[dict]:
    items = (await http.get("/api/v1/workspaces/ws-ae/gaps", headers=headers("p-sam"))).json()["items"]
    return sorted((g for g in items if g["id"] not in SEED_GAPS), key=lambda g: g["signal_ids"])


async def completion_audit(engine, analysis_id: str):
    async with make_sessions(engine)() as db:
        rows = (await db.execute(select(AuditEventRow).where(AuditEventRow.object_id == analysis_id, AuditEventRow.action.like("gap_analysis_%"))
                                 .order_by(AuditEventRow.seq))).scalars().all()
    done = [r for r in rows if r.action != "gap_analysis_started"]
    assert len(done) == 1, [r.action for r in rows]
    return done[0]


async def run_row(engine, analysis_id: str):
    async with make_sessions(engine)() as db:
        row = await db.get(ROW["agent_run"], f"{analysis_id}-a1")
    return row.status, row.doc.get("error_code"), row.case_id, row.doc


async def real_example(engine) -> Evidence:
    """A quote from an in-force chunk of the active snapshot, as the citation check accepts it. Found by trying chunks in the seeded library."""
    item, chunk = ROW["kb_snapshot_item"], ROW["chunk"]
    async with make_sessions(engine)() as db:
        ids = (await db.scalars(select(chunk.id).join(item, item.document_version_id == chunk.document_version_id)
                                .where(item.snapshot_id == SNAPSHOT, item.doc["legal_status"].astext == "in_force"))).all()
        chunks = await kb_chunks(db, "ws-ae", SNAPSHOT, ids)
    for anchor, c in sorted(chunks.items()):
        words = c.text.split()
        if len(words) >= 14:
            e = Evidence(anchor=anchor, quote=" ".join(words[2:14]))
            if check_quote(e, CitationContext(chunks)) is None:
                return e
    raise AssertionError("no in-force chunk in the seeded snapshot can be quoted")


def candidate(**fields) -> dict:
    return {"title": "A policy gap", "kind": "policy", "signal_ids": ["s-1"], "pack_rule": None, "description": "There is no pack rule for this.",
            "global_examples": [], "pipeline_node": None, "readiness_dimension": None, "suggested_next_step": None, **fields}


# ---- the call


async def test_the_agent_is_started_with_the_fresh_signals_and_a_token_the_tool_server_accepts(http, clock, engine):
    agents(http).script("ws-ae", "gap-analyst", ReplayScript("slow"))
    analysis_id = await start(http)
    await run_jobs(http.app, clock)
    await run_jobs(http.app, clock)
    assert (await http.get(f"/api/v1/gap-analyses/{analysis_id}", headers=headers("p-sam"))).json()["status"] == "running"
    run_id = f"{analysis_id}-a1"
    body = agents(http).runs[run_id].body
    request = GapAnalysisRequest.model_validate(body)
    assert [s.id for s in request.signals] == FRESH
    assert request.pipeline_nodes == gap_run.PIPELINE_NODES and request.readiness_dimensions == gap_run.READINESS_DIMENSIONS
    assert request.tenant == "ws-ae" and request.mode == "simulation" and request.priority == "batch"
    assert request.versions.kb_snapshot == SNAPSHOT and request.versions.pack == "pv-ae-3"
    status, _, case_id, _ = await run_row(engine, analysis_id)
    assert status == "running" and case_id is None
    scope = read(http.app.state.rt.settings, request.run_token)
    assert (scope.case, scope.agent, scope.tenant, scope.id, scope.snapshot) == (None, "gap-analyst", "ws-ae", run_id, SNAPSHOT)
    assert await http.app.state.tools.refusal(scope) is None


# ---- the agent's answer


async def test_an_accepted_answer_becomes_candidates(http, clock, engine):
    example = await real_example(engine)
    agents(http).answer("ws-ae", "gap-analyst", {"candidates": [
        candidate(title="No sameness rule", signal_ids=["s-1", "s-3", "s-nope"], pack_rule="LBL-02", description="The leaflet check is not backed by a source.",
                  global_examples=[example.model_dump()], pipeline_node="policy", readiness_dimension="AI policy and policy for AI",
                  suggested_next_step="Ask the legal team."),
        candidate(title="Waivers", kind="standards", signal_ids=["s-4", "s-1"], pipeline_node="not-a-node", readiness_dimension="Not a dimension"),
        candidate(title="Already covered", signal_ids=["s-3", "s-2"]),
    ]})
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["finished_at"] and done["candidates_created"] == 2
    first, second = await new_gaps(http)
    assert (first["title"], first["kind"], first["status"], first["signal_ids"]) == ("No sameness rule", "policy", "candidate", ["s-1"])
    assert first["description"] == f"The leaflet check is not backed by a source. {RULE}"
    assert first["global_examples"] == [example.model_dump()]
    assert (first["pipeline_node"], first["readiness_dimension"], first["suggested_next_step"]) == ("policy", "AI policy and policy for AI", "Ask the legal team.")
    # s-1 went to the first candidate, so the second keeps only s-4. A node or dimension that isn't on the lists is not kept.
    assert (second["title"], second["kind"], second["signal_ids"]) == ("Waivers", "standards", ["s-4"])
    assert second["description"] == "There is no pack rule for this." and second["pipeline_node"] is None and second["readiness_dimension"] is None
    audit = await completion_audit(engine, analysis_id)
    assert (audit.actor_type, audit.actor_id) == ("agent", "gap-analyst")
    assert audit.after == {"candidates_created": 2, "run_id": f"{analysis_id}-a1", "examples_dropped": 0}
    status, error, case_id, doc = await run_row(engine, analysis_id)
    assert (status, error, case_id) == ("completed", None, None) and doc["duration_ms"] is not None


async def test_an_example_that_fails_the_citation_check_is_dropped(http, clock, engine):
    good = await real_example(engine)
    words = good.quote.split()
    wrong_quote = Evidence(anchor=good.anchor, quote="This sentence is made up and appears nowhere in the library at all today")
    unknown = Evidence(anchor="Z-99:v1:c0001", quote=good.quote)
    assert len(words) >= 10
    agents(http).answer("ws-ae", "gap-analyst", {"candidates": [
        candidate(signal_ids=["s-1"], global_examples=[wrong_quote.model_dump(), good.model_dump(), unknown.model_dump()]),
    ]})
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 1
    (gap,) = await new_gaps(http)
    assert gap["global_examples"] == [good.model_dump()]
    assert (await completion_audit(engine, analysis_id)).after["examples_dropped"] == 2


# ---- the fallback


# The replay's own error codes are not in the contract's list, so its failed runs are refused as schema_invalid.
@pytest.mark.parametrize("kind, code", [("failed", "schema_invalid"), ("truncated", "schema_invalid"), ("invalid", "schema_invalid"), ("cut_off", "body_not_json")])
async def test_an_agent_that_fails_leaves_the_codes_candidates(http, clock, engine, kind, code):
    agents(http).answer("ws-ae", "gap-analyst", {"candidates": [candidate(title="Never used")]})
    agents(http).script("ws-ae", "gap-analyst", ReplayScript(kind))
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["finished_at"] and done["candidates_created"] == 2
    gaps = await new_gaps(http)
    assert [g["signal_ids"] for g in gaps] == [["s-1"], ["s-4"]] and "Never used" not in [g["title"] for g in gaps]
    audit = await completion_audit(engine, analysis_id)
    assert (audit.actor_type, audit.actor_id) == ("system", "code")
    assert audit.after == {"candidates_created": 2, "fallback": code}
    status, error, _, _ = await run_row(engine, analysis_id)
    assert (status, error) == ("failed", code)


async def test_an_agent_with_no_stored_answer_leaves_the_codes_candidates(http, clock, engine):
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    audit = await completion_audit(engine, analysis_id)
    assert (audit.actor_id, audit.after["fallback"]) == ("code", "schema_invalid")


@pytest.mark.parametrize("error, reason, code", [("internal_error", "error", "internal_error"), ("output_truncated", "length", "truncated")])
async def test_a_run_that_ends_failed_leaves_the_codes_candidates(http, clock, engine, error, reason, code):
    class Failing:
        async def start(self, agent, request):
            body = {"run_id": request["run_id"], "status": "failed", "finish_reason": reason, "finished_at": clock.now().isoformat(),
                    "agent": {"name": agent, "version": "v1", "prompt_hash": "sha256:0123456789abcdef"},
                    "error": {"code": error, "message": "It failed.", "retryable": False}}
            return reply_from(agent, 200, body)

    http.app.state.rt.agents = Failing()
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    assert (await completion_audit(engine, analysis_id)).after == {"candidates_created": 2, "fallback": code}
    assert (await run_row(engine, analysis_id))[:2] == ("failed", code)


async def test_an_answer_for_another_run_is_not_used(http, clock, engine):
    class Mixed:
        async def start(self, agent, request):
            body = {"run_id": "some-other-run", "status": "completed", "finish_reason": "stop", "finished_at": clock.now().isoformat(),
                    "agent": {"name": agent, "version": "v1", "prompt_hash": "sha256:0123456789abcdef"},
                    "result": {"candidates": [candidate(title="Not ours")]}}
            return reply_from(agent, 200, body)

    http.app.state.rt.agents = Mixed()
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    assert "Not ours" not in [g["title"] for g in await new_gaps(http)]
    assert (await completion_audit(engine, analysis_id)).after["fallback"] == "wrong_run"


async def test_an_agent_that_never_finishes_is_given_up_on_at_the_deadline(http, clock, engine):
    agents(http).script("ws-ae", "gap-analyst", ReplayScript("slow"))
    analysis_id = await start(http)
    for _ in range(3):  # the 210 second deadline has not passed yet
        await run_jobs(http.app, clock, seconds=60)
    assert (await http.get(f"/api/v1/gap-analyses/{analysis_id}", headers=headers("p-sam"))).json()["status"] == "running"
    done = await run_until_done(http, clock, analysis_id, seconds=60)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    audit = await completion_audit(engine, analysis_id)
    assert (audit.actor_type, audit.actor_id, audit.after["fallback"]) == ("system", "code", "deadline_passed")
    assert (await run_row(engine, analysis_id))[:2] == ("lost", "deadline_passed")
    await run_jobs(http.app, clock)  # the agent is told to stop
    assert agents(http).calls_to("cancel", f"{analysis_id}-a1") == 1


async def test_an_agent_that_does_not_answer_is_given_up_on_at_the_deadline(http, clock, engine):
    class Silent:
        async def start(self, agent, request):
            raise ConnectionError("down")

    http.app.state.rt.agents = Silent()
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id, seconds=60)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    assert (await completion_audit(engine, analysis_id)).after["fallback"] == "agent_unreachable"
    assert (await run_row(engine, analysis_id))[:2] == ("lost", "agent_unreachable")


async def test_a_forgotten_run_is_posted_again_once(http, clock, engine):
    agents(http).answer("ws-ae", "gap-analyst", {"candidates": [candidate(title="Found it again")]})
    agents(http).script("ws-ae", "gap-analyst", ReplayScript("404"))
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 1
    assert (await completion_audit(engine, analysis_id)).actor_id == "gap-analyst"
    assert agents(http).calls_to("start", f"{analysis_id}-a1") == 2


async def test_a_run_forgotten_twice_is_lost(http, clock, engine):
    agents(http).answer("ws-ae", "gap-analyst", {"candidates": [candidate(title="Never used")]})
    agents(http).script("ws-ae", "gap-analyst", ReplayScript("404", times=2))
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    assert (await completion_audit(engine, analysis_id)).after["fallback"] == "lost"
    assert (await run_row(engine, analysis_id))[:2] == ("lost", "lost")


async def test_a_step_that_breaks_still_ends_the_analysis(http, clock, engine, monkeypatch):
    async def broken(ctx, payload, reply):
        raise RuntimeError("boom")

    monkeypatch.setattr(gap_run, "_take", broken)
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    audit = await completion_audit(engine, analysis_id)
    assert (audit.actor_id, audit.after["fallback"]) == ("code", "step_failed")


# ---- the agent is off, or can't be asked


async def test_with_the_agent_off_it_is_never_called(http, clock, engine):
    http.app.state.rt.settings.agents_off = "gap-analyst"
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["finished_at"] and done["candidates_created"] == 2
    assert agents(http).calls == [] and agents(http).runs == {}
    audit = await completion_audit(engine, analysis_id)
    assert (audit.actor_type, audit.actor_id) == ("system", "code")
    assert audit.after == {"candidates_created": 2, "fallback": "agents_off"}
    async with make_sessions(engine)() as db:
        assert await db.get(ROW["agent_run"], f"{analysis_id}-a1") is None


async def test_an_agent_switched_off_after_the_run_was_queued_is_not_called(http, clock, engine):
    analysis_id = await start(http)
    http.app.state.rt.settings.agents_off = "gap-analyst"
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    assert agents(http).calls == []
    assert (await completion_audit(engine, analysis_id)).after["fallback"] == "agents_off"
    assert (await run_row(engine, analysis_id))[:2] == ("cancelled", "agents_off")


async def test_an_agent_with_no_address_is_not_asked(http, clock, engine):
    class Unaddressed:
        urls = {"gap-analyst": None}

        async def start(self, agent, request):
            raise AssertionError("not configured, so not called")

    http.app.state.rt.agents = Unaddressed()
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 2
    assert (await completion_audit(engine, analysis_id)).after["fallback"] == "agent_not_configured"


async def test_an_analysis_with_nothing_fresh_does_not_ask_the_agent(http, clock, engine):
    first = await analyse(http, clock)
    assert first["candidates_created"] == 2
    calls = len(agents(http).calls)
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "completed" and done["candidates_created"] == 0
    assert len(agents(http).calls) == calls
    assert (await completion_audit(engine, analysis_id)).after["fallback"] == "no_fresh_signals"


async def test_when_even_the_fallback_breaks_the_analysis_is_marked_failed(http, clock, engine, monkeypatch):
    from app.handlers import gaps

    async def broken(ctx, gap):
        raise RuntimeError("boom")

    http.app.state.rt.settings.agents_off = "gap-analyst"
    monkeypatch.setattr(gaps, "add_candidate", broken)
    analysis_id = await start(http)
    done = await run_until_done(http, clock, analysis_id)
    assert done["status"] == "failed" and done["finished_at"] and done["candidates_created"] == 0
    assert await new_gaps(http) == []
    assert (await completion_audit(engine, analysis_id)).action == "gap_analysis_failed"
