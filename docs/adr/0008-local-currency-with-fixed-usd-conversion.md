# ADR-0008: Price work in local currency and fix its USD value at a known date

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

**Extends:** [ADR-0007](0007-record-payments-not-move-money.md)

## Context

Workers are paid in their own country's currency, but funders and program managers compare opportunities across countries in USD. Exchange rates move, so converting at today's rate would change historical reports every day and drift away from what was actually paid.

## Decision

We will set prices and budgets in the opportunity's currency, and store USD values converted once, at a rate tied to a date that doesn't change.

- `PaymentUnit.amount`, `PaymentUnit.org_amount` and `Opportunity.total_budget` are whole numbers in the opportunity's currency, taken from its program.
- Rates are stored in `ExchangeRate` per currency and date, fetched from an external rates service. The rate used for a date is the newest stored rate on or before it; if none is stored, one is fetched for that month.
- Accrued USD on a `CompletedWork` uses the rate at its approval date (`status_modified_date`), which doesn't move when more visits arrive.
- Invoice line items use the rate of the month they are billed under: the approval month for a work's first billing, and the invoice's month for units approved after the work was first billed (ADR-0009). Each `CompletedWorkInvoice` stores the rate it used.
- Payments store both `amount` and `amount_usd`. Worker payments convert at the payment date; organization payments copy the invoice's amounts.
- USD opportunities use a rate of 1.

## Consequences

- Historical USD figures don't change when rates move.
- Accrued USD and invoiced USD for the same work can differ, because they are tied to different dates; reports must say which they show.
- Prices can't have fractions of the local currency unit.
- Approving work in a currency and month with no stored rate makes a synchronous call to the rates service.

## Alternatives considered

- Convert at today's rate on display — rejected because historical reports would change daily and diverge from what was paid.
- Convert at each visit's date — not chosen; payment is accrued per work, and its approval date is the moment it becomes owed.
- Store prices in USD and convert to local currency for payment — not chosen; workers are paid fixed local amounts.
