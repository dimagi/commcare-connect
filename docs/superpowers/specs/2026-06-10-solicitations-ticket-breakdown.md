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

### Key dependency (apply flow)

The brand-new external applicant path (E3-T1) depends on **CCCT-2494** (signup + probationary
org creation) — the gap CCCT-2494 exists to close. The returning-user path (apply as an existing
org) does not. The `Application` is keyed on `Organization` (design Decision 2); there is no
`LLOEntity` in this flow.

---

## Epic E0 — Foundations

Everything else depends on this epic. Land it first.

### E0-T1 — App scaffold, feature switch, data model + migrations
**Scope:** Create the `commcare_connect/solicitation/` Django app (modelled on `program/`)
and register it. Add the `solicitations` switch name to `flags/switch_names.py` with a
reusable switch-gating mixin/decorator used by every subsequent surface. Define all new
models from §3.4 (`Solicitation`, `EvaluationCriterion`, `SolicitationQuestion`,
`SolicitationAttachment`, `SolicitationInvitation`, `Application`, `ApplicationAnswer`,
`ReviewerAssignment`, `Review`, `CriterionScore`, `Award` — incl. `Award.released_date`),
extending `BaseModel` and following the UUID + integer-PK convention. Finalize `on_delete`,
indexes, and the unique constraints. Generate migrations.
**Acceptance:**
- App installed; migrations apply on PostGIS; models importable.
- `SOLICITATIONS = "solicitations"` (or similar) in `switch_names.py`; a reusable gate (CBV
  mixin) returns 404/redirect when the switch is off, verified by test.
- Unique constraints enforced (`one_application_per_org`, `one_assignment_per_user`,
  `one_review_per_reviewer`, `one_invitation_per_org`), tested at the DB/model level.
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
(optional); **public/private** (private = invite-only, Decision 8); **score-visibility toggle**
(`hide_scores_until_submit`, default on). Draft vs publish. Once `active`, structural fields
(questions/criteria) lock; descriptive copy + deadline extensions stay editable.
**Acceptance:**
- Draft saves with partial data; publish enforces required fields + weights-total-100%.
- Structural lock on `active` enforced and tested.
- `hide_scores_until_submit` persists from the form.
**Dependencies:** E0-T1, E0-T2 (weight validation).
**Design:** Part 2 Step 1, §3.2, Decisions 1/5/6/8.

### E1-T5 — Invite organizations (private solicitations)
**Scope:** PM manages `SolicitationInvitation` records for a private solicitation — invite/remove
organizations (from the create/edit form and a standalone page). Inviting an org not yet on
Connect falls back to emailing a link (deferred to E6-T3). Drives the marketplace "Invited to
you" gating (E2).
**Acceptance:** invitation creates/deletes records; `one_invitation_per_org` respected; only
invited orgs gain access (asserted in E2-T1/E2-T2 gating); public solicitations need no invite.
**Dependencies:** E0-T1.
**Design:** Decision 8, §3.2, Part 2 Step 1.

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

Top-level `/solicitations/`. `public` + `active` shown to everyone; private solicitations
gated by `SolicitationInvitation` (Decision 8).

### E2-T1 — Marketplace list + public nav entry + "Invited to you" section
**Scope:** Public, scannable card list with filters (type, country, delivery type, deadline) of
`public` + `active` solicitations. Add the **"Explore opportunities"** nav item (provisional
label, §3.2 note) + home CTA in `prelogin/home.html`. For **logged-in** users, add an **"Invited
to you"** section listing `private` solicitations their orgs hold a `SolicitationInvitation` for.
**Acceptance:** unauthenticated access works; only `public` + `active` shown publicly; filters
work; `private` never leaks to the public list; logged-in invited users see exactly their orgs'
invited solicitations and nobody else's.
**Dependencies:** E0-T1, E1-T5 (invitations).
**Design:** §3.1, §3.2 (Public marketplace), Decision 8, Part 2 Step 2.

### E2-T2 — Solicitation detail (public + invited-private)
**Scope:** Read-only detail: scope, budget range, deadline, attachments. **No questions, no
criteria.** "Apply" CTA routes through login to the apply flow. A `private` solicitation's detail
is reachable only by an invited org (else 404).
**Acceptance:** criteria/questions absent from the response (tested); CTA target correct; private
detail returns 404 for a non-invited user (tested).
**Dependencies:** E2-T1.
**Design:** §3.2, Decisions 5/8, Part 2 Step 2.

---

## Epic E3 — Apply flow

Authenticated; `/solicitations/<id>/apply/`. Applicant applies as an `Organization` (an existing
membership or a new probationary org via CCCT-2494 signup).

### E3-T1 — Application form (resolve org + answer questions)
**Scope:** After standard login/signup, the applicant resolves the applying `Organization`: pick
one of their memberships, or (brand-new user) create a new probationary org via CCCT-2494's
signup/org-creation flow. Render the question template (questions visible here for the first
time). Save draft or submit. Application keyed on the resolved `Organization`.
**Acceptance:** existing-member path applies as the chosen org; new-user path creates a
probationary org (CCCT-2494) + membership and is **not** blocked from submitting; draft/submit
transitions correct; required-question validation on submit; submission one-shot;
`unique(solicitation, organization)` enforced.
**Dependencies:** E2-T2; **CCCT-2494** (for the new-external-applicant path).
**Design:** Decision 2, Part 2 Step 2, §3.2 (Apply flow).

### E3-T2 — "My applications" list (org sidebar)
**Scope:** Org-scoped list of the current org's applications with status; filter by type. Entry
in the **org sidebar** ("Solicitations" → "My applications").
**Acceptance:** shows only the current org's applications; switching org context changes the
list; not reachable for an org the user isn't a member of.
**Dependencies:** E3-T1.
**Design:** §3.2 (Apply flow + nav note).

### E3-T3 — Application status / detail + withdraw (incl. post-award)
**Scope:** View one application's status + answers; withdraw the application while it is live,
**including after an award**. A post-award withdrawal releases the `Award` (sets `released_date`,
frees its budget), reverts any `ProgramApplication` out of `accepted`, and drops the solicitation
from `awarded` to its prior state when no active awards remain (re-award itself is E5-T4).
**Acceptance:** `withdrawn_date` set; pre-award withdraw works; post-award withdraw releases the
award + reverts the `ProgramApplication` + frees budget (tested); solicitation status recalculated.
**Dependencies:** E3-T1; E5-T4 (shared award/onboarding logic for the post-award path).
**Design:** §3.5 (Application), Part 2 Step 2.

---

## Epic E4 — Review

Org-scoped under `/a/<org_slug>/solicitations/reviews/…`; gated by `ReviewerAssignment`.

### E4-T1 — ReviewerAssignment authorization layer
**Scope:** A per-screen gate that authorizes via `ReviewerAssignment` for the solicitation (not
plain org membership), scoped to the current org (the solicitation must belong to `org_slug`),
enforcing reviewer vs observer. Reusable across review screens.
**Acceptance:** assigned reviewer/observer allowed; unassigned user denied; observer is
read-only; a solicitation from another org is not reachable under the current org's URL; tested
independently of views.
**Dependencies:** E0-T1, E1-T3.
**Design:** Decision 4, §3.6.

### E4-T2 — "My assigned solicitations" list (org sidebar)
**Scope:** A page listing the **current org's** solicitations the user is assigned to.
**"My reviews"** entry in the org sidebar.
**Acceptance:** shows only the current org's assignments; switching org context changes the
list; opens the scoring screen for that org.
**Dependencies:** E4-T1.
**Design:** Decision 4, §3.2 (Review screens), Part 2 Step 3.

### E4-T3 — Application scoring screen
**Scope:** Score each criterion (fixed 1–10, with guidance shown), notes/tags,
recommendation (approve/reject/needs-revision; blank until the reviewer decides —
"in progress" is `submitted_date IS NULL`). Save draft → submit. Enforce `hide_scores_until_submit`: other reviewers'
scores hidden until own submitted. Observers read-only. Overall score computed via E0-T2.
**Reviews are gated on `under_review`:** creating/editing a `Review`/`CriterionScore` is blocked
unless the application's status is `under_review` (shortlisting keeps it `under_review`, so it
stays open; `awarded`/`rejected`/`withdrawn` close it).
**Acceptance:** scores persist; overall_score computed correctly; hide-until-submit enforced
(tested); one review per reviewer (unique constraint); one score per criterion (unique
constraint); scoring blocked when status != under_review (tested); observer cannot submit.
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
**Scope:** Bulk shortlist (set `shortlisted = True`, status stays `under_review`, reversible,
emails affected applicants); un-shortlist (`shortlisted = False`) also emails affected
applicants; bulk-reject with templated email. Reviewer-initiated
`submitted → under_review` transition (per review). Shortlisting is a flag, not a status
transition: it does not freeze review and is not a gate to award.
**Acceptance:** shortlist toggles the `shortlisted` flag without changing status; status
transitions match §3.5 exactly; un-shortlist emails affected applicants; reviewers can still
score shortlisted apps (status remains `under_review`); PM-only for shortlist/reject;
reviewer-only for the under_review move.
**Dependencies:** E5-T1.
**Design:** §3.5 (Application + shortlisting bullets), Part 2 Step 4.

### E5-T4 — Award + downstream onboarding handoff
**Scope:** Award one or more applicants from the shortlist or directly from `under_review`.
Record `Award` (amount in the solicitation's currency). **Budget hard caps (Decision 1):** reject any award whose
amount would push the solicitation's cumulative awarded total over its `budget_max`; and, if a
Program is linked, also reject any award that exceeds the Program's remaining budget. On award: if
program-linked, create/update the `ProgramApplication` to `accepted` and link it on the
`Application`; if no program (standalone EOI), just record the award. Set solicitation `awarded`
(reachable from `active` or `closed`). **Verification gate (CCCT-2494):** award is blocked if the
applicant org is still probationary/unverified. **Re-award:** after an award is released by a
post-award withdrawal (E3-T3), the PM may award another applicant; the budget cap counts only
**active** (non-released) awards.
**Acceptance:** award pushing cumulative total over solicitation `budget_max` rejected (tested,
counting active awards only); award over Program remaining budget rejected when program-linked
(tested); award blocked for an unverified/probationary org (tested); re-award after a release
succeeds within the freed budget (tested); `ProgramApplication` set to `accepted` for
program-linked; standalone EOI records award only; solicitation status transitions correctly;
applicant emailed.
**Dependencies:** E5-T1 (and ideally E5-T3, though award-from-under_review must also work).
**Design:** Decision 1, Part 2 Steps 4–5, §3.5.

---

## Epic E6 — Notifications & scheduled tasks

Async via `send_mail_async.delay(...)` (`utils/tasks.py`), following `organization/tasks.py`.
Each event can be built in parallel once its triggering transition exists.

### E6-T1 — Applicant status-change emails
**Scope:** Templated emails on submission confirmation, under-review, shortlisted,
un-shortlisted (back to under-review), awarded, rejected (incl. bulk rejection). Absolute-URI
links into the relevant screen.
**Acceptance:** one email per transition; correct recipient (snapshot
`submitter_email`); un-shortlist emails the affected applicant.
**Dependencies:** the relevant transitions (E3-T1, E5-T3, E5-T4).
**Design:** §3.7 (applicants), §3.5.

### E6-T2 — PM weekly digest
**Scope:** Celery-beat weekly digest per open solicitation: count of applications awaiting
review + scores for already-scored ones. Follow the `WEEKLY_PERFORMANCE_REPORT` flag pattern.
**Acceptance:** digest content correct; respects the flag; scheduled via beat.
**Dependencies:** E4-T3, E5-T1.
**Design:** §3.7 (PMs).

### E6-T3 — PM broadcast + invitation email
**Scope:** PM-triggered "email all applicants" broadcast for solicitation updates; and an
**invitation email** when an org is invited to a private solicitation (E1-T5) linking to its
"Invited to you" detail, plus the PM's link-broadcast to selected orgs for public solicitations.
**Acceptance:** broadcast reaches all current applicants; invitation email links to the
solicitation detail for the invited org.
**Dependencies:** E1-T2, E1-T5, E3-T1.
**Design:** §3.7 (PMs, invited orgs), Decision 8, Part 2 Step 1.

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
- **External dependency — CCCT-2494 (Restructuring Programs, Opportunities and Organizations).**
  Two distinct relationships:
  - **Apply flow (hard dependency).** The brand-new external applicant path (E3-T1) reuses
    CCCT-2494's signup + probationary org creation — the gap CCCT-2494 exists to close. The
    award step also gates on org verification (E5-T4). The returning-user apply path does not
    depend on it. See design §Decision 2.
  - **Posting gate (forward-alignment only).** CCCT-2494 also adds a Funder / Program-Org
    designation; when it lands, E1's `@org_program_manager_required` gate should migrate to it.
    Neither blocks the other. See design §Decision 3.

## Suggested sequencing for a small team

1. **E0** (one dev, blocking).
2. Then parallelize: **E1** (PM authoring) + **E2** (marketplace) + **E4-T1** (auth layer). Note
   E2's "Invited to you" section needs **E1-T5** (invitations); the public list does not.
3. **E3** (apply) after E2 (the new-applicant path also needs **CCCT-2494**); **E4-T2/T3** (review) after E4-T1 + E3-T1.
4. **E5** (evaluation/award) after E3 + E4.
5. **E6** (notifications) folded in as each transition lands; **E6-T4** can go any time after E0.
