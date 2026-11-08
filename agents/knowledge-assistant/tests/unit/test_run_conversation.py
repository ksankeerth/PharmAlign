"""eval/run_conversation.py end to end with a fake classifier: no live call."""
import importlib.util
import json
from pathlib import Path

import pytest
from pharmalign_agent_common.testing import FakeModel
from pharmalign_agent_common.testing.fake_model import say_json

from app.chat.models import ModelChain, reset_cooldowns

PATH = Path(__file__).resolve().parents[2] / "eval" / "run_conversation.py"
SPEC = importlib.util.spec_from_file_location("run_conversation", PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

OUT = {"kind": "question", "follow_up": True, "parts": ["What is the fee for a clinic licence?"], "history_wanted": False,
       "english": True, "style": "shorter", "sources_named": ["WHO"]}
HISTORY = [{"user": "What is the fee for a clinic licence?", "reply": "100.", "kind": "answer", "suggestions": ["First?", "Second?"]}]


@pytest.fixture(autouse=True)
def _cooldowns():
    reset_cooldowns()
    yield
    reset_cooldowns()


def chain_of(count: int) -> tuple[ModelChain, FakeModel]:
    model = FakeModel(model="gemini-3.5-flash-lite", script=[say_json(OUT, tokens=(1000, 100))] * count)
    return ModelChain("classifier", ["gemini-3.5-flash-lite"], llm_factory=lambda _: model, retry_seconds=0), model


async def test_cases_are_scored_and_summed():
    cases = [
        {"id": "T-1", "category": "style", "message": "shorter please", "history": HISTORY, "expect": {
            "kinds": ["question"], "follow_up": True, "parts": ["clinic licence"], "not_parts": ["pharmacy"], "route": "answer",
            "style": "shorter", "sources_named": ["who"], "history_wanted": False}},
        {"id": "T-2", "category": "style", "message": "shorter please", "history": HISTORY, "expect": {
            "kinds": ["small_talk"], "follow_up": None, "parts": ["pharmacy"], "not_parts": ["clinic"], "route": "fixed:greeting",
            "style": "bullets", "sources_named": ["ICH"], "history_wanted": True}},
        {"id": "T-3", "category": "pick", "message": "the second one", "history": HISTORY, "expect": {
            "kinds": ["question"], "follow_up": True, "parts": ["^Second"], "not_parts": [], "route": "answer"}},
    ]
    chain, model = chain_of(5)
    result = await runner.run(cases, chain)
    by_id = {r["id"]: r for r in result["cases"]}
    assert by_id["T-1"]["pass"] and not by_id["T-1"]["missed"]
    assert not by_id["T-2"]["pass"] and len(by_id["T-2"]["missed"]) == 7
    assert by_id["T-3"]["pass"] and by_id["T-3"]["how"] == "suggestion_pick" and model.calls == 2
    s = result["summary"]
    assert (s["total"], s["passed"]) == (3, 1 + 1)
    assert s["categories"] == {"style": {"passed": 1, "total": 2}, "pick": {"passed": 1, "total": 1}}
    assert s["cost_usd"] == pytest.approx(2 * (1000 * 0.30 + 100 * 2.50) / 1_000_000, abs=1e-5)
    json.dumps(result)
    runner.report(result)


async def test_the_whole_case_file_runs_through_a_fake_chain():
    cases = runner.load_cases()
    assert len(cases) == 57
    chain, _ = chain_of(len(cases))
    result = await runner.run(cases, chain)
    assert result["summary"]["total"] == 57 and sum(c["category"] != "" for c in result["cases"]) == 57
    runner.report(result)


def test_help_works(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_conversation.py", "--help"])
    with pytest.raises(SystemExit) as stop:
        runner.main()
    assert stop.value.code == 0 and "--only" in capsys.readouterr().out
