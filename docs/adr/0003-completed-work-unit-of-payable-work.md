# ADR-0003: Pay for completed work per beneficiary and payment unit, not per visit

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

## Context

Workers deliver services through CommCare forms. A single form can contain several deliver blocks, and one service often needs several forms: a household visit plus a follow-up, or a required form plus one of several optional ones. Opportunities pay for the service, not for each form, and each service is delivered to a beneficiary that the forms identify by `entity_id`.

A CommCare app can be reused by later opportunities, but only one opportunity using it can be active at a time ([ADR-0013](0013-one-active-opportunity-per-app.md)). The form structure is defined by the app, while prices and limits are set per opportunity.

## Decision

We will record each deliver block as a `UserVisit` and pay for `CompletedWork`, which groups the visits of one worker for one beneficiary under one payment unit.

- `CompletedWork` is unique on `(opportunity_access, entity_id, payment_unit)`.
- A `DeliverUnit` is a deliver block in the opportunity's deliver app, identified by `slug` and unique per opportunity, app and slug. Connect creates an opportunity's deliver units by reading its deliver app from HQ when the opportunity is set up. A program manager links each one to the `PaymentUnit` it counts toward.
- A `PaymentUnit` belongs to an opportunity and holds the price (`amount`, `org_amount`) and the limits.
- A payment unit is completed once for each full set of its deliver units: every required deliver unit, plus at least one `optional` deliver unit when any exist.
- A payment unit with child payment units (`parent_payment_unit`) completes no more often than its children did for the same beneficiary.
- `CompletedWork` is the record that is approved, accrues payment and is invoiced. A `UserVisit` is the record that is verified, flagged and reviewed.

## Consequences

- Repeat services to one beneficiary accrue on the same `CompletedWork`, so its completed and approved counts can exceed one.
- Approval and payment move per beneficiary, but a rejected visit doesn't reject its `CompletedWork`: it is left out of the work's approved count, and the work's other approved completions are still paid.
- A form is rejected if one of its deliver blocks has no `DeliverUnit` linked to a `PaymentUnit`, including blocks added to the app after the opportunity was set up.
- Because deliver units belong to the opportunity, a later opportunity on the same app gets its own units, and configuring them doesn't change the earlier opportunity's payment units.
- Counting completions needs every visit of the work, so the counts are cached rather than computed on read (see [ADR-0006](0006-cached-payment-accrual.md)).
- Reports and exports that count "visits" have to say whether they mean `UserVisit` rows or completions.

## Alternatives considered

- Pay per `UserVisit` — rejected because a service often spans several forms, and paying each form would overpay partial services.
- Pay per form submission (`xform_id`) — rejected because one form can deliver several services to several beneficiaries.
- Share one set of deliver units between every opportunity on the app — dropped because a deliver unit links to one payment unit, so configuring a new opportunity took the units away from the earlier one.
- Have users select a delivery form per opportunity and match incoming forms to it — rejected in favour of reading the deliver blocks from the app, at the cost of keeping Connect's deliver units in sync with the app.
