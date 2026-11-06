import json

import httpx
import pytest
from pharmalign_agent_common import Settings, create_app
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION

from conftest import good_result, make_body
from test_routes import client_for, finished

RUN = "case-1042-s2-reader-g1-a1"


def write_script(root, case_id: str) -> None:
    folder = root / "scripted" / "dossier-reader"
    folder.mkdir(parents=True)
    (folder / "dossier-reader.json").write_text(json.dumps({
        "case": case_id,
        "calls": [{"tool": "read_page", "args": {"doc_id": "d-17", "page": 2}}],
        "answer": good_result(),
    }))


@pytest.fixture
def scripted(tmp_path, monkeypatch, mcp):
    monkeypatch.setenv("PHARMALIGN_DEMO_DIR", str(tmp_path))
    return Settings(mcp_url=mcp.url, model_mode="scripted")


async def test_scripted_run_makes_the_real_tool_call_then_answers(spec, scripted, headers, mcp, tmp_path):
    body = make_body()
    write_script(tmp_path, body["case"]["case_id"])
    async with client_for(create_app(spec, scripted)) as client:
        assert (await client.post("/dossier-reads", json=body, headers=headers)).status_code == 202
        done = (await finished(client, headers, RUN)).json()
    assert done["status"] == "completed", done
    assert [c.name for c in mcp.calls] == ["read_page"] and mcp.calls[0].authorization == f"Bearer {body['run_token']}"
    assert done["result"]["facts"][0]["evidence"][0]["anchor"] == "d-17:v1:p2"
    assert done["usage"]["tool_calls"] == 1 and done["model_calls"][0]["model"] == "scripted"


def write_recording(root, case_id: str) -> None:
    folder = root / "replay" / "runs" / case_id
    folder.mkdir(parents=True)
    (folder / "dossier-reader.json").write_text(json.dumps(good_result()))


async def test_a_request_with_no_script_gives_the_recorded_answer_after_reading_what_it_cites(spec, scripted, headers, mcp, tmp_path):
    body = make_body()
    write_recording(tmp_path, body["case"]["case_id"])
    write_script(tmp_path, "CASE-OTHER")  # a script for another case is not this case's
    async with client_for(create_app(spec, scripted)) as client:
        await client.post("/dossier-reads", json=body, headers=headers)
        done = (await finished(client, headers, RUN)).json()
    assert done["status"] == "completed", done
    assert [(c.name, c.arguments) for c in mcp.calls] == [("read_page", {"doc_id": "d-17", "page": 2})]
    assert done["result"]["facts"][0]["evidence"][0]["anchor"] == "d-17:v1:p2"


async def test_a_request_with_no_script_fails_honestly(spec, scripted, headers, mcp):
    async with client_for(create_app(spec, scripted)) as client:
        await client.post("/dossier-reads", json=make_body(), headers=headers)
        done = (await finished(client, headers, RUN)).json()
    assert done["status"] == "failed" and done["error"]["code"] == "internal_error"
    assert "no script for this request" in done["error"]["message"] and "no recorded answer" in done["error"]["message"]
    assert mcp.calls == []


async def test_scripted_mode_refuses_a_live_request(spec, scripted, headers):
    body = make_body()
    body["mode"] = "live"
    body["execution_profile"]["data_class"] = "confidential"
    async with client_for(create_app(spec, scripted)) as client:
        r = await client.post("/dossier-reads", json=body, headers=headers)
    assert r.status_code == 422 and r.json()["code"] == "scripted_not_for_live"


def test_scripted_mode_is_refused_on_cloud_run():
    with pytest.raises(RuntimeError):
        Settings.from_env({"PHARMALIGN_MODEL_MODE": "scripted", "K_SERVICE": "agent"})
    assert Settings.from_env({"PHARMALIGN_MODEL_MODE": "scripted"}).model_mode == "scripted"


def test_telemetry_export_is_off_unless_set():
    assert Settings.from_env({}).otel_to_cloud is False
    assert Settings.from_env({"PHARMALIGN_OTEL_TO_CLOUD": "true"}).otel_to_cloud is True
