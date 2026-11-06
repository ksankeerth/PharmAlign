"""The domain rules must give exactly the results stored in server/tests/vectors/guards.json."""
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pytest
from pharmalign_contracts.server.models import (
    CaseDetail,
    CheckDefinition,
    CheckDetail,
    Conflict,
    DocumentVersion,
    PackValidation,
    PackVersionSummary,
    Promotion,
    Question,
    RecommendationVersion,
    Thread,
    WatchMatch,
)

from app.domain import fingerprints as F
from app.domain import guards as G
from app.domain.status import is_clear

VECTORS = json.loads((Path(__file__).parent.parent / "vectors" / "guards.json").read_text(encoding="utf-8"))
GUARD_NAMES = """case_ready_for_promotion blocking_checks_clear current_recommendation no_open_threads no_open_questions
fingerprint_matches case_promoted signer_did_not_assess signer_did_not_promote no_declared_conflict foreign_status_current
recommendation_version_current case_not_frozen check_waivable waiver_permitted check_needs_person not_own_change
pack_simulated pack_valid pack_approved not_own_upload quality_gate_passed""".split()


@lru_cache(maxsize=None)
def facts_of(name: str) -> F.CaseFacts:
    s = VECTORS["states"][name]
    return F.CaseFacts(
        case_id=s["case_id"],
        case=CaseDetail.model_validate(s["case"]) if s["case"] else None,
        assessors=tuple(s["assessors"]),
        promotion=Promotion.model_validate(s["promotion"]) if s["promotion"] else None,
        checks=tuple(CheckDetail.model_validate(c) for c in s["checks"]),
        recommendations=tuple(RecommendationVersion.model_validate(r) for r in s["recommendations"]),
        threads=tuple(Thread.model_validate(t) for t in s["threads"]),
        questions=tuple(Question.model_validate(q) for q in s["questions"]),
        matches=tuple(WatchMatch.model_validate(m) for m in s["matches"]),
        conflicts=tuple(Conflict.model_validate(c) for c in s["conflicts"]),
        pack_checks=tuple(CheckDefinition.model_validate(d) for d in s["pack_checks"]),
    )


def check_of(facts: F.CaseFacts, result_id: str) -> CheckDetail:
    return next(c for c in facts.checks if c.id == result_id)


def run_vector(v: dict):
    fn, a = v["fn"], v["args"]
    facts = facts_of(v["state"]) if "state" in v else None
    match fn:
        case "case_checks":
            return [c.id for c in F.case_checks(facts)]
        case "case_input_fingerprint":
            return F.case_input_fingerprint(facts)
        case "case_package_fingerprint":
            return F.case_package_fingerprint(facts)
        case "current_recommendation":
            r = F.current_recommendation(facts)
            return r.version if r else None
        case "promotion_guards":
            return G.promotion_guards(facts)
        case "assessors_of":
            return sorted(G.assessors_of(facts))
        case "signing_guards":
            return G.signing_guards(facts, G.SigningInput(a["signer_id"], a["fingerprint"], a.get("recommendation_version")))
        case "assign_guards":
            return G.assign_guards(facts, a["person_id"])
        case "is_clear":
            return is_clear(check_of(facts, a["check"]))
        case "check_fingerprint":
            return F.check_fingerprint(check_of(facts, a["check"]))
        case "confirm_guards":
            return G.confirm_guards(facts, check_of(facts, a["check"]), a["fingerprint"])
        case "waive_guards":
            return G.waive_guards(facts, check_of(facts, a["check"]), a["view"], a["fingerprint"])
        case "fingerprint_guard":
            return G.fingerprint_guard(a["sent"], a["current"])
        case "pack_activate_guards":
            return G.pack_activate_guards(PackVersionSummary.model_validate(a["pack"]))
        case "pack_approve_guards":
            return G.pack_approve_guards(PackVersionSummary.model_validate(a["pack"]), a["approver_id"], PackValidation.model_validate(a["validation"]))
        case "document_review_guards":
            return G.document_review_guards(DocumentVersion.model_validate(a["version"]), a["reviewer_id"], a["decision"])
        case "fingerprint_of":
            return {"text": F.stable_stringify(a["value"]), "fingerprint": F.fingerprint_of(a["value"])}
    raise AssertionError(f"No port for {fn}")


def plain(value):
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value.model_dump() if hasattr(value, "model_dump") else value


BY_FN: dict[str, list[dict]] = defaultdict(list)
for vector in VECTORS["vectors"]:
    BY_FN[vector["fn"]].append(vector)


@pytest.mark.parametrize("fn", sorted(BY_FN))
def test_port_matches_the_mock(fn):
    wrong = []
    for v in BY_FN[fn]:
        got = plain(run_vector(v))
        if got != v["output"]:
            wrong.append((v.get("state"), v["args"], got, v["output"]))
    assert not wrong, f"{len(wrong)} of {len(BY_FN[fn])} differ. First: {wrong[0]}"


def test_every_guard_is_seen_both_passing_and_failing():
    outcomes = defaultdict(set)
    for v in VECTORS["vectors"]:
        if isinstance(v["output"], list) and v["output"] and isinstance(v["output"][0], dict):
            for g in v["output"]:
                outcomes[g["guard"]].add(g["passed"])
    assert set(GUARD_NAMES) == set(outcomes)
    assert {n: o for n, o in outcomes.items() if o != {True, False}} == {}


def test_counts_in_the_file_are_honest():
    assert VECTORS["counts"] == {fn: len(vs) for fn, vs in BY_FN.items()}
    assert sum(VECTORS["counts"].values()) > 1000
