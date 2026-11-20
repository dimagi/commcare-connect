# ADR-0009: Bill approved units with a per-work watermark and frozen line items

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

**Extends:** [ADR-0005](0005-cached-payment-accrual.md)

## Context

Service delivery invoices bill a program for the approved work on an opportunity, both worker and organization pay. A `CompletedWork` keeps gaining approved units after it is first billed: repeat services to the same beneficiary, and visits approved late. Invoices used to link each work through a single `CompletedWork.invoice` foreign key and recompute their line items from the work's cached fields on every view. Displayed line items drifted from the invoice's frozen total, units approved after billing were never billed, and units rejected after billing silently changed issued invoices. Late approvals must still be paid.

## Decision

We will bill each work's newly approved units as a delta, and freeze what each invoice billed in its own rows.

- `CompletedWork.invoiced_approved_count` records how many of the work's approved units have been billed. A work is billable while `saved_approved_count > invoiced_approved_count`.
- Creating a service delivery invoice writes one `CompletedWorkInvoice` row per billed work, with the units billed and the worker and organization amounts in local currency and USD, then raises the watermark to `saved_approved_count`.
- Invoice views and exports read `CompletedWorkInvoice` rows, never the work's current fields.
- A first billing must fall inside the invoice's date window by approval date. Units approved after a work was first billed are billed on the next invoice that ends on or after the work's approval date, ignoring the window's start.
- Only units the program manager has agreed to are counted as approved, and an agreed visit can't be rejected afterwards, so billed units can't be taken back.
- Cancelling or rejecting an invoice lowers each work's watermark by the units that invoice billed and deletes its line items.
- Billing locks the works it bills, so two invoices can't bill the same units.
- Invoices are created automatically each month for the previous month, and can also be created by hand. Custom invoices (`service_delivery=False`) have no line items and don't touch the watermark.

## Consequences

- An issued invoice never changes after it is created.
- A work can appear on several invoices, so "which invoice paid for this work" has more than one answer.
- The sum of an opportunity's line items equals its billed amounts, but not its current accrual, which uses a different exchange rate (ADR-0008).
- Any new status change that lowers `saved_approved_count` below the watermark would break the billing invariant, so agreed visits must stay irreversible.
- Drift on invoices created before this change was frozen into their line items, not corrected.

## Alternatives considered

- Keep the `CompletedWork.invoice` foreign key and recompute line items on view — rejected because one key can't record per-invoice amounts, which a cancellation needs to roll back, and line items drifted from the invoice total.
- Lock a work once invoiced and drop later approvals — rejected because workers approved after an invoice is issued must still be paid.
- Classify late units by approval date alone — rejected because the approval date doesn't change when more units are approved, so it can't tell a late delta from a first billing.
