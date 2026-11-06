import asyncio

from pharmalign_agent_common import run_agent
from pharmalign_agent_common.testing import FakeModel, call_tool, say_json
from pharmalign_contracts.agents.run_agents import DossierReadRequest

from conftest import good_result, make_body


def request(run_id: str, token: str) -> DossierReadRequest:
    body = make_body(run_id)
    body["run_token"] = token
    return DossierReadRequest.model_validate(body)


def script(run_id: str) -> FakeModel:
    # the doc_id argument names the run, so a call can be matched to its token
    steps = [call_tool("read_page", doc_id=run_id, page=1), call_tool("read_page", doc_id=run_id, page=2)]
    return FakeModel(script=steps + [say_json(good_result())])


async def test_each_run_sends_its_own_token(spec, mcp, mcp_settings):
    tokens = {"run-aaa": "token-for-run-aaa", "run-bbb": "token-for-run-bbb", "run-ccc": "token-for-run-ccc"}
    await asyncio.gather(
        *(run_agent(spec, request(r, t), settings=mcp_settings, model=script(r)) for r, t in tokens.items())
    )
    assert len(mcp.calls) == 6
    for call in mcp.calls:
        assert call.authorization == f"Bearer {tokens[call.arguments['doc_id']]}"


async def test_a_later_run_does_not_reuse_an_earlier_token(spec, mcp, mcp_settings):
    await run_agent(spec, request("run-one", "token-one"), settings=mcp_settings, model=script("run-one"))
    await run_agent(spec, request("run-two", "token-two"), settings=mcp_settings, model=script("run-two"))
    assert {c.authorization for c in mcp.calls_to("read_page") if c.arguments["doc_id"] == "run-two"} == {"Bearer token-two"}
    assert all("token-one" not in (c.authorization or "") for c in mcp.calls if c.arguments["doc_id"] == "run-two")
