# ADR-0002: Pay for completed work per beneficiary and payment unit, not per visit

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

## Context

Workers deliver services through CommCare forms. A single form can contain several deliver blocks, and one service often needs several forms: a household visit plus a follow-up, or a required form plus one of several optional ones. Opportunities pay for the service, not for each form, and each service is delivered to a beneficiary that the forms identify by `entity_id`.

The same CommCare app is reused across opportunities, so the form structure is defined by the app, while prices and limits are set per opportunity.

## Decision

We will record each deliver block as a `UserVisit` and pay for `CompletedWork`, which groups the visits of one worker for one beneficiary under one payment unit.

- `CompletedWork` is unique on `(opportunity_access, entity_id, payment_unit)`.
- A `DeliverUnit` is a deliver block in an app's forms, identified by `slug` and belonging to a `CommCareApp`. Connect creates it the first time a form containing that block arrives. It points at the `PaymentUnit` it counts toward.
- A `PaymentUnit` belongs to an opportunity and holds the price (`amount`, `org_amount`) and the limits.
- A payment unit is completed once for each full set of its deliver units: every required deliver unit, plus at least one `optional` deliver unit when any exist.
- A payment unit with child payment units (`parent_payment_unit`) completes no more often than its children did for the same beneficiary.
- `CompletedWork` is the record that is approved, accrues payment and is invoiced. A `UserVisit` is the record that is verified, flagged and reviewed.

## Consequences

- Repeat services to one beneficiary accrue on the same `CompletedWork`, so its completed and approved counts can exceed one.
- Approval and payment move per beneficiary, so one rejected visit rejects the whole `CompletedWork` when payments are auto-approved.
- A deliver block reaches payment only after a program manager links its `DeliverUnit` to a `PaymentUnit`; until then its forms are rejected.
- Because deliver units belong to the app, two opportunities on the same app share them, and linking one to a payment unit affects both.
- Counting completions needs every visit of the work, so the counts are cached rather than computed on read (see [ADR-0005](0005-cached-payment-accrual.md)).
- Reports and exports that count "visits" have to say whether they mean `UserVisit` rows or completions.

## Alternatives considered

- Pay per `UserVisit` — rejected because a service often spans several forms, and paying each form would overpay partial services.
- Pay per form submission (`xform_id`) — rejected because one form can deliver several services to several beneficiaries.
- Define deliver units per opportunity — rejected because the deliver blocks are fixed by the app's forms, which opportunities share.
- Have users select a delivery form per opportunity and match incoming forms to it — rejected in favour of discovering deliver blocks from the forms that arrive, at the cost of keeping Connect's deliver units in sync with the app by hand.
