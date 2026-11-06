# Sign off

**Where:** Process Owner › Sign-off (route `/owner/sign-off`). Before this step, a Platform Admin runs a readiness check from Platform Admin › Setup tracker, and the Process Owner freezes the library on Process Owner › Adoptions (**Freeze the library**). The pack comes from step 06.

Sign-off has no file to fill in. Choose these, then write a reason.

| Field | Choose | Notes |
|---|---|---|
| Latest readiness run | the run you started on the Setup tracker | It must be the latest run. If you changed anything since, start a new run first. |
| Pack version | Bahrain's pack, v1 from step 06 | Only approved or active versions can be signed. |
| Frozen library | the snapshot you made with **Freeze the library** | Freeze after step 04 and step 05, so it holds the documents you adopted. |
| Mode | `simulation` | The first mode a country moves to. Shadow and live follow once simulations have run. |
| Reason | for example: "Bahrain set up for simulation only. The national law text is still to be added." | Kept in the audit log with the person's name. |

Press **Sign off**.

## What to expect

In this demo the sign-off is refused. The server lists every guard that failed, not only the first, and the page shows them as they come. For Bahrain, as the steps leave it, you should see one guard:

- **readiness_passed**: "Readiness checks have not all passed." Open the run on the Setup tracker to see which checks failed:
  - **Must-have slots filled** fails: 6 of the 10 are filled. The medicines law, the executive regulations, the service fees and the marketing authorisation service are national texts that are not in the library (step 05).
  - **Retrieval test** fails: the questions ask each country for the document in its own slots. Two of the four that have a right document find it (stability and reliance). The law and fees questions find nothing, because those slots are empty. The detail reads "Document hit at 5: 0.50 on 5 questions" and says 0.80 is needed.
  - **Watch sources healthy** fails if the source from step 07 was never fetched. A new source reads "unchecked" until you press **Fetch now** on Platform Admin › Watch sources.
  - **Government connection** fails with "No approved connection." if the connection from step 08 was not approved. A Process Owner approves it, after the Platform Admin runs the conformance check. The Platform Admin cannot approve it.
  - **Scenarios meet expectations** fails: step 06 ran only the 5 ordinary scenarios, and the check needs all 8 (the two gate scenarios and the model outage as well).

The **Agents healthy** check does not fail. With the 8 agent services running (`scripts/run-local-agents.sh start`, or the containers) the server asks each one and the check passes. When the server answers from recordings instead, no agent service runs, and the check shows as "mock" and counts as passed.

One guard is met if you followed step 06: **pack_approved**, because v1 was simulated, approved by the second Process Owner and activated. If you skipped that, the detail reads "Pack v1 is draft." If you picked a version that is not Bahrain's, it reads "Unknown pack version."

Two more can appear, depending on what you picked:

- **readiness_current**: "Sign off against the latest readiness run." Shown if you picked an older readiness run than the latest one.
- **snapshot_exists**: "Unknown snapshot." Shown if the frozen library is not one of Bahrain's snapshots.

The refusal is a real result, not a fault. It is how the platform keeps a country with a gap from going live. To get a sign-off through, close each gap above:

1. Add the four national texts to the library and adopt them, link each to its slot (step 05), then freeze the library again.
2. Press **Fetch now** on the watch source.
3. Approve the connection as a Process Owner.
4. Run the three scenarios step 06 left out, from **Platform Admin › Simulations**, so all 8 have met their expected outcome.
5. Run readiness again, and sign against the new run and the new frozen library.

With that done, every check passes and the sign-off goes through. It moves Bahrain's mode from draft to simulation, makes the library you named the active one, and shows in the top bar and on the Setup tracker. Until then, the active library is the first one frozen.

The walk-through ends here. A first case needs a medicine and a company, and the app has no screen or route to add them: in a real country they arrive through the government connection. This demo has no such feed for Bahrain, so no case is opened in it.
