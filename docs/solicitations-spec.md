# Solicitations Module — Technical Spec

**Status:** Draft for review
**Date:** 2026-09-21
**Ticket:** [CCCT-2450](https://dimagi.atlassian.net/browse/CCCT-2450)
**Release path:** 3 — the whole module ships behind a global feature switch.

---

## Overview

### The words used in this document

CommCare Connect is a platform for running community health worker programs. A few
terms recur below, so they are defined once here.

| Term | What it means |
|---|---|
| **Program** | A funded body of work owned by a managing organization. It carries a budget, a currency, a country and start/end dates. Represented by the existing `Program` model. |
| **Program Manager (PM)** | A user inside the organization that owns and funds the work. In this document the PM is the person who writes, publishes and awards a solicitation. |
| **Partner organization** | A local organization that carries out delivery work on the ground. Elsewhere in Dimagi these are called "LLOs" (Locally Led Organizations); this document calls them partner organizations. |
| **Solicitation** | The new object introduced here: a public posting inviting organizations to apply for work. It is either an RFP or an EOI. |
| **RFP** | Request for Proposals — a posting for a defined piece of work with a budget, where the funder intends to pick a winner and pay for delivery. |
| **EOI** | Expression of Interest — a lighter-weight posting used to find out which organizations are interested and capable, often before any program or budget exists. |
| **Application** | One organization's submission against one solicitation: its answers to the solicitation's questions. |
| **Reviewer** | A colleague in the funding organization who is assigned to score applications on a specific solicitation. A reviewer can score, but cannot publish a solicitation or award it. |
| **Evaluation criterion** | A named, weighted dimension that reviewers score an application against, for example "Technical expertise — 25%". Criteria are internal: applicants never see them. |
| **Shortlist** | A reversible flag a PM puts on applications that are still in contention. It is not a status change. |
| **Award** | The record that an application won, together with the amount awarded. |
| **`ProgramApplication`** | An *existing* Connect model that tracks an organization being brought into a program (`invited → applied → accepted → …`). It is the hand-off point between this new module and the rest of Connect. |
| **Verified organization** | An organization whose details a Dimagi system administrator has confirmed. In the code this is the existing `Organization.verified` boolean. An unverified organization is fully usable for browsing and applying, but cannot be awarded. |

### The problem

Today a Program Manager can only bring a partner organization into a program through a
private, invite-driven flow: the PM already knows the organization, invites it by name, and
the existing `ProgramApplication` record moves through *invited → applied → accepted*.

That works only for partners a PM already knows. There is no public place on Connect where
an organization can discover upcoming work and put itself forward. Separately, Dimagi's
Delivery team regularly runs RFPs and EOIs to find new partners and test ideas, and runs them
entirely off-platform using Google Forms and a hand-maintained spreadsheet. The result is that
partner sourcing is unstructured, not auditable, and invisible to the rest of Connect.

### The proposed solution

Add a **Solicitations** module: a public marketplace on Connect where Program Managers publish
RFPs and EOIs, organizations browse them and apply, assigned reviewers score the applications
against weighted criteria, and the PM shortlists and awards.

The important design choice is where the new module *stops*. It does not try to replace the
onboarding machinery that already exists. When a PM awards an application, the module writes
the existing `ProgramApplication` record to *accepted* and stops there. Accepting that record is
already what unlocks Connect's normal opportunity-setup flow, so the marketplace becomes a
structured front door onto the existing system rather than a second system running beside it.

### The flow, end to end

1. **A PM creates and publishes a solicitation.** They give it a title, pick RFP or EOI, write
   the scope of work, set a budget range, currency, country, delivery type and an application
   deadline. They build the list of questions applicants must answer, and define the weighted
   evaluation criteria reviewers will score against. Each question can be tied to one criterion,
   so a reviewer can see which criterion a given answer informs. The solicitation may optionally
   be linked to a Program — exploratory EOIs often have no program yet, and that is allowed.
   The PM marks it public (visible to everyone) or private (visible only to organizations they
   invite), assigns reviewers, and publishes.

2. **An organization discovers it and applies.** Anyone, logged in or not, can browse the
   marketplace and filter by type, country, delivery type or deadline, and can open a
   solicitation to read its scope, budget range and deadline. A logged-in user additionally sees
   an "Invited" section listing private solicitations their organizations were invited to. The
   questions are not shown at this stage, and the evaluation criteria are never shown to
   applicants at all. Clicking **Apply** sends the user through Connect's standard sign-up or
   login, after which they choose which organization they are applying *as*: a returning user
   picks one of their own organizations; a brand-new user creates one during sign-up. They answer
   the questions and either save a draft or submit. After submitting they can track the status and
   can withdraw at any time, including after an award.

3. **Reviewers score the applications.** Each assigned reviewer sees only the solicitations they
   were added to. They open an application, score it against each criterion on a fixed 1–10
   scale with the PM's guidance shown alongside, add notes and an overall recommendation of
   approve, reject or needs-revision. By default a reviewer cannot see other reviewers' scores
   until they have submitted their own, which reduces anchoring bias.

4. **The PM shortlists and awards.** The PM sees every application with its status, the average
   of the submitted reviewers' scores, and their recommendations. They can shortlist a subset in
   bulk and change that set freely — shortlisting is reversible and does not stop reviewers
   scoring. They then award one or more applicants, recording an amount per winner. For a
   clear-cut RFP they can award without shortlisting first. They can bulk-reject the rest with a
   templated email, and can close a solicitation early with a reason.

5. **Awarded organizations flow into existing onboarding.** The winner already has a real
   Connect organization, created or chosen when they applied, so nothing needs creating at this
   point. If the solicitation is linked to a Program, the module creates or updates the existing
   `ProgramApplication` record to *accepted*, which unlocks the normal opportunity-setup flow.
   If there is no Program, the award is simply recorded.

Version 1 ends at the award. There is no contract generation and no e-signature; the award
carries a file field where a PM can manually upload a contract PDF signed elsewhere.

---

## Technical details

### System architecture

The feature is a new Django app, `commcare_connect/solicitation/`, laid out like the existing
`commcare_connect/program/` app. It adds no new services and no new infrastructure.

| Module | Responsibility |
|---|---|
| `solicitation/models.py` | The eleven new models listed under *Data model changes*. All extend the project's `BaseModel`. |
| `solicitation/views.py` | Four groups of views, one per URL surface (see *User Interface changes*): the anonymous marketplace, the authenticated apply flow, the organization-scoped PM workspace, and the organization-scoped review screens. New views are class-based, per the project's conventions. |
| `solicitation/forms.py` | The solicitation create/edit form, including the question builder and the criteria editor; the application form, whose fields are generated at runtime from the solicitation's questions; and the reviewer scoring form. |
| `solicitation/tables.py` | `django-tables2` tables for the marketplace list, the PM's solicitation dashboard, and the per-solicitation applications dashboard. |
| `solicitation/urls.py` | The organization-scoped routes, mounted under `/a/<org_slug>/`. The public routes are registered separately at the URL root in `config/urls.py`, because they must resolve without an organization in the path. |
| `solicitation/tasks.py` | Two Celery jobs (below), plus the outbound email sends. |
| `solicitation/helpers.py` | The pure logic that the views call: rolling criterion scores up into an overall score, checking an award against the budget caps, and performing the `ProgramApplication` hand-off. Keeping this out of the views is what makes it directly testable, which is what the project's testing guidance asks for. |

**Background jobs.** Two periodic Celery tasks, both on the existing beat schedule:

- A daily task that closes any solicitation whose application deadline has passed, moving it from
  *active* to *closed*. PMs can also close a solicitation by hand before that.
- A weekly digest to Program Managers, giving the count of applications awaiting review per open
  solicitation together with the scores of those already reviewed. This follows the pattern of the
  existing weekly performance report, which is gated by the `WEEKLY_PERFORMANCE_REPORT` waffle flag in `commcare_connect/flags/flag_names.py`.

**Boundary with the rest of Connect.** The only write this module makes outside its own tables is
setting an existing `ProgramApplication` to *accepted* on award (and reverting it if that award is
later released). It deliberately holds no reference to `ManagedOpportunity`: building the actual
opportunity stays with the existing flow, downstream of the `ProgramApplication` hand-off.

**Feature gating.** The whole module — public pages included — sits behind a single global waffle
switch named `solicitations`, added to `commcare_connect/flags/switch_names.py` and checked with
`switch_is_active`. This is Release Path 3: the code ships dark and is turned on centrally.

### Data model changes

**No existing model changes.** The following existing models are reused exactly as they are, with
no new fields and no migrations against them: `Organization`, `User`, `Program`, `Currency`,
`Country`, `DeliveryType` and `ProgramApplication`.

Two existing `Organization` fields carry meaning in this module and are worth calling out, because
both already exist in the code today: `Organization.funder` marks an organization as one that may
post solicitations, and `Organization.verified` marks one a Dimagi system administrator has
confirmed. Nothing needs adding to `Organization` for this feature.

All eleven new models below live in `commcare_connect/solicitation/models.py` and extend
`BaseModel`, which already supplies `created_by`, `modified_by`, `date_created` and
`date_modified`. They follow the project's existing convention of an integer primary key plus a
separate UUID field for anything exposed in a URL. Exact column lengths, index choices and
`on_delete` behavior are settled at implementation time; the tables below fix the structure and
the intent.

#### `Solicitation` — the posting itself

| Field | Type | Notes |
|---|---|---|
| `solicitation_id` | UUID, unique | Public identifier used in URLs. |
| `title` | Char | |
| `type` | Choice | `rfp` or `eoi`. |
| `status` | Choice | `draft`, `active`, `closed`, `cancelled`. Defaults to `draft`. |
| `public` | Boolean | `True` shows it on the open marketplace; `False` makes it invite-only. Defaults to `False`. |
| `description` | Text | The scope of work. |
| `budget_min` | Positive integer, nullable | Lower bound, shown to applicants. Informational only. |
| `budget_max` | Positive integer, nullable | Ceiling. Awards are capped against this — see the validation rules. |
| `currency` | FK → `Currency` | Required. Awards inherit it. |
| `country` | FK → `Country`, nullable | |
| `delivery_type` | FK → `DeliveryType`, nullable | |
| `expected_start_date` | Date, nullable | |
| `expected_end_date` | Date, nullable | |
| `application_deadline` | DateTime | Drives the daily auto-close job. |
| `estimated_scale` | Char, blank | Free text, for example "5,000 households, 3 districts". |
| `contact_email` | Email | Shown to applicants. |
| `program` | FK → `Program`, nullable | Optional. Absent for exploratory EOIs. Its presence is what decides whether an award performs the `ProgramApplication` hand-off. |
| `organization` | FK → `Organization` | The funding organization that posted it. |
| `hide_scores_until_submit` | Boolean | Defaults to `True`: a reviewer cannot see other reviewers' scores until they submit their own. |
| `closure_reason` | Text, blank | Filled in when a PM closes early or cancels. |

#### `EvaluationCriterion` — an internal scored dimension

Never shown to applicants or on any public page.

| Field | Type | Notes |
|---|---|---|
| `solicitation` | FK → `Solicitation`, cascade | |
| `label` | Char | For example "Technical expertise". |
| `description` | Text, blank | Guidance for reviewers on what a good answer looks like. |
| `weight` | Decimal | A percentage. All criteria on one solicitation must total 100. |
| `display_order` | Small integer | |

The scoring scale is a fixed 1–10 and is not configurable per criterion.

#### `SolicitationQuestion` — a question on the application form

| Field | Type | Notes |
|---|---|---|
| `solicitation` | FK → `Solicitation`, cascade | |
| `text` | Text | |
| `help_text` | Text, blank | |
| `question_type` | Choice | `text`, `number`, `choice`, `file`, `date`. |
| `choice_options` | JSON, nullable | Only used by `choice` questions. |
| `required` | Boolean | Defaults to `True`. |
| `display_order` | Small integer | |
| `criterion` | FK → `EvaluationCriterion`, nullable, set-null | Internal link only. A question maps to at most one criterion; one criterion may cover many questions. |

#### `SolicitationAttachment` — supporting documents for applicants

| Field | Type | Notes |
|---|---|---|
| `solicitation` | FK → `Solicitation`, cascade | |
| `file` | File | Stored under `solicitations/attachments/`. |
| `filename` | Char | Original upload name. |

#### `SolicitationInvitation` — grants access to a private solicitation

Public solicitations need no invitations. An invited organization sees the solicitation in the
marketplace's "Invited" section and may apply to it.

| Field | Type | Notes |
|---|---|---|
| `solicitation` | FK → `Solicitation`, cascade | |
| `organization` | FK → `Organization`, cascade | |
| `invited_by` | FK → `User`, nullable, set-null | |

#### `Application` — one organization's submission

| Field | Type | Notes |
|---|---|---|
| `application_id` | UUID, unique | Public identifier used in URLs. |
| `solicitation` | FK → `Solicitation`, cascade | |
| `organization` | FK → `Organization`, cascade | The applying organization. This is the key that matters: `ProgramApplication` downstream is also keyed on organization, so the hand-off needs no translation. |
| `submitted_by` | FK → `User`, nullable, set-null | |
| `submitter_name` | Char | Snapshot at submit time, kept even if the user's own record later changes. |
| `submitter_email` | Email | Snapshot, same reason. |
| `status` | Choice | `draft`, `submitted`, `under_review`, `awarded`, `rejected`, `withdrawn`. |
| `shortlisted` | Boolean, indexed | A PM curation flag, deliberately *not* a status. The dashboard filters on it. |
| `submitted_date` | DateTime, nullable | |
| `withdrawn_date` | DateTime, nullable | |
| `program_application` | FK → `ProgramApplication`, nullable, set-null | Set on award, for program-linked solicitations only. The hand-off link. |

#### `ApplicationAnswer` — one answer to one question

| Field | Type | Notes |
|---|---|---|
| `application` | FK → `Application`, cascade | |
| `question` | FK → `SolicitationQuestion`, cascade | |
| `answer` | JSON, nullable | Holds text, number, choice or date values. |
| `file` | File, nullable | Used only by `file` questions; stored under `solicitations/answers/`. |

#### `ApplicationReviewer` — scoped review access to one solicitation

| Field | Type | Notes |
|---|---|---|
| `solicitation` | FK → `Solicitation`, cascade | |
| `user` | FK → `User`, cascade | |
| `role` | Choice | `reviewer` (can score) or `observer` (read-only). |
| `invited_by` | FK → `User`, nullable, set-null | |

The presence of a row here is what grants a user access to the review screens for that one
solicitation. There is no organization-wide "reviewer" role.

#### `Review` — one reviewer's assessment of one application

| Field | Type | Notes |
|---|---|---|
| `application` | FK → `Application`, cascade | |
| `reviewer` | FK → `User`, cascade | |
| `recommendation` | Choice, blank | `approve`, `reject`, `needs_revision`. |
| `overall_score` | Decimal, nullable | Computed from the criterion scores, normalized to a score out of 100. |
| `notes` | Text, blank | |
| `tags` | JSON, nullable | |
| `submitted_date` | DateTime, nullable | Null means the review is still a draft. |

#### `CriterionScore` — one criterion's score inside a review

| Field | Type | Notes |
|---|---|---|
| `review` | FK → `Review`, cascade | |
| `criterion` | FK → `EvaluationCriterion`, cascade | |
| `score` | Small positive integer | Fixed 1–10 scale. |
| `comment` | Text, blank | |

#### `Award` — the record that an application won

| Field | Type | Notes |
|---|---|---|
| `application` | FK → `Application`, cascade | |
| `awarded_by` | FK → `User`, nullable, set-null | |
| `award_amount` | Positive integer | In the solicitation's currency. |
| `released_date` | DateTime, nullable | Set when an awarded applicant withdraws. A released award keeps its row for audit but no longer counts against the budget, and the PM may award someone else. |
| `comment` | Text, blank | |
| `contract_file` | File, nullable | A version-1 placeholder: the PM manually uploads a contract signed outside the system. Also covers solicitations run off-platform entirely. |

A solicitation may produce several awards, which is normal for an EOI.

#### How the overall score is calculated

Each criterion is scored 1–10 and carries a percentage weight. A review's overall score is the
weighted average of its criterion scores, expressed out of 100. The figure shown on the PM's
dashboard is the mean of the overall scores of the *submitted* reviews.

| Criterion | Weight | Score (of 10) | Contribution (score ÷ 10 × weight) |
|---|---|---|---|
| Technical expertise | 25% | 8 | 20.0 |
| Past performance | 25% | 6 | 15.0 |
| Cost | 20% | 9 | 18.0 |
| Local presence | 30% | 7 | 21.0 |
| **Overall** | **100%** | | **74 of 100** |

#### Data integrity and validation rules

**Uniqueness**, enforced as database constraints:

| Constraint | Scope |
|---|---|
| One application per organization per solicitation | `Application (solicitation, organization)` |
| One answer per question per application | `ApplicationAnswer (application, question)` |
| One invitation per organization per solicitation | `SolicitationInvitation (solicitation, organization)` |
| One reviewer assignment per user per solicitation | `ApplicationReviewer (solicitation, user)` |
| One review per reviewer per application | `Review (application, reviewer)` |
| One score per criterion per review | `CriterionScore (review, criterion)` |
| At most one *live* award per application | `Award (application)`, as a partial constraint limited to rows where `released_date` is null. Released awards are kept for audit and are excluded. |

**Value and business rules:**

| Rule | When it is checked | Behaviour if violated |
|---|---|---|
| Criterion weights on a solicitation must total exactly 100% | On publish (`draft → active`) | Publish is refused. This also means the PM reads relative importance straight off the form. |
| A criterion score is an integer from 1 to 10 | On save, via field validators | Rejected. |
| The sum of all *active* award amounts on a solicitation must not exceed its `budget_max` | At award time, against the real `Award.award_amount` totals | The award is refused outright. There is no soft warning. |
| When a solicitation is linked to a Program, an award must *also* fit within that Program's remaining budget | At award time | The award is refused. The Program stays the financial source of truth; where both caps apply, whichever binds first wins. |
| An award requires the applicant's organization to be verified | At award time | The award is blocked. An unverified organization may still apply and be reviewed in full — only the award is gated, because the award commits budget and flips `ProgramApplication` to *accepted*. |
| A review or criterion score may only be created or edited while the application's status is `under_review` | On save | Blocked. Once an application is awarded, rejected or withdrawn, scoring is closed. |
| Structural fields (questions and criteria) lock once a solicitation is `active` | On edit | Blocked. Descriptive copy and deadline extensions stay editable. |
| A solicitation's currency is required, and awards inherit it | On save | An award cannot name its own currency. |

**Why shortlisting is a boolean and not a status.** Keeping it separate is what makes the rule
above ("scoring is open exactly while status is `under_review`") clean. A shortlisted application
stays `under_review`, so shortlisting never silently opens or closes the review window. The
applicant is still shown the word "Shortlisted" — that label is derived at display time from the
combination of `under_review` plus the flag.

#### Status lifecycles

**Solicitation:**

| From | To | Trigger |
|---|---|---|
| `draft` | `active` | PM publishes. Weights must total 100% at this point. |
| `active` | `closed` | The deadline passes (daily Celery job), or the PM closes early with a reason. |
| `draft` or `active` | `cancelled` | PM cancels with a reason. Terminal. |

Drafts are freely editable. Once active, only descriptive copy and the deadline can change.

**Application:**

| From | To | Trigger |
|---|---|---|
| `draft` | `submitted` | Applicant submits. One-shot — there is no resubmission. |
| `submitted` | `under_review` | A reviewer picks the application up. This is a deliberate manual action, not automatic, because it is what triggers the applicant's "under review" email. |
| `under_review` | `awarded` | PM awards. Shortlisting first is encouraged by the dashboard but not required. |
| `under_review` | `rejected` | PM rejects, individually or in bulk. |
| any live state | `withdrawn` | Applicant withdraws. Allowed at any point, including after an award. |

Withdrawing after an award releases that award: `released_date` is set, the budget it held is
freed, and any `ProgramApplication` the award created is moved back out of *accepted*. The PM may
then award someone else. The solicitation itself drops back to its previous state if no active
awards remain, and stays as it was if other awards do.

Shortlisting and un-shortlisting can happen at any time before an application is awarded or
rejected, are done in bulk from the PM's dashboard, and cannot be done by reviewers. Both
directions email the affected applicant. There is no requirement that reviews be complete before
shortlisting; the dashboard shows per-application review progress so the PM can judge.

**Review:** `draft` (saved but not finalized, indicated by a null `submitted_date`) → `submitted`.
While `hide_scores_until_submit` is on, other reviewers' scores stay hidden until this transition.

**Contract:** there is no contract lifecycle in version 1. The `Award` simply holds a manually
uploaded file.

#### Emails

| Recipient | Sent when |
|---|---|
| Applicant | On every application status change, and additionally on shortlist and un-shortlist, which are not status changes. |
| Program Manager | A weekly digest of applications awaiting review per open solicitation, with scores for those already reviewed. |
| Program Manager (on demand) | A PM-triggered broadcast to all applicants on a solicitation, for updates. |
| Invited organization | When a `SolicitationInvitation` is created for a private solicitation, with a link to it. A PM can also broadcast a link to selected organizations for a public solicitation. |

### User Interface changes

The feature has four distinct audiences, so it has four URL surfaces with different
authentication requirements. Concrete per-page routes are not fixed here; they follow the
conventions already in `commcare_connect/program/urls.py`.

| Surface | Route prefix | Authentication |
|---|---|---|
| **Public marketplace** | `/solicitations/`, `/solicitations/<id>/` | None. Must resolve for anonymous visitors, like the existing `prelogin` marketing pages. Shows public, active solicitations only. |
| **Apply flow** | `/solicitations/<id>/apply/` | Login required. Uses Connect's standard login and sign-up. The applicant then picks which of their organizations they are applying as, or creates one during sign-up. |
| **PM workspace** | `/a/<org_slug>/solicitations/…` | `org_pm_required` (or the `OrgPMRequiredMixin` for class-based views), the existing decorator in `commcare_connect/organization/decorators.py`. Standard per-organization URL scoping applies. |
| **Review screens** | `/a/<org_slug>/solicitations/reviews/` and the per-solicitation scoring screen beneath it | Login required, plus an `ApplicationReviewer` row for that specific solicitation. There is no organization-wide reviewer permission. |

Every surface is additionally behind the `solicitations` feature switch.

**Pages on the public marketplace.** These are visible to everyone; a logged-in user additionally
sees private solicitations their organizations were invited to.

- **Marketplace list** — browse and filter published public solicitations as cards, by type,
  country, delivery type and deadline. Reached from a new item in the public site navigation and
  a home-page call to action.
- **"Invited" section** — for logged-in users only, listing private solicitations their
  organizations hold an invitation for.
- **Solicitation detail** — scope, budget range, deadline, and an "Apply" call to action that
  routes through login or sign-up. **Neither the questions nor the evaluation criteria appear
  here.** Questions become visible only on the apply form; criteria are never shown to applicants
  at all. A private solicitation's detail page is reachable only by an invited organization.

**Pages in the apply flow.**

- **Application form** — pick the organization to apply as, then answer the solicitation's
  questions. Save as draft or submit.
- **"My applications"** — the current organization's applications with their statuses, filterable
  by type. Reached from a "Solicitations" entry in the organization sidebar.
- **Application detail** — one application's status and answers, with a withdraw action.

**Pages in the PM workspace.**

- **Solicitations dashboard** — the organization's solicitations with status, deadline and
  response counts. Reached from a new "Solicitations" item in the organization sidebar, beside
  Programs.
- **Create / edit solicitation** — a multi-part form: scope, budget, dates and contact email; the
  question builder; the criteria editor with weights; the optional Program link; the public or
  private setting; reviewer assignment; and the score-visibility toggle
  (`hide_scores_until_submit`, on by default). Saves as draft or publishes.
- **Applications dashboard**, per solicitation — every application with its status, a shortlisted
  column and filter, the reviewer-averaged score and recommendations, plus bulk shortlist,
  un-shortlist and reject actions.
- **Application review detail** — one application together with every reviewer's scores and notes.
  The PM always sees everything, regardless of the score-visibility toggle.
- **Award screen** — award one or more applicants, entering an amount in the solicitation's
  currency, subject to the budget and verification checks above.
- **Reviewer management** — add and remove reviewers and observers on a solicitation. Also
  reachable from the create/edit form.
- **Close or cancel dialog** — close early or cancel, with a reason.

**Pages on the review screens.**

- **My assigned solicitations** — the solicitations in the *current* organization that this user
  is assigned to. Reached from a new "My reviews" entry in the organization sidebar. A user
  assigned in several organizations switches organization context to see each set.
- **Application scoring** — score each criterion with the PM's guidance shown beside it, add notes
  and tags, set a recommendation, and submit. Other reviewers' scores are hidden until this
  reviewer submits. Observers get the same screen read-only.

New UI follows the project's existing conventions: Django forms rather than hand-written HTML
forms, the predefined style classes in `tailwind/tailwind.css` rather than raw utility classes,
Alpine.js for in-page interactivity and htmx for loading data from the server.

**One open naming question.** The public navigation label for the marketplace is provisionally
"Explore opportunities", which needs confirming with Product because it overlaps with Connect's
existing and unrelated "Opportunities" concept. The URL stays `/solicitations/` and the internal
naming stays "Solicitations" whatever is decided.

### Assumptions and dependencies

**Dependency on CCCT-2494 (Restructuring Programs, Opportunities and Organizations).** This is the
one hard external dependency, and it affects only part of the feature.

- The **returning-applicant path** — a user who already belongs to at least one organization picks
  which one to apply as — works today without CCCT-2494.
- The **brand-new external applicant path** does not. Historically, a public sign-up on Connect was
  blocked pending manual unblocking by Dimagi, which would make a public marketplace unusable.
  CCCT-2494 exists specifically to allow public sign-up plus self-service organization creation,
  where the new organization starts unverified but is not blocked from acting. This module
  deliberately builds no identity or sign-up mechanism of its own and defers entirely to that work.

A consequence worth stating plainly: because a real `Organization` always exists by the time an
application is submitted, there is nothing to create lazily at award time. The deprecated
`LLOEntity` model is not used anywhere in this flow.

**Assumptions about existing platform behavior:**

- `ProgramApplication` set to *accepted* is, and remains, the trigger that unlocks Connect's
  opportunity-setup flow. This module's entire hand-off rests on that.
- `Organization.verified` is, and remains, the signal that a Dimagi system administrator has
  confirmed an organization. The award gate uses it.
- A user's own organization memberships are the only organizations they can apply as. There is no
  search across other organizations, because the organization list is private.
- `Currency`, `Country` and `DeliveryType` reference data is populated in every environment the
  feature runs in. A solicitation cannot be created without a currency.

**Infrastructure and environment:**

- **Celery with a Redis broker, and the database-backed beat scheduler**, both already in use.
  The deadline-close job and the weekly digest both need beat to be running. Neither is
  correctness-critical: if beat stops, solicitations stay open past their deadline until a PM
  closes them by hand, and no digests go out.
- **A working outbound email backend.** Applicants are emailed on every status change, so an
  unconfigured or failing email backend degrades the experience meaningfully, though it does not
  block the workflow itself.
- **Media file storage** for three kinds of upload: solicitation attachments, file answers on
  applications, and the manually uploaded contract PDF on an award. These use standard Django
  `FileField` storage, as the rest of the project does.
- **django-waffle**, already installed, for the `solicitations` switch. Following the project's
  robustness guidance, the absence of the switch record must leave the feature cleanly off rather
  than erroring — no public navigation entry, no reachable routes.
- **No new third-party libraries.** The module uses what the project already has: Django forms,
  django-tables2, Alpine.js, htmx and Tailwind.

**Scope boundaries assumed for version 1:**

- No REST API. Every surface is server-rendered HTML. Nothing is added to `config/api_router.py`.
- No contract generation and no e-signature. Phase 2 adds a `Contract` model per award, carrying
  status, the signed copy and e-signature provider fields, plus a program-level versioned
  `ContractTemplate`. The `Award.contract_file` placeholder is what version 1 offers instead, and
  it also covers solicitations run off-platform.
- The module never creates an opportunity. That stays with the existing flow, downstream of the
  `ProgramApplication` hand-off.
