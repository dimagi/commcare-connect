---
name: spec-review
description: 'Critically review a tech spec or design doc for commcare-connect before it is approved: check its claims against the code, find what it leaves out, and question the design it builds on, not just the change it proposes. Use when asked to "review this spec", "poke holes in this design", "what is this design doc missing", or when given a design doc (file, docs/design/ page, Confluence or Google Doc) to assess. Not for reviewing code diffs; use code-review or pr-impact for those.'
---

# Review a tech spec

A spec review fails in two ways: it accepts what the spec says about the current system, and it only reviews the change, not what the change is built on. This skill prevents both.

Be skeptical and precise. Every finding names a section of the spec, and, where it concerns the codebase, a `file:line` that proves it. A finding you can't back with the spec text or the code is a question, not a finding.

## 1. Read the spec and the context it depends on

- Read the whole spec. List its explicit claims ("gives the same results as today", "never changes X", "safe to roll back", "only A and B do X") and its tickets/releases. Each claim gets checked in step 2.
- If `docs/design/` has a current-state doc for this area (written with the `current-state` skill), read it first and start from its design issues. Check its commit against the current code before relying on it.
- Read the ADRs in `docs/adr/` and the design docs in `docs/design/` that touch the same area, `docs/core-flows.md`, and the app READMEs it affects (`opportunity/README.md`, `opportunity/PAYMENTS.md`, `form_receiver/README.md`, ...).

## 2. Check every claim against the code

For each claim and each "today it works like this" statement, find the code and confirm or refute it. Use parallel Explore agents when the spec spans several areas; give each one a list of claims to check, not your conclusions.

Look especially for:

- **Every reader and writer** of each field or model the spec changes (grep the field name, `F("...__field")` lookups, serializers, exports, admin, management commands, simulation steps, migrations). The spec's list is usually incomplete.
- **Behaviour the spec changes silently**: a different date, rate, rounding, ordering or default than the current code uses.
- **Every deletion path** that hits a model the spec protects (`on_delete=PROTECT`), including admin actions, `simulation/reset.py`, management commands and views.
- **Functions the spec calls but never defines** (`set_totals`, `funding_shortfall`, ...). An undefined function in a design is an undefined behaviour.

## 3. Question the foundation

Ask whether the existing design the spec builds on is sound, and whether the spec inherits or deepens its defects. Write this up as its own section, even when the change itself is fine. Use these lenses:

- **Source of truth.** For each fact (an amount, a count, a status), how many places store it? Which one wins when they disagree, and what checks that they agree? Cached totals, copied columns and "recalculated from scratch" values are warning signs.
- **Money and other things that must add up.** Is it a ledger, with append-only signed entries, reversals instead of edits or deletes, and balances derived from entries? Or is it derived from mutable state (counts × current rate)? Can every amount ever shown or billed be explained later? Are payments linked to what they pay for?
- **Corrections.** What happens when something approved, billed or paid is later undone? If the only answer is "it can only go up", "we zero it", or "delete the row", that is a finding.
- **Time.** Which time zone? Effective dates versus when a job actually ran. Rates and other values that change over time: which one applies to past records?
- **Concurrency.** Which locks, in which order, held for how long, and by whom (requests, Celery tasks, nightly jobs)? Is a Redis lock released before the request's transaction commits (`ATOMIC_REQUESTS`)?
- **Rollout.** Migrations and backfills: are they re-runnable, how long do they take, and what happens if they run at the wrong time or fail halfway? Is a release that "can't be rolled back" given a go/no-go check?
- **Decisions without a record.** Is this costly to reverse or does it shape how money, data or access are stored? Then it needs an ADR (see AGENTS.md); say so if none exists.

When the spec defers the fix for a foundational problem to "future improvements", check whether doing it now is cheap compared with doing it later (a second backfill, a second irreversible release).

## 4. Check what is missing

- Every behaviour the spec says is required has a ticket. List requirements with no ticket.
- Every new piece of state the spec relies on is in the model (for example "email once" needs somewhere to record that it was sent).
- Testing: races, backfill correctness, before/after comparison of money totals.
- Docs that AGENTS.md says must change with the code (READMEs, `docs/core-flows.md`, Confluence).
- Each user-facing change says who sees what, including when the change can't happen (unfunded, delayed, cancelled).

## 5. Report

Write in this order, most severe first in each section:

1. **Blocking**: a promise in the spec that is false, or a gap that would lose, duplicate or misstate money or data.
2. **The foundation** (step 3): what the spec inherits, whether it makes it worse, and what to do instead or which ADR is needed.
3. **Significant gaps**: missing definitions, tickets, state, deletion paths, rollout steps.
4. **Smaller points.**
5. **Questions for the author**: at most five, the ones whose answers would change the design.

Each finding: the spec section, what is wrong or missing, the evidence (`file:line` or quoted spec text), and the consequence in a concrete case. Don't pad the report with praise or restate the spec. If the spec is sound in an area, leave that area out.
