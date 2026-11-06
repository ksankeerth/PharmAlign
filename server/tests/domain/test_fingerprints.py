"""What the vectors can't show: storage round trips, and what each fingerprint must react to."""
import ast
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest
from pharmalign_contracts.common import FactValue
from pharmalign_contracts.server.models import CheckDetail, Conflict

import app.domain
from app.domain import fingerprints as F
from app.domain.guards import assign_guards, assessors_of, signer_guards
from app.domain.fingerprints import CaseFacts

from .builders import CASE, FP_B, NOW, case, check, match, package, ready_case, waiver, with_brief

VECTORS = json.loads((Path(__file__).parent.parent / "vectors" / "guards.json").read_text(encoding="utf-8"))
SEEDED = [c for name, s in VECTORS["states"].items() if name.startswith("seed:") for c in s["checks"]]


@pytest.mark.parametrize("number, text", [
    (5.0, "5"), (5, "5"), (-0.0, "0"), (0.1 + 0.2, "0.30000000000000004"), (1e21, "1e+21"), (1e-7, "1e-7"), (0.000001, "0.000001"),
    (1.2345678901234568e20, "123456789012345680000"), (1.5e300, "1.5e+300"), (-2.5, "-2.5"), (2**60, "1152921504606847000"),
    (math.nan, "null"), (math.inf, "null"),
])
def test_numbers_are_written_the_way_javascript_writes_them(number, text):
    assert F.stable_stringify(number) == text


def test_every_seeded_fingerprint_is_reproduced_from_its_contract_model():
    assert len(SEEDED) > 100
    for row in SEEDED:
        model = CheckDetail.model_validate(row)
        assert F.check_fingerprint(model) == row["fingerprint"], row["id"]


def test_a_fingerprint_survives_being_stored_and_read_back():
    for row in SEEDED:
        model = CheckDetail.model_validate(row)
        stored = CheckDetail.model_validate(json.loads(json.dumps(model.model_dump(mode="json"))))
        assert F.check_fingerprint(stored) == F.check_fingerprint(model), row["id"]


def test_a_whole_number_fact_hashes_the_same_as_the_mock_after_becoming_a_float():
    fact = FactValue(key="strength", status="found", value="5", number=5, evidence=[{"anchor": "doc:1:p1", "quote": "5"}])
    assert isinstance(fact.number, float)
    assert '"number":5,' in F.stable_stringify(fact.model_dump(mode="json"))


CHANGES = {
    "recommendation version": lambda f: with_brief(f, version=2),
    "check result": lambda f: replace(f, checks=(check(status="held"),)),
    "evidence, through the check's fingerprint": lambda f: replace(f, checks=(check(fingerprint=FP_B),)),
    "a waiver being given": lambda f: replace(f, checks=(check(waiver=waiver()),)),
    "pack version": lambda f: replace(f, case=case(pack_version_id="pack-2")),
    "snapshot": lambda f: replace(f, case=case(kb_snapshot_id="snap-2")),
    "sequence": lambda f: replace(f, case=case(sequence=2)),
    "an open foreign match": lambda f: replace(f, matches=(match(),)),
}


@pytest.mark.parametrize("what", CHANGES)
def test_the_signing_fingerprint_changes_when(what):
    facts = ready_case()
    assert package(CHANGES[what](facts)) != package(facts), what


def test_the_signing_fingerprint_ignores_a_dismissed_match_and_match_order():
    facts = ready_case()
    assert package(replace(facts, matches=(match("dismissed"),))) == package(facts)
    a, b = match(id="m-a"), match(id="m-b")
    assert package(replace(facts, matches=(a, b))) == package(replace(facts, matches=(b, a)))


@pytest.mark.parametrize("over", [{"generation": 2}, {"attempt": 2}, {"no_source_found": True}, {"suspect_pages": [1]}])
def test_the_check_fingerprint_changes_with_its_evidence(over):
    assert F.check_fingerprint(check(**over)) != F.check_fingerprint(check())


def test_a_replaced_result_stays_out_of_the_fingerprint_but_its_people_stay_assessors():
    current = check()
    old = check(id="cr-m-1~g0a1", is_current=False, status="waived", waiver=waiver(by="p-old"))
    facts = CaseFacts(case_id=CASE, case=case(), checks=(current, old))
    assert [c.id for c in F.case_checks(facts)] == [current.id]
    assert F.case_input_fingerprint(facts) == F.case_input_fingerprint(replace(facts, checks=(current,)))
    assert "p-old" in assessors_of(facts)
    assert signer_guards(facts, "p-old")[0].passed is False


def test_a_long_conflict_reason_is_cut_to_what_the_contract_allows():
    conflict = Conflict(id="coi-1", person_id="p-1", case_id=CASE, reason="x" * 2000, declared_at=NOW)
    facts = CaseFacts(case_id=CASE, case=case(), conflicts=(conflict,))
    assert len(assign_guards(facts, "p-1")[1].detail) == 500


def test_the_domain_has_no_database_or_web_code():
    banned = {"sqlalchemy", "asyncpg", "fastapi", "starlette", "alembic", "httpx"}
    for path in Path(app.domain.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in banned, f"{path.name} imports {name}"
                assert not (name.startswith("app.") and not name.startswith("app.domain")), f"{path.name} imports {name}"
