"""An agent that is off by configuration shows as off in the health views and never fails readiness."""
from types import SimpleNamespace

import pytest

from ..conftest import LiveAgents
from ..packs.conftest import run_jobs
from .conftest import headers, seeded  # noqa: F401  (the seeded fixture)

pytestmark = pytest.mark.seeded
ADMIN = headers("p-jordan")
OFF = "challenger,sameness,reliance-watch"


@pytest.fixture
async def off_http(make_app):
    from ..conftest import client_for

    app = make_app(settings={"agents_off": OFF})
    async with client_for(app) as client:
        client.app = app
        yield client


class AskedAgents(LiveAgents):
    """Remembers who was asked, because an off agent must not be."""

    def __init__(self, down=()):
        super().__init__(down)
        self.asked = []

    async def alive(self, agent):
        self.asked.append(agent)
        return await super().alive(agent)


async def test_off_agents_show_as_off_and_the_others_are_asked(off_http):
    off_http.app.state.rt.agents = agents = AskedAgents(down=("sameness",))  # an off agent that would be down is still just off
    items = (await off_http.get("/api/v1/admin/agents/health", headers=ADMIN)).json()["items"]
    status = {h["agent"]: h["status"] for h in items}
    assert {n for n, s in status.items() if s == "off"} == set(OFF.split(","))
    assert {n for n, s in status.items() if s == "ok"} == set(agents.urls) - set(OFF.split(","))
    assert not set(agents.asked) & set(OFF.split(","))


async def test_off_agents_show_as_off_when_answers_come_from_recordings(off_http):
    items = (await off_http.get("/api/v1/admin/agents/health", headers=ADMIN)).json()["items"]
    status = {h["agent"]: h["status"] for h in items}
    assert status and {n for n, s in status.items() if s == "off"} == set(OFF.split(",")) & set(status)
    assert all(s != "down" for s in status.values())


async def test_an_off_agent_does_not_make_the_server_degraded(off_http):
    off_http.app.state.rt.agents = LiveAgents()
    off_http.app.state.rt.worker = SimpleNamespace(done=lambda: False)  # the worker is off in tests; this stands in for a running one
    details = (await off_http.get("/health/details", headers=ADMIN)).json()
    assert details["status"] == "ok"
    assert {a["agent"] for a in details["agents"] if a["status"] == "off"} == set(OFF.split(","))
    assert not [a for a in details["agents"] if a["status"] in ("down", "degraded")]


async def test_a_down_agent_that_is_on_still_degrades_the_server(off_http):
    off_http.app.state.rt.agents = LiveAgents(down=("coordinator",))
    off_http.app.state.rt.worker = SimpleNamespace(done=lambda: False)
    details = (await off_http.get("/health/details", headers=ADMIN)).json()
    assert [a["agent"] for a in details["agents"] if a["status"] == "down"] == ["coordinator"] and details["status"] == "degraded"


async def test_readiness_ignores_off_agents(off_http, clock):
    off_http.app.state.rt.agents = LiveAgents(down=("challenger",))
    total = len(off_http.app.state.rt.agents.urls)
    clock.advance(minutes=1)
    await off_http.post("/api/v1/workspaces/ws-om/readiness-runs", headers=ADMIN)
    await run_jobs(off_http.app, clock)
    run = (await off_http.get("/api/v1/workspaces/ws-om/readiness", headers=ADMIN)).json()
    agents = next(c for c in run["checks"] if c["key"] == "agents")
    assert agents["status"] == "pass" and agents["detail"] == f"{total - 3} of {total - 3} agents report ok. 3 off by configuration."
    setup = (await off_http.get("/api/v1/workspaces/ws-om/setup", headers=ADMIN)).json()
    assert "The challenger agent is down." not in str(setup)
