# Country pack

**Where:** Process Owner › Packs and versions. Sign in as **Maryam Ghosh**, or use **View as** from Platform Admin. You also need the second Process Owner, **Karim Odell**, added in step 02.

Step 01 copied Saudi Arabia, so Bahrain already has a draft pack: **v1**, a copy of the Saudi Arabia pack that was active when Bahrain was created, with Bahrain's country code. It is a draft, not a live pack. Nothing else in the country uses it until it is approved and activated. You do not write the pack from nothing. You check the copy, then approve it.

Nobody approves their own change, so two people are involved: one validates, the other approves.

## 1. Validate the draft (Maryam Ghosh)

1. Open **Packs and versions**. The **Versions** list shows **v1**, state draft, based on the Saudi Arabia version.
2. Press **Edit** on v1. The editor shows the case types, routes, steps and checks.
3. Press **Open validation**. The validation page lists every rule the pack must meet.
4. The copy has no errors and one warning, about a check that is local practice with no published source. A warning does not block approval. Press **Validate again** if you changed anything.

If the page reports errors, fix them in the editor and press **Save draft**, then validate again.

## 2. Freeze the library, simulate and approve it (Karim Odell)

A simulation runs against a frozen library, so freeze it first. In a country that is still in draft, the first frozen library also becomes its active one.

1. Use **View as** to switch to Karim Odell. Open **Adoptions** (the page from step 04) and press **Freeze the library**, then **Freeze the library** again in the dialog. If you already froze it at the end of step 05, skip this.
2. Open **Packs and versions**, press **Edit** on v1, then **Open validation**, then **Continue to approval**. The **Approve and activate v1** page opens with the guards listed under **Before approval**.
3. Under **1. Simulate**, the standard scenarios are listed and the ordinary ones are ticked. Press **Run simulation**. After a few seconds v1 is **Simulated** and the **pack_simulated** guard is met.
4. Under **2. Approve**, press **Approve v1**, write a reason, for example "Copied from the Saudi Arabia pack and checked for Bahrain.", and press **Approve**.

## 3. Activate it

On the same page, under **3. Activate**, press **Activate for new cases**, then **Activate**. Bahrain's active pack is now v1, and new cases start on it.

## What to expect

Bahrain starts with the platform's standard scenarios, because Saudi Arabia has none of its own to copy. They are in the list under **1. Simulate** from the start. Approval is refused until a simulation has run:

- **pack_simulated**: "Run a simulation before approval."

If you press **Approve v1** before step 3 above, you see this refusal and the pack stays a draft. It is also why **Activate for new cases** only appears once the pack is approved. Approval also checks that someone other than the author approves (**not_own_change**) and that validation passes (**pack_valid**). Both pass here: the draft was written when the workspace was created, not by a Process Owner.

The simulation needs a frozen library. Without one, **Run simulation** is refused with **snapshot_exists**: "This workspace has no knowledge snapshot yet." The draft was copied from a Saudi Arabia version, which is in another workspace. So the **Changes from its base** row on the approval page shows "The base version is in another workspace, so its changes can't be shown here." instead of a count of changes. That is expected.
