# ADR-0013: Allow only one active opportunity per CommCare app

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-20

**Extends:** [ADR-0007](0007-hq-form-forwarding-ingest.md)

## Context

HQ forwards every form from a domain to Connect (ADR-0007). A form names its domain, app and HQ mobile worker, but not the opportunity it was filled in for. Connect has to work out which opportunity a form belongs to before it can check limits or record payable work. Apps are reused: a later opportunity can run on the same app as an earlier one (ADR-0003).

## Decision

We will let only one active opportunity use a CommCare app at a time, and route each form to an opportunity by its app.

- Activating or reactivating an opportunity is refused while another `active` opportunity uses its learn or deliver app. Apps are matched by their HQ app id, in either role.
- The form receiver routes a form to the opportunity that is `active`, not past its `end_date`, and whose learn or deliver `CommCareApp` matches the form's domain and app.
- More than one match rejects the form with a 400.
- No match accepts the form with a 200 and ignores it.

## Consequences

- One app can't run two opportunities at once, for example in two regions or for two organizations; each needs its own copy of the app in HQ.
- A new opportunity on a reused app can't be activated until the earlier one is deactivated, even after the earlier one's `end_date` has passed.
- Forms for an opportunity that has ended or been deactivated are accepted and dropped, so forms HQ forwards late are lost.
- Nothing in the database enforces the rule, so an opportunity activated by another route can share an app, and the receiver then rejects that app's forms until one of them is deactivated.

## Alternatives considered

- Carry the opportunity in each form — not chosen; the forms are defined by the app in HQ, which every opportunity on the app shares.
- Route by the worker's claimed opportunity for the app — not chosen; a worker could hold claims on two opportunities that use the same app.
