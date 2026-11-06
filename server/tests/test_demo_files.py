"""The demo files for a new country validate against their contract models. The files are read as data only."""
import json
import re
from pathlib import Path

import pytest
from pharmalign_contracts.server import models as m

from app.domain.slot_template import PLATFORM_SLOTS

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo" / "new-country"
SEED = ROOT / "demo" / "seed"
BAHRAIN = DEMO / "bahrain"

# Folder -> contract model. A list file is checked item by item.
MODEL_OF = {
    "01 New country workspace": m.WorkspaceCreate,
    "02 Add a person": m.PersonCreate,
    "03 Grant a role": m.RoleGrantCreate,
    "04 Set legal status": m.AdoptionUpdate,
    "07 New watch source": m.WatchSourceCreate,
    "08 Register a connection": m.ConnectionCreate,
}
# Keys a file holds beside the model's own fields: the person is named, and the version is addressed.
EXTRA_KEY = {"03 Grant a role": "display_name", "04 Set legal status": "version_id"}


@pytest.fixture
def clean():
    """These tests need no database, so they replace the suite's cleaning step."""


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def items_of(path: Path) -> list[dict]:
    value = load(path)
    return value if isinstance(value, list) else [value]


def json_files() -> list[Path]:
    return sorted(DEMO.rglob("*.json"))


def seed(name: str) -> list[dict]:
    return load(SEED / f"{name}.json")


def validate(folder: str, item: dict):
    body = dict(item)
    extra = EXTRA_KEY.get(folder)
    if extra:
        assert extra in body, f"{folder}: every item needs {extra}"
        body.pop(extra)
    if folder == "03 Grant a role":
        body["person_id"] = "person-made-by-the-server"
    return MODEL_OF[folder].model_validate(body)


def test_every_json_file_has_a_model():
    assert json_files(), "no demo files found"
    for path in json_files():
        assert path.parent.name in MODEL_OF, f"{path.relative_to(DEMO)} has no contract model"


def test_every_model_has_a_file():
    for folder in MODEL_OF:
        assert list((BAHRAIN / folder).glob("*.json")), f"{folder} has no JSON file"


@pytest.mark.parametrize("path", json_files(), ids=lambda p: p.parent.name)
def test_file_validates(path: Path):
    folder = path.parent.name
    for i, item in enumerate(items_of(path)):
        try:
            validate(folder, item)
        except Exception as e:
            pytest.fail(f"{path.relative_to(DEMO)} item {i}: {e}")


def test_workspace():
    ws = m.WorkspaceCreate.model_validate(load(BAHRAIN / "01 New country workspace" / "workspace.json"))
    assert ws.country_code == "BH"
    assert ws.authority_name.endswith("(fictional)")
    assert ws.day_counting == "sun_thu"
    assert ws.time_zone == "Asia/Bahrain"
    if ws.template == "copy":
        assert ws.copy_from_workspace_id in {w["doc"]["id"] for w in seed("workspace")}


def test_people_are_synthetic_and_new():
    people = [m.PersonCreate.model_validate(p) for p in items_of(BAHRAIN / "02 Add a person" / "people.json")]
    assert len(people) == 7
    assert sorted(p.view for p in people) == ["auditor", "case_officer", "case_officer", "decision_maker", "decision_maker", "process_owner", "process_owner"]
    assert all(p.email and p.email.endswith("@example.org") for p in people)
    names = [p.display_name for p in people]
    assert len(set(names)) == 7
    assert not set(names) & {p["doc"]["display_name"] for p in seed("person")}


def test_each_person_has_one_grant():
    names = [p["display_name"] for p in items_of(BAHRAIN / "02 Add a person" / "people.json")]
    grants = items_of(BAHRAIN / "03 Grant a role" / "role-grants.json")
    assert sorted(g["display_name"] for g in grants) == sorted(names)
    expected = {"case_officer": "assessor", "decision_maker": "decision_maker", "process_owner": "process_owner", "auditor": "auditor"}
    view_of = {p["display_name"]: p["view"] for p in items_of(BAHRAIN / "02 Add a person" / "people.json")}
    for g in grants:
        assert g["role"] == expected[view_of[g["display_name"]]]
        assert (g["role"] == "assessor") == bool(g.get("discipline")), "an assessor grant names a discipline"


def test_adoptions_are_of_shared_library_documents():
    adoptions = items_of(BAHRAIN / "04 Set legal status" / "adoptions.json")
    assert 8 <= len(adoptions) <= 12
    versions = {v["doc"]["id"]: v["doc"] for v in seed("document_version")}
    documents = {d["doc"]["id"]: d["doc"] for d in seed("document")}
    ids = [a["version_id"] for a in adoptions]
    assert len(set(ids)) == len(ids)
    for vid in ids:
        version = versions[vid]
        doc = documents[version["document_id"]]
        assert doc["shelf"] == "shared", f"{vid} is not a shared document"
        assert version["review_status"] == "approved" and not version["held"], f"{vid} is not an approved version"
        assert doc["jurisdiction"] != "UAE"


def test_slots_file_names_real_documents():
    text = (BAHRAIN / "05 Link required documents" / "slots.md").read_text(encoding="utf-8")
    documents = {d["doc"]["id"] for d in seed("document")}
    labels = {d.label for d in PLATFORM_SLOTS}
    for ref in set(re.findall(r"`([A-Z]-S?\d+)`", text)):
        assert ref in documents, f"slots.md names {ref}, which is not in the library"
    rows = [line.split("|")[1].strip() for line in text.splitlines() if re.match(r"\| .+ \| (must|nice) \| `", line)]
    assert rows, "slots.md has no link table"
    for label in rows:
        assert label in labels, f"slots.md names the slot {label!r}, which the standard list does not have"


def test_slots_file_lists_every_slot_the_template_creates():
    text = (BAHRAIN / "05 Link required documents" / "slots.md").read_text(encoding="utf-8")
    for d in PLATFORM_SLOTS:
        assert d.label in text, f"slots.md does not name {d.label!r}"
    must = sum(d.importance == "must" for d in PLATFORM_SLOTS)
    assert f"{len(PLATFORM_SLOTS)} of them, {must} must-have and {len(PLATFORM_SLOTS) - must} nice-to-have" in text
    readme = (DEMO / "README.md").read_text(encoding="utf-8")
    assert f"{len(PLATFORM_SLOTS)} required slots" in readme


def test_there_are_two_process_owners_and_a_pack_step():
    people = items_of(BAHRAIN / "02 Add a person" / "people.json")
    assert sum(p["view"] == "process_owner" for p in people) == 2
    pack = (BAHRAIN / "06 Country pack" / "pack.md").read_text(encoding="utf-8")
    for button in ("Freeze the library", "Open validation", "Run simulation", "Approve v1", "Activate for new cases"):
        assert button in pack, f"pack.md does not name {button!r}"


def test_folders_are_numbered_without_gaps():
    folders = sorted(p.name for p in BAHRAIN.iterdir() if p.is_dir())
    assert [f[:2] for f in folders] == [f"{i:02d}" for i in range(1, len(folders) + 1)]


def test_no_real_authority_names():
    pattern = re.compile(r"NHRA|national health regulatory", re.I)
    for path in DEMO.rglob("*"):
        if path.is_file():
            assert not pattern.search(path.read_text(encoding="utf-8")), f"{path.relative_to(DEMO)} names a real authority"
