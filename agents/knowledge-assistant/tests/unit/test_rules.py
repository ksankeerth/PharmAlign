import json

import pytest
from pharmalign_agent_common import OutputInvalid, ToolLog, Turn
from pharmalign_contracts.agents.knowledge_assistant import SessionCreate, TurnRequest

from app.agent import SPEC
from app.answer import TurnAnswer
from app.rules import build_message, check_turn, legal_status_of
from pharmalign_agent_common.sessions import parse_answer
from tests.conftest import LAW_ANCHOR, LAW_QUOTE, MESSAGE, OLD_ANCHOR, OLD_QUOTE, case, session_body


def log_of(*calls) -> ToolLog:
    """A log from (tool, args) pairs, answered by the fake case."""
    fake, log = case(), ToolLog()
    for tool, args in calls:
        log.record(tool, args, {"content": [{"type": "text", "text": json.dumps(getattr(fake, tool)(args))}]})
    return log


READ = log_of(("search_kb", {"query": "long-term stability data conditions"}), ("read_clause", {"anchor": OLD_ANCHOR}))


def turn(message: str = "What stability data is required?", case_session: bool = False, before: int = 0) -> Turn:
    return Turn(SessionCreate.model_validate(session_body(case_session)), TurnRequest(message=message), before)


def answer(**changes) -> TurnAnswer:
    return TurnAnswer.model_validate({"events": [{**MESSAGE["events"][0], **changes}]})


def test_a_good_message_passes():
    check_turn(turn(), answer(), READ)


def test_legal_status_comes_from_what_the_tool_returned():
    assert legal_status_of(READ, LAW_ANCHOR) == "in_force" and legal_status_of(READ, OLD_ANCHOR) == "superseded"


@pytest.mark.parametrize("changes, reason", [
    ({"citations": []}, "at least one citation"),
    ({"citations": [{"anchor": "E-S25:v3:c0001", "quote": LAW_QUOTE}]}, "wasn't returned"),
    ({"citations": [{"anchor": LAW_ANCHOR, "quote": "a quote the clause does not hold"}]}, "isn't in the text"),
    ({"trace_ids": ["trace-QUAL-07"]}, "get_run_trace"),
    ({"citations": [{"anchor": OLD_ANCHOR, "quote": OLD_QUOTE}]}, "in force"),
])
def test_a_message_breaking_a_rule_is_refused(changes, reason):
    with pytest.raises(ValueError, match=reason):
        check_turn(turn(), answer(**changes), READ)


def test_old_law_in_a_history_answer_must_be_labelled():
    cited = {"citations": [{"anchor": OLD_ANCHOR, "quote": OLD_QUOTE}]}
    history = turn("What was the earlier rule?")
    with pytest.raises(ValueError, match="Say so"):
        check_turn(history, answer(**cited), READ)
    check_turn(history, answer(text="The superseded guideline asked for 6 months.", **cited), READ)


def test_no_source_counts_only_searches_of_this_turn():
    nothing = log_of(("search_kb", {"query": "long-term stability data conditions"}))  # a hit
    nothing.record("search_kb", {"query": "zzz"}, {"content": [{"type": "text", "text": json.dumps({"no_source_found": True, "hits": []})}]})
    one = {"events": [{"type": "no_source", "searches_tried": [{"search": "zzz", "result": "nothing"}]}]}
    two = {"events": [{"type": "no_source", "searches_tried": [{"search": "a", "result": "n"}, {"search": "b", "result": "n"}]}]}
    check_turn(turn(), TurnAnswer.model_validate(one), nothing)
    with pytest.raises(ValueError, match="Search first"):
        check_turn(turn(before=len(nothing.calls)), TurnAnswer.model_validate(one), nothing)  # that search was an earlier turn
    nothing.record("search_kb", {"query": "yyy"}, {"content": [{"type": "text", "text": json.dumps({"no_source_found": True, "hits": []})}]})
    with pytest.raises(ValueError, match="list every search"):
        check_turn(turn(), TurnAnswer.model_validate(one), nothing)
    check_turn(turn(), TurnAnswer.model_validate(two), nothing)


def test_the_answer_must_hold_only_allowed_event_types():
    bad = json.dumps({"events": [{"type": "status_change", "status": "pass"}]})
    with pytest.raises(OutputInvalid, match="outside the allowed set"):
        parse_answer(SPEC, turn(), bad, ToolLog())
    with pytest.raises(OutputInvalid, match="JSON"):
        parse_answer(SPEC, turn(), "Sure, approved.", ToolLog())


def test_the_message_has_the_question_and_no_token():
    message = json.loads(build_message(SessionCreate.model_validate(session_body()), TurnRequest(message="Why?", focus={"check_id": "QUAL-07"})))
    assert message["question"] == "Why?" and message["focus"] == {"check_id": "QUAL-07"} and message["case_id"] == "case-1042"
    assert "session-token" not in json.dumps(message)
