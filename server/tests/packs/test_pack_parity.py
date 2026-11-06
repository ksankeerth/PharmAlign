"""Pack validation and diffs give exactly the results stored in server/tests/vectors/packs.json."""
import json
from pathlib import Path

import pytest

from app.domain.pack_validation import json_diff, locked_step_changes, misplaced_new_steps, validate_pack_document

VECTORS = json.loads((Path(__file__).parent.parent / "vectors" / "packs.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("v", VECTORS["validation"], ids=lambda v: v["name"])
def test_every_validation_rule_agrees_with_the_web_mock(v):
    known = set(v["known_doc_ids"]) if v["known_doc_ids"] is not None else None
    got = validate_pack_document(v["document"], known).model_dump(mode="json")
    assert got == v["output"]


@pytest.mark.parametrize("v", VECTORS["diffs"], ids=lambda v: v["name"])
def test_every_diff_agrees_with_the_web_mock(v):
    assert [c.model_dump(mode="json") for c in json_diff(v["before"], v["after"])] == v["output"]


def test_the_vectors_reach_every_rule():
    rules = {i["rule"] for v in VECTORS["validation"] for i in v["output"]["issues"]}
    assert rules >= {
        "unique_check_ids", "source_required", "typed_needs_rule", "code_needs_rule", "agent_needs_source_and_profile", "binding_waiver",
        "return_notice_code_only", "prefer_typed", "blocking_passes_alone", "unknown_source", "local_practice", "escalation_holder_required",
        "one_fallback_route", "locked_steps", "door_before_decide", "decide_before_record", "unique_step_ids", "label_proposed",
        "decision_person_signs", "question_suggest_only", "unknown_checks",
    }


def doc_with(steps: list[dict]) -> dict:
    return {"case_types": [{"id": "ct", "label": "Type", "routes": [{"id": "r", "label": "Route", "steps": steps}]}]}


STEPS = [{"id": "submit", "name": "Submit", "kind": "submit", "locked": True}, {"id": "door", "name": "Door", "kind": "door", "locked": True},
         {"id": "decide", "name": "Decision", "kind": "decide", "locked": True}, {"id": "record", "name": "Record", "kind": "record", "locked": True}]


def test_a_locked_step_cannot_be_removed_or_change_kind():
    before = doc_with(STEPS)
    assert locked_step_changes(before, before) == []
    assert locked_step_changes(before, doc_with([s for s in STEPS if s["id"] != "submit"])) == ["Type, Route: the submit step can't be removed."]
    renamed = [{**s, "kind": "work"} if s["id"] == "door" else s for s in STEPS]
    assert locked_step_changes(before, doc_with(renamed)) == ["Type, Route: the door step keeps its kind."]
    added = [*STEPS[:2], {"id": "x", "name": "X", "kind": "decide"}, *STEPS[2:]]
    assert locked_step_changes(before, doc_with(added)) == ["Type, Route: a decide step can't be added."]


def test_a_new_step_must_go_before_the_decision():
    before = doc_with(STEPS)
    new = {"id": "price", "name": "Pricing", "kind": "work"}
    assert misplaced_new_steps(before, doc_with([*STEPS[:2], new, *STEPS[2:]])) == []
    assert misplaced_new_steps(before, doc_with([*STEPS[:3], new, STEPS[3]])) == ["Type, Route: the new step Pricing must go before the decision."]
