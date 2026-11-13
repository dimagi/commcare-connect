# ADR-0007: Record payments made outside Connect, to two payees

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

**Extends:** [ADR-0005](0005-cached-payment-accrual.md)

## Context

Workers earn money for approved work, and the organization that runs the delivery is paid for it too. Workers are paid through whatever channel works in their country, such as mobile money or bank transfer, chosen and operated by the organization or program, not by Connect. Program managers still need Connect to show what each worker and organization is owed and what has been paid.

## Decision

We will not move money. Connect records accrued pay and the payments made elsewhere, for two payees.

- Each `PaymentUnit` has a worker price (`amount`) and an organization price (`org_amount`). Accrual for both is stored per work (ADR-0005).
- Worker payments are recorded by uploading a payment file, which creates `Payment` rows linked to the worker's `OpportunityAccess`. The whole file is validated before any row is written.
- Organization payments are recorded when a program manager marks a service delivery `PaymentInvoice` paid, which creates one `Payment` linked to the `organization` and the `invoice`.
- A worker's balance is `OpportunityAccess.payment_accrued − Σ Payment.amount`. Payments are matched to works oldest first only to set `CompletedWork.payment_date`.
- Workers confirm receipt of a payment from the mobile app (`Payment.confirmed`).

## Consequences

- Connect holds no payment credentials and needs no integration with payment providers.
- Whether a payment actually happened depends on someone recording it; Connect can't detect a missed or failed transfer.
- A worker payment is not tied to particular works or payment units (`Payment.payment_unit` is unused), so "what has been paid for" is derived, not recorded.
- Every opportunity budget counts both prices, so organization pay reduces what workers can be allocated (ADR-0003).
- Organization accrual has no per-organization total; reports sum the per-work fields.

## Alternatives considered

- Disburse payments from Connect through a payment provider — not pursued; payment channels differ by country and are run by the organizations.
- Set organization pay per opportunity — replaced by `org_amount` per payment unit so that organization pay can differ by type of delivery.
