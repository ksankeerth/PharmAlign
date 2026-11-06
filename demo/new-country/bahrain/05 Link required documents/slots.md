# Link required documents

**Where:** Process Owner › Required slots. Sign in as the Process Owner you added in step 02 (**Maryam Ghosh**), or use **View as** from Platform Admin.

A new country starts with the platform's standard list of required slots: 14 of them, 10 must-have and 4 nice-to-have. Every slot starts empty, with the status **Missing** and no document linked. This file says which shared document goes into which slot for Bahrain, and which slot to leave empty on purpose.

| Importance | Slots a new country gets |
|---|---|
| Must-have | Medicines law in force; Executive regulations of the medicines law; Service fees; Marketing authorisation service; Dossier structure (CTD); Stability testing guideline; Product information and labelling; Pharmacovigilance guideline; Good manufacturing practice; Reliance practice |
| Nice-to-have | Variations; Recall procedure; Pricing rules; Child-resistant packaging |

## Link a document

For each row, press **Link a document** on the slot, choose the document, and save. The slot then shows as filled.

| Slot | Importance | Link this document | Why |
|---|---|---|---|
| Dossier structure (CTD) | must | `G-03` ICH M4 Organisation of the Common Technical Document | The dossier layout every reviewer expects. |
| Stability testing guideline | must | `E-S25` GCC guidelines for stability testing of active pharmaceutical ingredients and finished products | GCC rule, adopted at level 2 in step 04. |
| Product information and labelling | must | `E-S06` GCC Guidance for Presenting the SPC, PIL and Label | GCC rule, adopted at level 2. |
| Pharmacovigilance guideline | must | `E-S24` Guideline on Good Pharmacovigilance Practices (GVP) for Arab Countries | The regional safety-reporting guideline, adopted at level 4 as context. |
| Good manufacturing practice | must | `E-S15` GCC Good Manufacturing Practice cGMP | GCC rule, adopted at level 2. |
| Reliance practice | must | `F-01` WHO TRS 1033 Annex 10: Good Reliance Practices in the Regulation of Medical Products | Supports reliance on trusted regulators. |
| Variations | nice | `E-S03` The GCC Guidelines for Variation Requirements version 6.1 | GCC rule, adopted at level 2. |

Every document in the table is one you adopted in step 04. Link a document you have not adopted and the slot is filled, but the document carries no legal status for Bahrain.

## Mark one slot missing

Every slot is already empty, so this step records the reason. On **Medicines law in force**, press **Mark missing** and give this reason:

> No Bahrain national medicines law text is shipped with the platform. The Process Owner will add the official text when it is obtained.

This gap is intended. Marking a must-have slot missing opens a gap candidate for a person to look at (Process Owner › Gaps), and the library check in the readiness run then reports that must-have slots are not all filled.

## What stays open

These must-have slots have no Bahrain document in the shared library, so leave them as they are. They are national texts a real team would collect from the authority:

- Executive regulations of the medicines law
- Service fees
- Marketing authorisation service

The nice-to-have slots other than Variations (Recall procedure, Pricing rules, Child-resistant packaging) can also stay open.

## What you should see

With the table above, 6 of the 10 must-have slots are filled. Variations, a nice-to-have, is filled too. The Setup tracker's library stage stays open and names the slots still not filled, and the readiness run's library check fails with the same count. The retrieval check fails too: it asks for the document in each country's own slots, and the empty law and fees slots find nothing. That is the expected result for a country whose national texts are not shipped. Link a document to each of the four open must-have slots and the two checks pass.
