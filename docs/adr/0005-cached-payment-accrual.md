# ADR-0005: Cache completion counts and accrued payment on CompletedWork

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

**Extends:** [ADR-0002](0002-completed-work-unit-of-payable-work.md)

## Context

How many times a `CompletedWork` was completed and approved depends on every visit of the work, its payment unit's deliver units and its child payment units (ADR-0002). Computing that on read takes several queries per work. Worker progress, payment reports, exports and invoicing all need it across thousands of works, and computing it inside web requests made accrual time out.

## Decision

We will store the computed counts and amounts on each work, and recalculate them whenever something that affects them changes.

- `CompletedWork` stores `saved_completed_count`, `saved_approved_count`, `saved_payment_accrued`, `saved_org_payment_accrued` and their `_usd` equivalents. `OpportunityAccess.payment_accrued` stores the sum of the worker's `saved_payment_accrued`.
- Accrued amounts are non-zero only when the work's `status` is `approved`: `saved_approved_count × PaymentUnit.amount` for the worker, and `× org_amount` for the organization.
- When `auto_approve_payments` is on, the same recalculation also sets the work's status from its visits. When it is off, work status changes only by import.
- Every action that changes a visit's or work's status triggers a recalculation for the affected worker: receiving a form, approving, rejecting or reviewing visits, status imports and a nightly task.
- Recalculation is either full, or incremental, which skips works that are already approved or have approved units. Form receipt and bulk visit approval use incremental.
- Recalculation for one worker holds a Redis lock per `OpportunityAccess`, with a timeout.
- Code that loops over many works reads the `saved_*` fields, not the live properties.

## Consequences

- Reports, exports and invoicing read stored values, so they cost one query regardless of how many visits a work has.
- A new code path that changes visit or work status must trigger a recalculation, or the cached values go stale without any error.
- Incremental recalculation doesn't revisit already-approved works, so changes to them need a full recalculation.
- A changed `PaymentUnit.amount` or `org_amount` reaches already-cached works only on their next full recalculation.
- The live properties remain on the model for single-work reads, so the same number has two sources that can disagree.
- Recalculation for a worker runs inside the form receiver's request, so a worker with many works makes form receipt slower.

## Alternatives considered

- Compute counts and accrual on read — rejected because it took several queries per work and made accrual time out in web requests.
- Only cache the per-worker total (`OpportunityAccess.payment_accrued`) — rejected because reports and invoicing need the amounts per work and in USD.
- Recalculate only works modified since the last run, by modification date — not pursued; incremental mode by status was simpler to add.
