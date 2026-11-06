import copy

import pytest
from pharmalign_agent_common import AgentSpec, Settings, agent_spec
from pharmalign_agent_common.testing import FakeMcpServer
from pharmalign_contracts.agents.run_agents import RUN_AGENTS, DossierReadRequest, DossierReadResult
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION

PAGE_QUOTE = "a shelf life of 36 months"

READER = next(a for a in RUN_AGENTS if a.name == "dossier-reader")


def _reject_unasked(request: DossierReadRequest, result: DossierReadResult) -> None:
    asked = {f.key for f in request.fact_keys}
    for fact in result.facts:
        if fact.key not in asked:
            raise ValueError(f"fact '{fact.key}' wasn't asked for")


@pytest.fixture
def spec() -> AgentSpec:
    return agent_spec(
        "dossier-reader",
        adk_name="dossier_reader",
        result_model=DossierReadResult,
        instruction="Read the documents and answer in JSON.",
        default_model="fake-default-model",
        build_message=lambda req: f"Read {len(req.documents)} documents for {len(req.fact_keys)} facts.",
        check_result=_reject_unasked,
    )


@pytest.fixture
def headers() -> dict:
    return {CONTRACT_HEADER: CONTRACT_VERSION}


@pytest.fixture
def settings() -> Settings:
    return Settings(mcp_url="http://127.0.0.1:1/mcp")


@pytest.fixture
def mcp():
    tools = {
        "read_page": {"anchor": "d-17:v1:p2", "text": f"The product has {PAGE_QUOTE}."},
        "verify_quote": {"ok": True},
    }
    with FakeMcpServer(tools) as server:
        yield server


@pytest.fixture
def mcp_settings(mcp) -> Settings:
    return Settings(mcp_url=mcp.url)


def make_body(run_id: str = "case-1042-s2-reader-g1-a1", **budget) -> dict:
    body = copy.deepcopy(READER.request_example)
    body["run_id"] = run_id
    body["budget"].update(budget)
    return body


def good_result(key: str = "shelf_life_months") -> dict:
    return {
        "document_map": [{"doc_id": "d-17", "module": "3.2.P.8", "basis": "Title"}],
        "facts": [
            {"key": key, "status": "found", "value": "36 months", "number": 36, "unit": "months",
             "evidence": [{"anchor": "d-17:v1:p2", "quote": PAGE_QUOTE}]},
        ],
        "issues": [],
    }
