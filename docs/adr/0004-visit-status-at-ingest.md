# ADR-0004: Decide a visit's status when the form arrives, under a claim-limit row lock

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

**Extends:** [ADR-0003](0003-per-worker-claim-limits.md)

## Context

Every delivery form has to be checked against the worker's limits (ADR-0003), against earlier visits to the same beneficiary, and against the opportunity's verification rules before anyone reviews it. Workers often submit several forms at once when their phone syncs, so the forms for one worker arrive concurrently. Two kinds of people judge a visit: the delivering organization verifies the visit, and the program manager reviews the result.

## Decision

We will set each `UserVisit`'s status synchronously while the form is received, and keep the program manager's review in a separate field.

- `UserVisit.status` is one of `pending`, `approved`, `rejected`, `over_limit`, `duplicate` and `trial`. Form processing sets it in a fixed order: `trial` before the opportunity or payment unit starts, then `over_limit`, then `duplicate`, then verification flags, suspension and blocking tasks, then automatic rejection or approval.
- `trial` and `over_limit` are statuses, not separate fields, and they are left out of every limit and duplicate count.
- `UserVisit.over_limit_reasons` records every limit the visit breached.
- `UserVisit.review_status` (`pending`, `agree`, `disagree`) holds the program manager's review. A visit counts toward payment only when `status=approved` and `review_status=agree`. Only auto-approved visits get `agree` without a review.
- Counting visits and checking duplicates happens inside a transaction that holds a `select_for_update` lock on the worker's `OpportunityClaimLimit` row for that payment unit.

## Consequences

- A visit's limit and duplicate outcome is fixed when it arrives; later changes to limits don't re-evaluate it, and reviewers change it by hand.
- Forms that arrive out of order are judged in arrival order, so the visit marked `over_limit` or `duplicate` may not be the later one by `visit_date`.
- The claim-end checks compare against the server's date on arrival, and the daily limit uses the form's `timeStart` date, so a late-forwarded form can be over limit even though it was on time.
- Concurrent forms for the same worker and payment unit are serialized, and forms for different payment units or workers are not.
- Every check that counts visits must run inside the lock, or limits can be exceeded again.
- Status changes by the delivering organization and the program manager don't overwrite each other, but every query for payable visits must filter on both fields.

## Alternatives considered

- A Redis lock keyed on `entity_id` — rejected because different beneficiaries took different locks, so the daily and total limits weren't protected, and its timeout could expire while the transaction was still running.
- A separate field marking trial visits — rejected because a trial visit has no meaningful pending or over-limit state, so it is one more status.
- Fold the program manager's review into `status` — rejected because the delivering organization and the program manager judge the same visit independently.
