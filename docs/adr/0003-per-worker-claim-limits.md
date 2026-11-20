# ADR-0003: Allocate visit limits per worker at claim time

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

**Extends:** [ADR-0002](0002-completed-work-unit-of-payable-work.md)

## Context

An opportunity has a fixed `total_budget` that has to cover what every worker who joins it may earn, including the organization's share (`org_amount`). Workers join at different times, and the program manager needs to give individual workers more or less work than the default without changing the terms for everyone.

## Decision

We will give each worker their own visit limits when they claim an opportunity, and treat the opportunity budget, not the payment unit, as the ceiling.

- Claiming creates an `OpportunityClaim` (with its own `end_date`) and one `OpportunityClaimLimit` per payment unit, holding the worker's `max_visits` and an optional `end_date`.
- `max_visits` starts at the payment unit's `max_total`. `PaymentUnit.max_total` is the default allocation for new claims, not a cap on existing ones.
- A claim is refused unless the remaining budget covers the full per-worker allocation across all payment units.
- The budget committed by claims is `Σ max_visits × (amount + org_amount)` over all claim limits. `remaining_budget = total_budget − committed`.
- A program manager can raise or lower `max_visits`, and move the claim and claim-limit `end_date`, for selected workers. An increase is allowed while the opportunity has remaining budget, so `max_visits` can exceed `max_total`. A decrease can't go below the visits the worker has already made.
- `PaymentUnit.max_daily` is read live from the payment unit, not copied to the claim.
- Claim creation and budget increases lock the `Opportunity` row, so concurrent claims can't spend the same budget.
- Adding a payment unit gives every existing claimant a claim limit for it, funding units in creation order out of the remaining budget. A unit the budget can't fund gets no `OpportunityClaimLimit` row, and its forms from that worker are rejected.

## Consequences

- Changing `PaymentUnit.max_total` or `end_date` affects only workers who claim afterwards; existing claims keep their limits until a program manager adjusts them.
- When the budget runs short, the most recently added payment units are the ones workers can't deliver.
- Budget is committed when a worker claims, not when they deliver, so unused allocations hold budget until they are reduced.
- The per-worker limit and the payment unit's default can disagree, so reports and the mobile app must read the worker's `OpportunityClaimLimit`, not `PaymentUnit.max_total`.
- Organization pay (`org_amount`) reduces how many workers a budget can fund.
- `max_visits` is compared with the worker's visits to each deliver unit of the payment unit, not with completions.

## Alternatives considered

- Read limits live from `PaymentUnit` — rejected because changing a unit's limits would retroactively change every worker's allocation, and the budget could be exceeded.
- Derive remaining budget from the number of workers the budget allows (`total_budget ÷ per-worker cost`) — rejected because program managers allocate more visits to some workers than the payment unit allows, so the worker count no longer measures committed budget.
- Let program managers set a new `max_visits` instead of adding or removing N visits — rejected because adjustments would then need validating against each worker's current limit.
- Commit budget on delivery instead of on claim — rejected because it can't guarantee every claimed worker gets paid for their full allocation.
