---
name: current-state
description: 'Map how an existing commcare-connect feature or area works today, from the code, and write it up as a current-state design doc with the design issues it has: where state lives, every entry point and flow, the invariants it relies on, and what is fragile or wrong. Use before writing a spec or starting work that builds on or changes an existing feature, or when asked "how does X work today", "map the current design of X", "what is wrong with how we do X". Not for reviewing a spec (use spec-review) or a diff (use code-review).'
---

# Map the current state of a feature

Write a document that says how a feature works **today**, as the code shows it, and what is wrong with that design. It is the input to a new spec: the spec author should not have to rediscover the system, and should know which existing problems their change will inherit.

The code is the only source of truth. READMEs, docs, ADRs, comments and the user's description are claims to check, not facts. When they disagree with the code, the doc says so.

## 0. Agree the scope

You need two things. Ask the user for whichever is missing, in one question:

- **The area**: a feature, a flow from `docs/core-flows.md`, or a set of models (for example "payment accrual and invoicing").
- **The planned change**, if there is one (for example "PMs can schedule pay rate changes"). It decides which parts get the most depth and which issues matter most. Without one, give every part equal depth.

Write the scope down as a list of what is in and what is out. Stop widening it once the planned change's dependencies are covered.

## 1. Find everything that touches the area

Start from `docs/core-flows.md`, the app READMEs (`opportunity/README.md`, `opportunity/PAYMENTS.md`, `form_receiver/README.md`, ...), the ADRs in `docs/adr/` and the docs in `docs/design/`. Then find, by grepping model and field names (including `F("...__field")` and `values("...")` lookups):

- **Models and fields** in the area, and every **writer** and **reader** of each field.
- **Entry points**, all of them: web views and htmx endpoints, the mobile and automation APIs (`opportunity/api/`), the form receiver, Celery tasks and the beat schedule, imports (`visit_import.py` and others), exports (`data_export/`, `export.py`), Django admin actions, management commands, signals, and `simulation/` steps.
- **External contracts**: what the mobile app, CommCare HQ, ConnectID, exports or emails receive from this area.

When the area spans several apps or flows, split this across parallel Explore agents, one per flow or entry-point group. Ask each for file:line references and the exact order of operations, not a summary of what the code is "for".

## 2. Trace the flows

For each flow, write the steps in order, with `file:line` for each: who or what starts it, what it reads, what it computes, what it writes, and in which transaction. Note:

- **When it runs**: on request, on form ingest, nightly, on a date, on demand.
- **Transactions and locks**: what is atomic (requests are, with `ATOMIC_REQUESTS`; tasks aren't unless they say so), which locks are taken, in which order, and whether a lock is released before the transaction commits.
- **Time**: which time zone and which date each step uses (now, a status date, a visit date, the first of a month).
- **Failure**: what happens if it fails halfway or runs twice.

Read the code; don't infer a flow from function names. Mark anything you couldn't confirm as unverified.

## 3. Find the design issues

Go through the system with the lenses in step 3 of `.claude/skills/spec-review/SKILL.md` (source of truth, money and things that must add up, corrections, time, concurrency, rollout, decisions without a record). Also look for:

- **Derived values stored as facts**: totals, counts and amounts recalculated from current state, so past values change when inputs change.
- **The same fact in several places** with nothing that checks they agree.
- **Invariants that are assumed but not enforced**: no constraint, no check, nothing that would fail if they broke.
- **Matching by heuristics** instead of by reference (for example payments matched to work by order and amount).
- **Deletes and `on_delete` behaviour** that can remove records other records depend on.
- **Docs that are wrong**: a README or doc that says something the code doesn't do.

Each issue needs evidence (`file:line`), a concrete case where it goes wrong, and a severity: **High** (loses, duplicates or misstates money or data, or blocks users), **Medium** (wrong in edge cases, or makes changes in this area risky), **Low** (confusing or costly to work with). If there is a planned change, say for each issue whether the change will inherit it, make it worse, or is a good moment to fix it.

Don't design the fix. One line on the direction a fix would take is enough; the spec does the rest.

## 4. Write the document

Save it as `docs/design/<YYYY-MM-DD>-<area>-current-state.md` and add it to the `nav` in `zensical.toml` next to the other design docs. Use this structure:

```markdown
# <Area>: current state

!!! info "Status: current-state analysis"
    Written <date> at commit <short SHA>. Describes the code as it was then; the code is the source of
    truth where they differ. Written before: <planned change, or "no planned change">.

## Scope
In / out lists.

## Summary
Five lines at most: how it works, and the issues that matter most.

## Terms
Each term as the code uses it (model, field or function), especially where the UI or docs use another word.

## Data model
The models and key fields, how they relate, and a "source of truth" table: each fact, where it is
stored, whether it is stored or derived, who writes it, and what keeps copies in step.

## Entry points
Table: entry point, kind (view, API, task, import, admin, command, ...), file:line, what it changes.

## Flows
One subsection per flow: the steps in order with file:line, when it runs, transactions and locks,
time and time zone, and what happens on failure or re-run.

## Invariants
Each rule the system relies on, and whether it is enforced (constraint, check, test) or only assumed.

## External contracts
What the mobile app, HQ, ConnectID, exports and emails receive from this area.

## Design issues
Most severe first. Each: severity, what is wrong, evidence (file:line), a concrete failure case,
relevance to the planned change, and a one-line fix direction.

## Docs that are out of date
Each doc statement that the code contradicts, with both references.

## Open questions
What you couldn't determine from the code, and who could answer it.
```

Use diagrams only where a flow has branches or several actors; a numbered list is usually clearer.

## 5. Hand over

Tell the user the path, the three most important issues, and anything marked unverified. Suggest an ADR (via `create-adr`) for any High issue that is about how data or money is stored. If a README or `docs/core-flows.md` is wrong, offer to fix it in a separate change. Don't commit unless asked.
