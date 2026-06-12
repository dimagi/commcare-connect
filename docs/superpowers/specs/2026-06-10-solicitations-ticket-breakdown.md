# Solicitations Module — Tentative Ticket Breakdown

**Status:** Draft for review
**Date:** 2026-06-10
**Author:** Charl Smit (with Claude)
**Companion to:** [`2026-06-09-solicitations-design.md`](2026-06-09-solicitations-design.md)
**Jira:** CCCT-2450 (epic). Ticket IDs below are local labels (`E<n>-T<n>`); map them to
CCCT issues when the backlog is created.

This breaks the design doc into implementable tickets, grouped into epics. Each ticket lists
its **scope**, **acceptance criteria**, **dependencies**, and the **design section** it
implements. Read the design doc first — this doc assumes its decisions and data model.

---

## How to read this

- **Epics are roughly sequential**, but tickets within and across epics parallelize where the
  dependency graph allows (see below).
- Every ticket sits behind the `solicitations` feature switch (E0-T1); no ticket ships a
  user-visible surface without it.
- "Test functions, not view responses" (per `CLAUDE.md`): each ticket's acceptance criteria
  assume the business logic is extracted into testable functions with unit tests, not just
  exercised through HTTP.

### Dependency overview

```
E0 Foundations ─┬─> E1 PM authoring ──┐
                │                      ├─> E5 PM evaluation & award ─> E6 notifications
                ├─> E2 Public marketplace ─> E3 Apply flow ──────────┘
                └─> E4 Review ─────────────────────────────────────┘
```

- **E0 blocks everything** (app, switch, models, score-rollup util).
- **E2 → E3**: the apply flow is reached from the public detail page's CTA.
- **E3 + E4 → E5**: award needs applications (E3) and the reviewer-averaged score (E4).
- **E6 (notifications) is last** because it hangs off transitions defined across E1–E5; it can
  start once those transitions exist, and is itself parallelizable per-event.

### Blocking open question (resolve before E3-T1)

The design's [deferred open question](2026-06-09-solicitations-design.md) — whether
`Organization ↔ LLOEntity` is 1:1, and whether the `Application` keys on `LLOEntity` or
`Organization` — **must be decided before the apply form (E3-T1) is built**, because it
changes the unique constraint and the org-resolution logic. Tracked as **E3-T0** below.

---

## Epic E0 — Foundations

Everything else depends on this epic. Land it first.

### E0-T1 — App scaffold, feature switch, data model + migrations
**Scope:** Create the `commcare_connect/solicitation/` Django app (modelled on `program/`)
and register it. Add the `solicitations` switch name to `flags/switch_names.py` with a
reusable switch-gating mixin/decorator used by every subsequent surface. Define all new
models from §3.4 (`Solicitation`, `EvaluationCriterion`, `SolicitationQuestion`,
`SolicitationAttachment`, `Application`, `ApplicationAnswer`, `ReviewerAssignment`, `Review`,
`CriterionScore`, `Award`), extending `BaseModel` and following the UUID + integer-PK
convention. Finalize `on_delete`, indexes, and the unique constraints. Generate migrations.
**Acceptance:**
- App installed; migrations apply on PostGIS; models importable.
- `SOLICITATIONS = "solicitations"` (or similar) in `switch_names.py`; a reusable gate (CBV
  mixin) returns 404/redirect when the switch is off, verified by test.
- Unique constraints enforced (`one_application_per_llo`, `one_assignment_per_user`,
  `one_review_per_reviewer`), tested at the DB/model level.
- `CriterionScore.score` validated to the fixed 1–10 range (Decision 6).
- No `scope_of_work` field — `description` only (per review).
**Dependencies:** none.
**Design:** §3.1, §3.4, §3.6 (Release Path 3).

### E0-T2 — Score rollup utility
**Scope:** Pure function(s): given a `Review`'s `CriterionScore`s + the solicitation's
criterion weights, compute the weighted overall score normalized to /100; and given an
application's submitted reviews, compute the reviewer-averaged score. Validate weights total
100%.
**Acceptance:**
- Worked example from Decision 6 (74/100) reproduced in a unit test.
- Weight-sum validation (100%) covered, including the publish-time check helper.
- Handles partial scoring (criteria not yet scored) with a defined rule.
**Dependencies:** E0-T1.
**Design:** Decision 6.

---

## Epic E1 — PM workspace: authoring

Org-scoped (`@org_program_manager_required`), under `/a/<org_slug>/solicitations/…`.

### E1-T1 — Solicitations dashboard (org) + sidebar nav
**Scope:** List the org's solicitations (status, deadline, response counts). Add the
**"Solicitations"** item to `layouts/sidenav.html` beside Programs.
**Acceptance:** PM sees only their org's solicitations; counts are correct; non-PM is denied.
**Dependencies:** E0-T1.
**Design:** §3.2 (PM workspace), Part 2 Step 4.

### E1-T2 — Create / edit solicitation form
**Scope:** Multi-part Django form: scope (`description`) / budget (`budget_min/max` +
currency) / dates / contact email / country / delivery type / `estimated_scale`; question
builder; criteria editor (weights, with 100%-total validation on publish); program link
(optional); public/private; **score-visibility toggle** (`hide_scores_until_submit`, default
on). Draft vs publish. Once `active`, structural fields (questions/criteria) lock; descriptive
copy + deadline extensions stay editable.
**Acceptance:**
- Draft saves with partial data; publish enforces required fields + weights-total-100%.
- Structural lock on `active` enforced and tested.
- `hide_scores_until_submit` persists from the form.
**Dependencies:** E0-T1, E0-T2 (weight validation).
**Design:** Part 2 Step 1, §3.2, Decisions 1/5/6.

### E1-T3 — Reviewer & observer management
**Scope:** Add/remove `ReviewerAssignment`s (role = reviewer | observer) on a solicitation,
from a standalone page and the create/edit form. Enforce that assignees are members of the
posting org (Decision 4).
**Acceptance:** assignment creates/deletes records; non-members rejected; one assignment per
user (unique constraint) respected.
**Dependencies:** E0-T1.
**Design:** Decision 4, §3.2.

### E1-T4 — Close / cancel
**Scope:** PM closes a solicitation early or cancels with a reason. `cancelled` from
`draft`/`active`; `closed` by PM hand.
**Acceptance:** reason stored; terminal states block further edits/applications; lifecycle
rules from §3.5 enforced.
**Dependencies:** E0-T1.
**Design:** §3.5, Part 2 Step 4.

---

## Epic E2 — Public marketplace

Top-level, unauthenticated, `/solicitations/`. Shows only `public` + `active`.

### E2-T1 — Marketplace list + public nav entry
**Scope:** Public, scannable card list with filters (type, country, delivery type, deadline).
Add the **"Explore opportunities"** nav item (provisional label, §3.2 note) + home CTA in
`prelogin/home.html`.
**Acceptance:** unauthenticated access works; only `public` + `active` shown; filters work;
non-public/non-active never leak.
**Dependencies:** E0-T1.
**Design:** §3.1, §3.2 (Public marketplace), Part 2 Step 2.

### E2-T2 — Public solicitation detail
**Scope:** Read-only detail: scope, budget range, deadline, attachments. **No questions, no
criteria.** "Apply" CTA routes through login to the apply flow.
**Acceptance:** criteria/questions absent from the response (tested); CTA target correct.
**Dependencies:** E2-T1.
**Design:** §3.2, Decision 5, Part 2 Step 2.

---

## Epic E3 — Apply flow

Authenticated; `/solicitations/<id>/apply/`. Applicant applies as an `LLOEntity`.

### E3-T0 — Resolve org↔LLOEntity identity rule *(spike / decision)*
**Scope:** Decide and document whether to assume/enforce 1:1 `Organization ↔ LLOEntity` for
the apply flow, or key the `Application` on `Organization` instead. Update the design doc and
the unique constraint accordingly. Also decide inline-create name-collision handling
(`LLOEntity.name` is unique).
**Acceptance:** decision recorded in the design doc; E0-T1's constraint updated if needed.
**Dependencies:** E0-T1.
**Design:** Decision 2 + the deferred open question note.

### E3-T1 — Application form (pick/create LLO + answer questions)
**Scope:** After standard login, applicant picks an affiliated `LLOEntity` or creates one
inline (name, short name) — which also creates a backing `Organization` (`org.llo_entity`)
with the user as member. Render the question template (questions visible here for the first
time). Save draft or submit. Application keyed per E3-T0's decision.
**Acceptance:** inline-create produces LLO + org + membership; existing-LLO path reuses the
right org; draft/submit transitions correct; required-question validation on submit;
submission one-shot.
**Dependencies:** E2-T2, E3-T0.
**Design:** Decision 2, Part 2 Step 2, §3.2 (Apply flow).

### E3-T2 — "My applications" list (header dropdown)
**Scope:** Cross-org list of the user's applications with status; filter by type. Entry in the
**header user-profile dropdown** (`layouts/header.html`) — the non-org-scoped surface.
**Acceptance:** shows all the user's applications across orgs; reachable on top-level pages.
**Dependencies:** E3-T1.
**Design:** §3.2 (Apply flow + nav note).

### E3-T3 — Application status / detail + withdraw
**Scope:** View one application's status + answers; withdraw allowed only before the deadline.
**Acceptance:** withdraw blocked after deadline; status reflects lifecycle; `withdrawn_date`
set.
**Dependencies:** E3-T1.
**Design:** §3.5 (Application), Part 2 Step 2.

---

## Epic E4 — Review

Top-level, non-org-scoped, `/solicitations/reviews/…`; gated by `ReviewerAssignment`.

### E4-T1 — ReviewerAssignment authorization layer
**Scope:** A per-screen gate that authorizes via `ReviewerAssignment` for the solicitation
(not org membership), enforcing reviewer vs observer. Reusable across review screens.
**Acceptance:** assigned reviewer/observer allowed; unassigned user denied; observer is
read-only; tested independently of views.
**Dependencies:** E0-T1, E1-T3.
**Design:** Decision 4, §3.6.

### E4-T2 — "My assigned solicitations" list (header dropdown)
**Scope:** One page listing **every** solicitation the user is assigned to, **across all
orgs**. **"My reviews"** entry in the header user-profile dropdown.
**Acceptance:** shows assignments from multiple orgs in one list; opens scoring directly
without entering an org workspace.
**Dependencies:** E4-T1.
**Design:** Decision 4, §3.2 (Review screens), Part 2 Step 3.

### E4-T3 — Application scoring screen
**Scope:** Score each criterion (fixed 1–10, with guidance shown), notes/tags, suggested
reward (`reward_budget`), recommendation (approve/reject/needs-revision; `under_review` is the
unset default). Save draft → submit. Enforce `hide_scores_until_submit`: other reviewers'
scores hidden until own submitted. Observers read-only. Overall score computed via E0-T2.
**Acceptance:** scores persist; overall_score computed correctly; hide-until-submit enforced
(tested); one review per reviewer (unique constraint); observer cannot submit.
**Dependencies:** E4-T1, E0-T2, E3-T1 (needs applications to score).
**Design:** Part 2 Step 3, Decision 6, §3.5 (Review).

---

## Epic E5 — PM evaluation & award

Org-scoped PM screens for triaging and awarding.

### E5-T1 — Applications dashboard (per solicitation)
**Scope:** All applications with status, reviewer-averaged score (E0-T2), recommendations, and
per-application review-completion progress.
**Acceptance:** averaged scores correct; completion progress surfaced; PM-only.
**Dependencies:** E3-T1, E4-T3.
**Design:** §3.2, Part 2 Step 4.

### E5-T2 — Application review detail (PM view)
**Scope:** PM reads one application + **all** reviewers' scores/notes (PM sees everything,
ignoring hide-until-submit).
**Acceptance:** all submitted + draft reviews visible to PM; not to reviewers.
**Dependencies:** E5-T1.
**Design:** §3.2.

### E5-T3 — Shortlist + bulk-reject + `submitted → under_review`
**Scope:** Bulk shortlist (`under_review → shortlisted`, reversible, emails affected
applicants); un-shortlist sends no email; bulk-reject with templated email. Reviewer-initiated
`submitted → under_review` transition (per review). Shortlisting does not freeze review and is
not a gate to award.
**Acceptance:** transitions match §3.5 exactly; un-shortlist silent; reviewers can still score
shortlisted apps; PM-only for shortlist/reject; reviewer-only for the under_review move.
**Dependencies:** E5-T1.
**Design:** §3.5 (Application + shortlisting bullets), Part 2 Step 4.

### E5-T4 — Award + downstream onboarding handoff
**Scope:** Award one or more applicants from the shortlist or directly from `under_review`.
Record `Award` (amount, currency). **Budget hard cap:** if a Program is linked, reject any
award that exceeds the Program's remaining budget (Decision 1). On award: if program-linked,
create/update the `ProgramApplication` to `accepted` and link it on the `Application`; if no
program (standalone EOI), just record the award. Set solicitation `awarded` (reachable from
`active` or `closed`).
**Acceptance:** budget over-cap rejected (tested); `ProgramApplication` set to `accepted` for
program-linked; standalone EOI records award only; solicitation status transitions correctly;
applicant emailed.
**Dependencies:** E5-T1 (and ideally E5-T3, though award-from-under_review must also work).
**Design:** Decision 1, Part 2 Steps 4–5, §3.5.

---

## Epic E6 — Notifications & scheduled tasks

Async via `send_mail_async.delay(...)` (`utils/tasks.py`), following `organization/tasks.py`.
Each event can be built in parallel once its triggering transition exists.

### E6-T1 — Applicant status-change emails
**Scope:** Templated emails on submission confirmation, under-review, shortlisted, awarded,
rejected (incl. bulk rejection). Absolute-URI links into the relevant screen.
**Acceptance:** one email per transition; correct recipient (snapshot
`submitter_email`); no email on un-shortlist.
**Dependencies:** the relevant transitions (E3-T1, E5-T3, E5-T4).
**Design:** §3.7 (applicants), §3.5.

### E6-T2 — PM weekly digest
**Scope:** Celery-beat weekly digest per open solicitation: count of applications awaiting
review + scores for already-scored ones. Follow the `WEEKLY_PERFORMANCE_REPORT` flag pattern.
**Acceptance:** digest content correct; respects the flag; scheduled via beat.
**Dependencies:** E4-T3, E5-T1.
**Design:** §3.7 (PMs).

### E6-T3 — PM broadcast + invited-orgs link email
**Scope:** PM-triggered "email all applicants" broadcast for solicitation updates; and the
PM-sent link to a new solicitation's public page for selected existing orgs.
**Acceptance:** broadcast reaches all current applicants; invited-orgs email links to the
public detail page.
**Dependencies:** E1-T2, E3-T1.
**Design:** §3.7 (PMs, invited orgs), Part 2 Step 1.

### E6-T4 — Daily deadline-close task
**Scope:** Daily Celery-beat task flipping `active → closed` for any solicitation past its
`application_deadline`.
**Acceptance:** past-deadline solicitations close; not-yet-due untouched; idempotent.
**Dependencies:** E0-T1.
**Design:** §3.5 (deadline close bullet).

---

## Cross-cutting

- **Permissions/access test pass** — once E1–E5 land, a dedicated ticket asserting the §3.6
  access matrix end-to-end (public/apply/PM/review/switch) is worth its own ticket to guard
  against leaks (criteria/questions on public pages, cross-org review visibility, switch-off
  404s).
- **Phase 2 is explicitly out of scope** (contracts, AI, reviewer blinding, external-funder
  posting, FAQs, funder analytics) — see §3.8. No tickets here.

## Suggested sequencing for a small team

1. **E0** (one dev, blocking).
2. Then parallelize: **E1** (PM authoring) + **E2** (marketplace) + **E4-T1** (auth layer).
3. **E3** (apply) after E2 + the E3-T0 decision; **E4-T2/T3** (review) after E4-T1 + E3-T1.
4. **E5** (evaluation/award) after E3 + E4.
5. **E6** (notifications) folded in as each transition lands; **E6-T4** can go any time after E0.
