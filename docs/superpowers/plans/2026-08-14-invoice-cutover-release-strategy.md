# Invoice Cutover Release Strategy

## **Recommendation: one release.**
Ship Schema + Backfill command + Write, Read and Cancel/Reject together (the new code). Deploy at **low-traffic**, then run `backfill_invoice_line_items` once right after.

### The gap

From deploy until the command finishes (a few minutes): pre-existing invoices have no snapshot rows yet, so Read shows them with no line items. Self-heals as the command runs. New invoices are
unaffected — Write snapshots them immediately.

The command is expected to take a short time to run (Approx 2 to 3 mins). (For reference, it took a couple of minutes for similar no. for records in local.)

### What happens to each case in that gap

| Scenario | Outcome |
|---|---|
| New invoice created | Write snapshots it immediately; backfill later finds nothing left to do for it. |
| Existing work gets approved further | Backfill bills the new total whenever it reaches that work — same as any rerun. |
| Existing (pending) invoice cancelled/rejected *before* backfill reaches it | Rollback no-ops (nothing to roll back yet). Backfill's query excludes cancelled/rejected invoices, and re-checks status under its own lock too, so it's never billed. `CompletedWork.invoice` is left stale-linked — harmless, that field is unused going forward. |
| Existing invoice cancelled/rejected *after* backfill already snapshotted it | Rollback correctly reverses the invoiced count on the work and deletes the snapshot row, under its own lock. |


## Alternative: two releases

Release 1: schema + backfill only, old code still serving.
Release 2: everything else, plus a migration that reruns backfill to catch drift between the two releases.

**Trade-off:** two coordinated deploys instead of one, to avoid the one real downside of option 1 —
a few minutes where pre-existing invoices show no line items. 

Since release 2's rerun is a migration, it finishes before the app serves any traffic, so that gap never becomes visible at all.
Every other scenario above already resolves correctly under option 1 on its own, so this is the
only thing the second release actually buys — not a correctness fix, just avoiding a brief cosmetic
gap.
