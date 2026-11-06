"""Search filters first, ranks second, and says when nothing answers. Legal status belongs to a country's adoption."""
from .conftest import OTHER_OWNER, law


async def test_superseded_law_stays_out_of_in_force_search_but_stays_citable(lib):
    doc = await lib.register("Operating licences")
    v1 = (await lib.upload(doc, law("operating licences")))["id"]
    await lib.process(v1)
    await lib.adopt(v1, "in_force")
    anchor = (await lib.chunks(v1))[0]["anchor"]
    old_snapshot = await lib.snapshot("Before the new law")

    v2 = (await lib.upload(doc, law("operating licences", suffix="Replaces the earlier law.")))["id"]
    await lib.process(v2)
    await lib.adopt(v2, "in_force")
    await lib.adopt(v1, "superseded")

    in_force = await lib.search("operating licences records", top_k=50)
    assert in_force["hits"] and {h["legal_status"] for h in in_force["hits"]} == {"in_force"}
    assert not any(h["anchor"].startswith(f"{doc}:v1:") for h in in_force["hits"])

    history = await lib.search("operating licences records", purpose="context", top_k=50)
    old = [h for h in history["hits"] if h["anchor"] == anchor]
    assert old and old[0]["legal_status"] == "superseded"  # still there, and labelled

    pinned = await lib.search("operating licences records", snapshot_id=old_snapshot, top_k=50)
    assert any(h["anchor"] == anchor and h["legal_status"] == "in_force" for h in pinned["hits"])  # what applied then still reads as it was


async def test_top_matches_outside_the_filter_do_not_crowd_out_a_qualifying_document(lib):
    for i in range(6):  # six near-perfect matches that may not be used as requirements
        await lib.publish(f"Old pharmacy rule {i}", law("pharmacy operating licence renewal fees"), status="superseded")
    right, _ = await lib.publish("Current licensing", law("licence renewal", suffix="Fees are due each year."))
    result = await lib.search("pharmacy operating licence renewal fees", top_k=3)
    assert result["no_source_found"] is False
    assert {h["document_id"] for h in result["hits"]} == {right}
    assert result["filters_applied"]["applied_before_search"] is True


async def test_a_question_with_no_in_force_source_returns_no_source(lib):
    await lib.publish("Storage rules", law("cold storage of vaccines"))
    nothing = await lib.search("child resistant closure for oral liquids")
    assert nothing["no_source_found"] is True and nothing["hits"] == []
    tried = nothing["searches_tried"]
    assert tried and tried[0]["candidates"] > 0 and "floor" in tried[0]

    await lib.publish("Superseded stock law", law("controlled stock counts"), status="superseded")
    only_old = await lib.search("controlled stock counts")
    assert only_old["no_source_found"] is True and only_old["hits"] == []  # a superseded source is not an answer to "what is required"
    assert "superseded" in only_old["searches_tried"][1]["result"]


async def test_an_adoption_in_one_country_does_not_affect_another(lib):
    _, v = await lib.publish("Shared guideline", law("shared stability"))  # in force in ws-ae
    await lib.adopt(v, "superseded", workspace="ws-sa", person=OTHER_OWNER)
    await lib.adopt(v, "in_force", workspace="ws-sa", person=OTHER_OWNER)  # then in force there too
    await lib.adopt(v, "superseded", workspace="ws-sa", person=OTHER_OWNER)

    ae = await lib.http.get("/api/v1/workspaces/ws-ae/adoptions", headers=lib.as_("p-sam"))
    sa = await lib.http.get("/api/v1/workspaces/ws-sa/adoptions", headers=lib.as_(OTHER_OWNER))
    assert [a["legal_status"] for a in ae.json()["items"]] == ["in_force"]
    assert [a["legal_status"] for a in sa.json()["items"]] == ["superseded"]
    assert (await lib.search("shared stability records"))["hits"]
    elsewhere = await lib.search("shared stability records", person=OTHER_OWNER)
    assert elsewhere["no_source_found"] is True


async def test_a_pinned_snapshot_is_unaffected_by_a_later_adoption(lib):
    _, v = await lib.publish("Registration law", law("registration of products"))
    pinned = await lib.snapshot("Pinned to a case")
    await lib.adopt(v, "superseded")

    now = await lib.search("registration of products")
    assert now["no_source_found"] is True
    then = await lib.search("registration of products", snapshot_id=pinned)
    assert then["hits"] and {h["legal_status"] for h in then["hits"]} == {"in_force"}
    detail = (await lib.http.get(f"/api/v1/kb-snapshots/{pinned}", headers=lib.as_("p-sam"))).json()
    assert [i["legal_status"] for i in detail["items"]] == ["in_force"]
