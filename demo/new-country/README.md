# Set up a new country

Files for setting up one new country, **Bahrain**, through the app's own forms, the way a real team would: create the workspace, add the people, set the legal status of the documents the country relies on, fill the required slots, approve the country pack, add a watch source and a connection, then ask for sign-off.

The Bahrain authority in these files is **fictional** ("Bahrain Medicines Authority (fictional)"). It uses no real authority's name or branding. Every person is **synthetic**, with an `example.org` email address. Bahrain is not in the seed data, so it starts blank. The sample UAE, Saudi Arabia and United Kingdom workspaces are untouched, and Oman stays as the half-finished example.

## How to use it

Sign in as **Jordan Lee** (Platform Admin). Work down the folders in order. Each folder is named after the dialog or page where its file is used. Every form has a **Fill from a JSON file** button: press it, choose the file, check what it filled in, then save. Later steps refer to earlier ones, so keep the order.

```
bahrain/
├── 01 New country workspace/workspace.json
├── 02 Add a person/people.json                 7 people
├── 03 Grant a role/role-grants.json            1 grant per person
├── 04 Set legal status/adoptions.json          12 adoptions
├── 05 Link required documents/slots.md         which document goes in which slot
├── 06 Country pack/pack.md                     validate, approve and activate the copied pack
├── 07 New watch source/watch-source.json
├── 08 Register a connection/connection.json
└── 09 Sign off/sign-off.md                     what to choose, and the refusal to expect
```

| Folder | Where in the app | Do this |
|---|---|---|
| `01 New country workspace` | **Platform Admin › Setup**, the Setup tracker | Press **New country workspace**, **Fill from a JSON file** with `workspace.json`, then **Create**. The tracker opens on Bahrain in draft mode with the first stage ("Workspace") done. |
| `02 Add a person` | **Platform Admin › People**, People and roles | Press **Add a person**, **Fill from a JSON file** with `people.json`, pick one person, then save. Repeat for all 7. |
| `03 Grant a role` | **Platform Admin › People**, the role grant dialog | After you save a person, the role grant dialog opens for that person. Find their `display_name` in `role-grants.json`, fill the dialog from it and save. Repeat for each person. |
| `04 Set legal status` | **Process Owner › Adoptions** | Set the legal status and level for each document in `adoptions.json`. Then press **Freeze the library**, once steps 04 and 05 are done. |
| `05 Link required documents` | **Process Owner › Slots**, Required slots | Follow `slots.md`: **Link a document** on each listed slot, and **Mark missing** on one slot, with the reason given. |
| `06 Country pack` | **Process Owner › Packs and versions** | Follow `pack.md`. One Process Owner validates the copied draft. The second freezes the library on Adoptions (**Freeze the library**), presses **Run simulation**, approves it, then presses **Activate for new cases**. |
| `07 New watch source` | **Platform Admin › Watch sources** | Press **Add a source**, **Fill from a JSON file** with `watch-source.json`, then save. |
| `08 Register a connection` | **Platform Admin › Connections** | Press **Register a connection**, **Fill from a JSON file** with `connection.json`, then save. Then **Run conformance check**. A Process Owner (not the Platform Admin) approves it for use. |
| `09 Sign off` | **Process Owner › Sign-off** | Follow `sign-off.md`. Before it, run readiness from the Setup tracker. The sign-off is refused on purpose; the file lists what fails and how to close each gap. The walk-through ends at sign-off. |

## File by file

**01 New country workspace.** One `WorkspaceCreate`: name, country code `BH`, authority name, languages `ar` and `en`, time zone `Asia/Bahrain`, day counting `sun_thu` (Sunday to Thursday), data location, and the template.

*Template: `copy` from `ws-sa`.* The contract offers three: `platform`, `region` and `copy`. A `region` template needs the name of a region, and the server has none: no regional template exists, and the server refuses a `region` request with "No regional templates exist yet. Use the platform template or copy a workspace." So `copy` is the choice. Saudi Arabia is the closest workspace to Bahrain in the seed: same Gulf Cooperation Council rules, same working week (Sunday to Thursday), same languages. Copying it gives Bahrain the same starting rules and workflows: a draft pack, based on the Saudi Arabia pack that is active, opens in Bahrain (step 06). It also gives Bahrain the required slots, all empty. Saudi Arabia has no slots or scenarios of its own yet, so Bahrain gets the platform's standard slots (see step 05) and the platform's standard scenarios (see step 06).

**02 Add a person.** A list of 7 `PersonCreate` items (display name, email, view, title): 2 Case Officers (Hana Mirza, Idris Kamal), 2 Decision Makers (Yusuf Bakr, Salma Rahim), 2 Process Owners (Maryam Ghosh, Karim Odell) and 1 Auditor (Tariq Vance). A pack needs a second Process Owner to approve it, so there are two. All names are invented and none is in the seed.

**03 Grant a role.** One grant per person, in the shape of the role grant request (`RoleGrantCreate`) with one change: the file has `display_name` where the request has `person_id`. Person ids are made by the server when you add the person, so they cannot be in a file. The role grant dialog opens for the person you just added, so use `display_name` to find that person's line. The two Case Officers hold the `assessor` role with a `discipline` (`quality` and `labelling`), because an assessor grant is by discipline. The others hold one role with no discipline. The scope is `Bahrain`, and grants start on 1 November 2026. A person can hold more roles; add them from the same dialog if you want, for example the Process Owner as knowledge steward.

**04 Set legal status.** 12 adoptions of shared documents that are already in the library, all approved versions: 6 GCC, 5 ICH or WHO, and 1 regional. Each item is the adoption request (`AdoptionUpdate`: `adopted`, `legal_status`, `authority_level`, `notes`) plus the `version_id` of the document version it applies to. The version goes in the address of the request, not in its body, and the page knows it from the row you pick.

The contract allows these legal statuses: `in_force`, `draft`, `superseded`, `review_needed` and `not_applicable`. There is no separate "referenced guideline" status. The level carries that meaning: "1 is binding law. 5 is context only." The files follow the pattern already used for Saudi Arabia in the seed:

| Level | Used for | Documents |
|---|---|---|
| 2 | GCC rules the country applies | E-S17, E-S05, E-S06, E-S25, E-S15, E-S03 |
| 3 | ICH and WHO guidelines the country refers to | G-03, G-01, G-02, F-01, F-S05 |
| 4 | Regional guidance used as context | E-S24 |

All twelve are `in_force`. A guideline can be in force as a reference without being Bahrain's law, so the `notes` on each say so. UAE-only laws are not adopted: they are not shared documents.

**05 Link required documents.** `slots.md`: the 14 required slots a new country starts with (10 must-have, 4 nice-to-have, all empty), which slot gets which document, one slot to mark missing, and what that does to readiness. No Bahrain national medicines law text is shipped, so that slot stays empty and shows up in the readiness run. The gap is intended.

**06 Country pack.** `pack.md`: the draft pack that opened in Bahrain when the workspace was created, and who does what. One Process Owner validates it. The second freezes the library, runs a simulation with the standard scenarios, approves it and activates it. The file also names the refusal to expect if approval is tried before the simulation.

**07 New watch source.** One `WatchSourceCreate` for a trusted foreign regulator the seed already watches, "Reference Agency B (synthetic)". It is a staged feed, which delivers synthetic notices in the simulation. It follows the pattern of the staged source in the seed and sets the lowest severity to `review`.

**08 Register a connection.** One `ConnectionCreate` for the built-in mock regulator served at `/mock`, the same one the other workspaces use. It is a mock, so readiness reports its connection check as "mock", not as a real government connection. A Process Owner approves it after the Platform Admin's conformance check; the Platform Admin is refused.

**09 Sign off.** `sign-off.md`: what to choose on the Sign-off page (readiness run, pack version, frozen library, mode `simulation`), which checks fail while the gaps from steps 05 to 08 are open, and how to close them. A first case would need a medicine and a company, which arrive through the government connection: the app cannot add them, so the walk-through stops at sign-off.

## Checking the files

`server/tests/test_demo_files.py` loads every JSON file here and validates it against its contract model, so a change to the contract that breaks a file fails the tests. It reads the files as data only and needs no database.
