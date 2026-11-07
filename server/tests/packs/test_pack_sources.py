"""A pack is refused when a check's source names a clause that matches no passage of that document in the workspace's knowledge snapshot,
read as the agents' read_clause reads it. The pure rule is covered with the other pack rules; this covers the handlers."""
import copy

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db import make_sessions
from app.domain.pack_validation import SourceCheck, clause_sources, validate_pack_document
from app.handlers.packs import source_check_of
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files, truncate_all
from app.tables import ROW

from ..conftest import START, TENANT
from .conftest import add_clause_passages, failed, headers
from .test_packs import read, save

pytestmark = pytest.mark.seeded
BOGUS = "Principles of reliance"
UNRESOLVED_IN_THE_SEED: dict[str, list] = {}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def seeded(engine):
    """The demo seed, plus a passage for each clause that the seed's excerpts do not hold in a retired pack (see add_clause_passages). The live packs need none."""
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)
        packs = (await db.scalars(select(ROW["pack_version"]).where(ROW["pack_version"].tenant_id == TENANT))).all()
        for row in packs:
            if row.doc["state"] != "retired":  # a retired version is history and keeps the clauses it had
                UNRESOLVED_IN_THE_SEED[row.id] = sorted((await source_check_of(db, TENANT, row.doc["document"])).unresolved)
            await add_clause_passages(db, TENANT, row.doc["document"])


async def errors_of(http, version: str) -> list[dict]:
    r = await http.post(f"/api/v1/pack-versions/{version}/validate", headers=headers("p-sam"))
    assert r.status_code == 200, r.text
    return [i for i in r.json()["issues"] if i["severity"] == "error"]


async def test_the_seed_itself_holds_a_passage_for_every_clause_of_its_live_packs():
    """No helper passages are needed for the active and draft UAE packs: each clause they cite is in an excerpt the seed carries."""
    assert UNRESOLVED_IN_THE_SEED and UNRESOLVED_IN_THE_SEED == {pack: [] for pack in UNRESOLVED_IN_THE_SEED}


async def test_the_seeded_pack_validates_when_its_clauses_have_passages(http):
    assert [e for e in await errors_of(http, "pv-ae-4") if e["rule"] == "source_clause_unresolved"] == []


async def test_a_pack_with_a_clause_that_matches_nothing_fails_naming_the_check(http):
    document = copy.deepcopy((await read(http, "pv-ae-4"))["document"])
    check = next(c for c in document["checks"] if (c.get("source") or {}).get("clause"))
    doc_id = check["source"]["doc_id"]
    check["source"]["clause"] = BOGUS
    assert (await save(http, "pv-ae-4", document)).status_code == 200
    bad = [e for e in await errors_of(http, "pv-ae-4") if e["rule"] == "source_clause_unresolved"]
    assert [e["path"] for e in bad] == [f"checks[{check['id']}]"]
    assert bad[0]["message"] == (f'Check {check["id"]}: clause "{BOGUS}" matches no passage of {doc_id} in knowledge snapshot snap-ae-14. '
                                 "Point it at a real article or heading.")
    simulate = await http.post("/api/v1/pack-versions/pv-ae-4/simulate", json={"scenario_ids": ["scn-clean"]}, headers=headers("p-priya"))
    assert simulate.status_code == 409 and "pack_valid" in failed(simulate)
    approve = await http.post("/api/v1/pack-versions/pv-ae-4/approve", json={"reason": "Trying to approve."}, headers=headers("p-priya"))
    assert "pack_valid" in failed(approve)


async def test_a_clause_resolves_as_read_clause_reads_it(engine):
    """Case does not matter and a heading anywhere in the path counts. A % in the clause is a literal character, not a wildcard."""
    async with make_sessions(engine)() as db:
        document = (await db.get(ROW["pack_version"], "pv-ae-3")).doc["document"]
        check = await source_check_of(db, TENANT, document)
        assert check.snapshot_id == "snap-ae-14" and check.unresolved == frozenset()
        doc_id, clause = next((d, c) for _, _, d, c in clause_sources(document))
        probe = lambda clause: {"checks": [{"id": "X-01", "source": {"doc_id": doc_id, "clause": clause}}]}  # noqa: E731
        assert (await source_check_of(db, TENANT, probe(clause.upper()))).unresolved == frozenset()
        assert (await source_check_of(db, TENANT, probe(clause[:4]))).unresolved == frozenset()
        assert (await source_check_of(db, TENANT, probe("%"))).unresolved == {(doc_id, "%")}
        assert (await source_check_of(db, TENANT, probe("_"))).unresolved == {(doc_id, "_")}


# ---------------------------------------------------------------- the pure rule

def pack(*sources: tuple[str, str, str]) -> dict:
    return {"final_escalation_holder": "role-owner",
            "checks": [{"id": cid, "method": "code", "rule": {"x": 1}, "source": {"doc_id": d, "clause": c}} for cid, d, c in sources]}


def test_an_unresolved_source_is_an_error_naming_its_check():
    doc = pack(("ROUTE-01", "F-01", BOGUS), ("DOOR-01", "B-01", "Required documents"))
    got = validate_pack_document(doc, {"F-01", "B-01"}, SourceCheck("snap-ae-14", frozenset({("F-01", BOGUS)})))
    assert got.valid is False and [(i.path, i.rule, i.severity) for i in got.issues] == [("checks[ROUTE-01]", "source_clause_unresolved", "error")]
    assert got.issues[0].message == 'Check ROUTE-01: clause "Principles of reliance" matches no passage of F-01 in knowledge snapshot snap-ae-14. Point it at a real article or heading.'


def test_a_pack_whose_sources_all_resolve_is_valid_and_the_check_is_optional():
    doc = pack(("ROUTE-01", "F-01", BOGUS))
    assert validate_pack_document(doc, {"F-01"}, SourceCheck("snap-ae-14")).valid is True
    assert validate_pack_document(doc, {"F-01"}).valid is True


def test_a_source_in_a_reliance_rule_is_named_by_its_path():
    doc = {**pack(), "case_types": [{"id": "ct", "label": "Type", "routes": [{"id": "abridged", "label": "Abridged", "steps": [
        {"id": "door", "name": "Door", "kind": "door"}, {"id": "decide", "name": "Decide", "kind": "decide", "agent_autonomy": "person_signs"}, {"id": "record", "name": "Record", "kind": "record"}],
        "reliance_rule": {"source": {"doc_id": "F-01", "clause": BOGUS}}}]}]}
    got = validate_pack_document(doc, {"F-01"}, SourceCheck("snap-ae-14", frozenset({("F-01", BOGUS)})))
    unresolved = [i for i in got.issues if i.rule == "source_clause_unresolved"]
    assert [i.path for i in unresolved] == ["case_types[ct].routes[abridged].reliance_rule.source"]
    assert unresolved[0].message.startswith('case_types[ct].routes[abridged].reliance_rule.source: clause "Principles of reliance" matches no passage of F-01')


def test_no_snapshot_is_a_warning_not_an_error_and_a_source_without_a_clause_is_not_checked():
    got = validate_pack_document(pack(("ROUTE-01", "F-01", BOGUS)), {"F-01"}, SourceCheck(None))
    assert got.valid is True and [(i.rule, i.severity) for i in got.issues] == [("sources_unchecked", "warning")]
    bare = {"final_escalation_holder": "x", "checks": [{"id": "A-01", "method": "code", "rule": {"x": 1}, "source": {"doc_id": "F-01"}}]}
    assert validate_pack_document(bare, {"F-01"}, SourceCheck(None)).issues == []
    assert validate_pack_document(bare, {"F-01"}, SourceCheck("snap-ae-14", frozenset({("F-01", "")}))).issues == []


SUPPORTED = {"checks": [{"id": "LEGAL-01", "source": {"doc_id": "A-01", "clause": "Article (6)"},
                         "supporting_sources": [{"doc_id": "B-01", "clause": "Required documents"}]}]}


def test_supporting_sources_are_listed_with_the_main_source():
    assert [x[1:] for x in clause_sources(SUPPORTED)] == [("LEGAL-01", "A-01", "Article (6)"), ("LEGAL-01", "B-01", "Required documents")]
    assert [x[0] for x in clause_sources(SUPPORTED)] == ["checks[LEGAL-01]", "checks[LEGAL-01].supporting_sources[0]"]


def test_an_unknown_or_unresolved_supporting_source_is_an_error():
    v = validate_pack_document(SUPPORTED, known_doc_ids={"A-01"}, source_check=SourceCheck("snap-1", frozenset({("B-01", "Required documents")})))
    assert [i.path for i in v.issues if i.rule == "source_clause_unresolved"] == ["checks[LEGAL-01].supporting_sources[0]"]
