# Solicitations Module — Design Doc

**Status:** Draft for review
**Date:** 2026-06-09
**Author:** Charl Smit (with Claude)
**Release path:** 3 — gated by a global **feature switch** (`solicitations`, via
`flags/switch_names.py`), not a per-org feature flag. The switch is a simple on/off for the
whole module, which also covers the anonymous public marketplace (an org-scoped flag can't
be evaluated for unauthenticated visitors).

This document is split into three parts:
  - Part 1 — Overview: explains the problem - no public discovery; Delivery team stuck on Google Forms + a manual spreadsheet - the idea, and v1 goals.
  - Part 2 — The flow by persona: a single narrative walk through the whole lifecycle — PM creates → org discovers & applies → reviewers score → PM shortlists & awards → awarded LLOs flow into onboarding — with the four personas introduced up front and why each step works the way
  it does.
  - Part 3 — Technical design: module/URLs, the key decisions each framed as "problem it solves → decision → benefit", the data model, lifecycles, permissions, notifications, and Phase 2

---

## Part 1 — Overview: the problem and the idea

### Background

CommCare Connect is a platform for managing community health worker programs. Two
existing concepts matter for this document:

- A **Program** is a funded body of work, owned by a managing organization (run by a
  **Program Manager**, or "PM"). It carries a budget, currency, country, and dates.
- The actual delivery work that flows out of a program is set up as opportunities
  (**ManagedOpportunity** in the code) that local partner organizations carry out.

The partner organizations who carry out the work are **LLOs** — *Locally Led
Organizations*. Today, the only way a PM can bring a new LLO into a program is a private,
invite-driven flow (the existing **ProgramApplication** mechanism): the PM finds an LLO
through their own contacts, invites that specific organization, and the org is moved
through *invited → applied → accepted* statuses.

**The gap:** that flow only works for partners a PM *already knows*. There is no public
place where an LLO can *discover* upcoming work and put itself forward. Separately, the
Delivery team regularly runs **RFPs** (Requests for Proposals) and **EOIs** (Expressions of Interest) to find new partners and test ideas. Today they do this with Google Forms
and a manually maintained spreadsheet of LLOs. It is off-platform, unstructured, and
invisible to the rest of Connect.

### The idea

Build a **Solicitations** module: a public marketplace on Connect where PMs publish RFPs
and EOIs, organizations browse and apply, assigned reviewers score the applications, and
PMs shortlist and award the work. Awarded LLOs then flow into the *existing* program
machinery, so the marketplace becomes a structured front door to onboarding rather than a
parallel system.

### What success looks like (v1 goals)

- A PM can post a solicitation (RFP or EOI), optionally tied to a Program.
- Anyone — logged in or not — can browse public solicitations; external users can sign up
  and apply.
- PMs and reviewers can score applications against defined criteria, shortlist, and award.
- An award cleanly creates/updates the downstream onboarding record so the LLO can be
  brought into the program through the flow that already exists.

---

## Part 2 — How it works: the flow, by persona

This section walks the whole lifecycle once, in order, so a reviewer can follow the
journey before meeting the data model. The four people involved:

| Persona | Who they are |
|---|---|
| **Program Manager (PM)** | A user in a *funding* organization who creates and runs solicitations. ("Funder" in the brief maps to this.) |
| **Reviewer** | A colleague in the same funding org, assigned to score a specific solicitation's applications. Can score, but not publish or award. |
| **Applicant (LLO)** | Someone applying on behalf of a local organization — either a returning Connect user or a brand-new sign-up who found a public solicitation. |
| **Public visitor** | An anonymous browser who can see published, public solicitations but nothing internal. |

### Step 1 — A PM creates and publishes a solicitation

The PM opens their workspace and creates a draft. They give it a title, choose **RFP** or
**EOI**, write the scope of work, set a budget range, pick country / delivery type, and set
an application deadline. They build a **question template** — the questions applicants must
answer — and define **evaluation criteria** with weights (e.g. "Technical Expertise — 25%")
that reviewers will later score against. Each question can be tied to a criterion so
reviewers know which criterion a given answer informs.

They may optionally **link the solicitation to a Program** (some EOIs are exploratory and
have no program yet — that's allowed). They mark it **public** (shows on the open
marketplace) or **private** (only invited orgs), assign one or more reviewers, and
**publish**. They can also email selected existing orgs a link to the public page.


### Step 2 — An organization discovers it and applies

A **public visitor** browses the marketplace, filtering by type, country, delivery type, or
deadline, and opens a solicitation to read its scope, budget range, and deadline. The
**questions and the internal criteria are not shown** at this stage — questions appear only
after they sign in to apply, and criteria are never shown to applicants at all (they're an
internal scoring tool).

When they click **Apply**, they go through the **standard Connect sign-up/login flow** and
then choose who they're applying *as*: they **pick an LLO they're affiliated with** (one of
the organizations they already belong to) or **create a new one inline** — just a name and
short name. Creating one inline also sets up the backing Connect organization behind the
scenes, so a brand-new user becomes a normal org from then on. The application is keyed on
that LLO (Decision 2). They answer the questions and either save a draft or submit.
After submitting they can track status (*submitted → under review → shortlisted →
approved/rejected*) and can withdraw before the deadline. They're emailed on every status
change.

### Step 3 — Reviewers score the applications

Each assigned **reviewer** sees only the solicitations they were added to. They open an
application, score it against each evaluation criterion (with the PM's guidance shown
beside each), add notes/tags, suggest an award amount, and set an overall recommendation
(*approve / reject / needs revision*). By default a reviewer can't see other reviewers'
scores until they've submitted their own, to reduce anchoring bias.

### Step 4 — The PM shortlists and awards

The PM sees a dashboard of all applications with their status, the reviewer-averaged score,
and recommendations. They can move a subset to **shortlisted** (and notify those
applicants), then **award** one or more applicants — RFPs usually produce one award, EOIs
often several. Awarding records the award amount per winner. The PM can **bulk-reject** the
rest with a templated email, and can **close** a solicitation early (with a reason) if the
scope is cancelled.

### Step 5 — Awarded LLOs flow into onboarding

This is the hand-off to the existing system. When a winner is awarded:

- The winner already has a real **Organization** (created or linked when they applied), so
  nothing needs to be created here.
- If the solicitation is **linked to a Program**, Connect creates/updates the existing
  **ProgramApplication** record to *accepted* — which is exactly what *unlocks* the normal
  opportunity-setup flow the platform already has. The marketplace deliberately stops there;
  it does not try to auto-build the opportunity itself.
- If the solicitation has **no Program** (a standalone EOI), the award is simply recorded.
  There's nothing to onboard into yet, which is the correct outcome for an expression of
  interest.

> v1 ends at the award. There is **no contract/signing step** in v1 (that's Phase 2).

---

## Part 3 — Technical design

Everything below serves the flow in Part 2. Each decision notes the problem it solves.

### 3.1 Where the code lives, and the URL surfaces

A new Django app, `commcare_connect/solicitation/`, modelled on the existing `program`
app. The feature has four distinct audiences, so it has four URL surfaces:

| Surface | URL | Why it's separate |
|---|---|---|
| **Public marketplace** | top-level `/solicitations/`, `/solicitations/<id>/` | Must be reachable with no login, like the existing `prelogin` marketing pages. Shows only public, active solicitations. |
| **Apply flow** | `/solicitations/<id>/apply/` (authenticated) | Standard login; the applicant then picks an `LLOEntity` they're affiliated with or creates one inline (which also creates a backing org). The application is keyed on that LLO (Decision 2). |
| **PM workspace** | `/a/<org_slug>/solicitations/…` | Org-scoped management screens; reuses Connect's standard per-org URL pattern and permissions. |
| **Review screens** | top-level `/solicitations/reviews/` (list), `/solicitations/<id>/review/` (scoring) | Authorized by `ReviewerAssignment`, **not** org membership — so a reviewer assigned across several orgs sees *all* their assigned solicitations in one non-org-scoped list (Decision 4). |

The whole module sits behind a global **feature switch** named `solicitations`, following
the existing `flags/switch_names.py` pattern (Release Path 3).

### 3.2 Pages to build and their audiences

The screens below, grouped by the four URL surfaces above. "Audience" is who the page is
*for*; auth requirements follow the surface it belongs to. Each maps back to a step in Part 2.

**Public marketplace** (unauthenticated; shows only `public` + `active` solicitations)

| Page | Audience | Purpose |
|---|---|---|
| Marketplace list | Public visitor / prospective applicant | Browse + filter (type, country, delivery type, deadline) published solicitations as scannable cards. *(Part 2, Step 2)* |
| Solicitation detail (public) | Public visitor / prospective applicant | Read scope, budget range, deadline. **No questions, no criteria.** "Apply" CTA → sign-up/login. |

**Apply flow** (authenticated applicant; standard sign-up/login, applies as an `LLOEntity` — picked or created inline)

| Page | Audience | Purpose |
|---|---|---|
| Application form | Applicant (LLO) | Pick an affiliated `LLOEntity` or create one inline (name, short name), then answer the question template; save draft or submit. Questions visible here for the first time. *(Part 2, Step 2; Decision 2)* |
| "My applications" list | Applicant (LLO) | All applications the user submitted, with status; filter by type. |
| Application status / detail | Applicant (LLO) | View one application's status + answers; withdraw before deadline. |

**PM workspace** (`@org_program_manager_required`)

| Page | Audience | Purpose |
|---|---|---|
| Solicitations dashboard (org) | Program Manager | At-a-glance list of the org's solicitations: status, deadline, response counts. *(Part 2, Step 4)* |
| Create / edit solicitation | Program Manager | Multi-part form: scope/budget/dates + question builder + criteria editor (weights) + program link + public/private + reviewer assignment. Draft vs publish. *(Part 2, Step 1)* |
| Applications dashboard (per solicitation) | Program Manager | All applications with status, reviewer-averaged score, recommendation; shortlist + bulk-reject actions. *(Part 2, Step 4)* |
| Application review detail (PM view) | Program Manager | Read one application + all reviewers' scores/notes (PM sees everything). |
| Award screen | Program Manager | Award one or more applicants: amount/currency (budget check), confirm → downstream onboarding. *(Part 2, Steps 4–5)* |
| Reviewer management | Program Manager | Add/remove reviewers (and observers) on a solicitation. *(Decision 4)* |
| Close / cancel dialog | Program Manager | Close early or cancel with a reason. |

**Review screens** (top-level, non-org-scoped; gated by `ReviewerAssignment`)

| Page | Audience | Purpose |
|---|---|---|
| My assigned solicitations | Reviewer / Observer | One page listing **every** solicitation the user is assigned to, **across all orgs** — not scoped to a single org. *(Part 2, Step 3)* |
| Application scoring | Reviewer | Score each criterion (with guidance), notes/tags, suggested reward, recommendation; submit. Other reviewers' scores hidden until own submitted. Observers get read-only. |

> Notification **emails** are templated content, not pages, and are listed under
> Notifications & emails rather than here.

### 3.3 Key design decisions (with the reasoning behind each)

**Decision 1 — Budget lives on the Solicitation; the Program budget is a hard cap on awards.**
*Problem it solves:* budget needs to be shown to applicants even when a solicitation has no
Program (so we can't rely on a Program always existing), but we also don't want to invent a
new "fund" model. *Decision:* store `budget_min` / `budget_max` + `currency` directly on the
Solicitation. When a Program *is* linked, an award that would exceed the Program's remaining
budget is **rejected (hard block)** — the Program remains the financial source of truth.

**Decision 2 — Applicants apply as an `LLOEntity`; the backing Connect `Organization` is created or linked inline.**
*Problem it solves:* a marketplace applicant identifies as their real-world organization — the
**`LLOEntity`** — and may be brand new to Connect, but the downstream onboarding
(`ProgramApplication`) is keyed on a full **`Organization`**. We need an identity that's
natural to the applicant *and* resolvable to an org at award. *Decision:* after the standard
login, the applicant either **picks an `LLOEntity` they're affiliated with** (the LLO of an
org they already belong to) or **creates one inline** (`name`, `short_name`). Picking an
existing LLO reuses the org they reached it through; creating a new LLO inline also creates a
Connect `Organization` linked to it (`org.llo_entity = the new LLO`), with the user as a
member. The **`Application` is keyed on the `LLOEntity`** — one application per LLO per
solicitation — and also stores the resolved `Organization` so the award has a definite org.
*Benefit:* the applicant identity matches how LLOs think of themselves, and a real
`Organization` always exists by submit time, so there's nothing to create lazily at award —
the award step simply creates/updates the `ProgramApplication` for that org.
*Affiliation* is defined through org membership: the LLOs a user can pick are the
`llo_entity` values of the orgs they belong to (a user with no orgs simply creates one
inline).

**Decision 3 — Reuse the existing `program_manager` capability to decide who can post.**
*Problem it solves:* who is a "Funder" allowed to post solicitations? *Decision:* for v1,
reuse the existing rule — admins of an organization flagged `program_manager=True`, via the
existing `@org_program_manager_required` decorator. No new permission concept. Letting
external funders post (without managing programs) is future scope.

**Decision 4 — Reviewer visibility is scoped per-solicitation via a ReviewerAssignment record.**
*Problem it solves:* the requirement is "a reviewer sees only the solicitations they've been
added to," but Connect's access model is all-or-nothing per organization — it can't express
"these two solicitations but not the others this org runs." *Decision:* a
`ReviewerAssignment(user, solicitation, role)` record *is* the authorization; review screens
check it rather than relying on org membership alone. Reviewers must still be members of the
funding org that posted the solicitation (cross-org reviewers — assigning someone outside the
posting org — are out of scope for v1). But because a single user can belong to *several*
funding orgs and be assigned in each, the review surface is **non-org-scoped**: it lives on
its own top-level path so the user's "My assigned solicitations" list shows every assignment
across all their orgs in one place, and each scoring screen opens directly (no need to first
enter a specific org's workspace). The per-screen gate is the `ReviewerAssignment` for that
solicitation. `role` covers reviewer (can score) vs observer (read-only).

**Decision 5 — Evaluation criteria are strictly internal.**
*Problem it solves:* the prototype showed criteria to applicants, but the brief says they're
an internal scoring tool. *Decision:* criteria are visible only to PMs and assigned
reviewers — never on public pages or the apply form.

**Decision 6 — Each question can link to a criterion; scoring rolls up by weight.**
*Problem it solves:* reviewers need to know which criterion an answer informs, and the PM
sets relative importance. *Decision:* a `SolicitationQuestion` links to at most one
`EvaluationCriterion` (and one criterion can cover several questions — matching the
prototype's shared "Technical Expertise (25%)"). Reviewers score each criterion on a **1–10**
scale; each criterion's **weight is a percentage**, and a solicitation's weights are
**validated to total 100% on publish** (so the PM reads the relative importance straight off
the form). The review's overall score is the weighted average of the criterion scores,
**normalized to /100**. The dashboard's "reviewer-averaged score" is the mean of submitted
reviews' overall scores.

*Example roll-up* — four criteria, weights totalling 100%, each scored out of 10:

| Criterion | Weight | Score (/10) | Contribution (score/10 × weight) |
|---|---|---|---|
| Technical Expertise | 25% | 8 | 20.0 |
| Past Performance | 25% | 6 | 15.0 |
| Cost | 20% | 9 | 18.0 |
| Local Presence | 30% | 7 | 21.0 |
| **Overall** | **100%** | | **74 / 100** |

**Decision 7 — No AI features and no contracts in v1.**
The prototype teased "Generate criteria with AI" and "AI Comparative Review"; the brief
lists AI as future scope, so they're excluded. Contracts (record, upload, and later
e-signature) are also excluded from v1 — the award is the terminal artifact. Model space for
both is described in Phase 2 but nothing is built.

### 3.4 Data model

All new models extend the project's `BaseModel` (which already provides `created_by`,
`modified_by`, `date_created`, `date_modified`) and follow the existing UUID + integer-PK
convention. The sketches below show structure and intent; exact field types, lengths,
`db_index`, and `on_delete` choices are finalized in the technical spec.

**Reused as-is (no schema change):** `LLOEntity` (the applicant identity; picked or created
inline at apply — only its existing `name`/`short_name` fields are used), `Organization`
(created or linked inline at apply, and carried for the award handoff), `User`, `Program`,
`Currency`, `Country`, `DeliveryType`, `ProgramApplication` (set to *accepted* on award),
`ManagedOpportunity` (back-linked later by the existing flow).

**New models** (in `commcare_connect/solicitation/models.py`):

```python
class Solicitation(BaseModel):
    """The posting itself — an RFP or EOI shown on the marketplace."""

    class Type(models.TextChoices):
        RFP = "rfp", _("Request for Proposals")
        EOI = "eoi", _("Expression of Interest")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        ACTIVE = "active", _("Active")
        CLOSED = "closed", _("Closed")
        AWARDED = "awarded", _("Awarded")
        CANCELLED = "cancelled", _("Cancelled")

    solicitation_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=10, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    public = models.BooleanField(default=False)

    description = models.TextField()
    scope_of_work = models.TextField()

    budget_min = models.PositiveBigIntegerField(null=True, blank=True)
    budget_max = models.PositiveBigIntegerField(null=True, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, null=True)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, null=True)
    delivery_type = models.ForeignKey(DeliveryType, on_delete=models.PROTECT, null=True, blank=True)

    expected_start_date = models.DateField(null=True, blank=True)
    expected_end_date = models.DateField(null=True, blank=True)
    application_deadline = models.DateTimeField()
    estimated_scale = models.CharField(max_length=255, blank=True)  # e.g. "5,000 households, 3 districts"
    contact_email = models.EmailField()

    # Solicitations may be program-less (exploratory EOIs); organization is the posting PM org.
    program = models.ForeignKey(Program, on_delete=models.PROTECT, null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)

    hide_scores_until_submit = models.BooleanField(default=True)
    awarded_date = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)


class EvaluationCriterion(BaseModel):
    """Internal-only scored dimension. Never shown to applicants or on public pages."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="criteria")
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)  # reviewer guidance ("what good looks like")
    weight = models.DecimalField(max_digits=5, decimal_places=2)  # relative importance
    score_min = models.PositiveSmallIntegerField(default=1)
    score_max = models.PositiveSmallIntegerField(default=10)
    display_order = models.PositiveSmallIntegerField(default=0)


class SolicitationQuestion(BaseModel):
    """A question on the application form."""

    class QuestionType(models.TextChoices):
        TEXT = "text", _("Free text")
        NUMBER = "number", _("Number")
        CHOICE = "choice", _("Choice")
        FILE = "file", _("File upload")
        DATE = "date", _("Date")

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    help_text = models.TextField(blank=True)
    question_type = models.CharField(max_length=10, choices=QuestionType.choices)
    choice_options = models.JSONField(null=True, blank=True)  # for CHOICE types
    required = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    # Internal link only (Decision 6): a question maps to at most one criterion;
    # a criterion may cover many questions.
    criterion = models.ForeignKey(
        EvaluationCriterion, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions"
    )


class SolicitationAttachment(BaseModel):
    """Supporting documents (scope PDFs, reference materials) for applicants."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="solicitations/attachments/")
    filename = models.CharField(max_length=255)


class Application(BaseModel):
    """One submission from an LLO in response to a solicitation."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        UNDER_REVIEW = "under_review", _("Under Review")
        SHORTLISTED = "shortlisted", _("Shortlisted")
        AWARDED = "awarded", _("Awarded")
        REJECTED = "rejected", _("Rejected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    application_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="applications")
    # The applicant identity: an LLOEntity, picked or created inline at apply (Decision 2).
    llo_entity = models.ForeignKey(LLOEntity, on_delete=models.PROTECT, related_name="solicitation_applications")
    # The backing Connect org (created or linked inline at apply); carried for the award handoff.
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_applications")

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    submitter_name = models.CharField(max_length=255)   # snapshot, kept even if user changes
    submitter_email = models.EmailField()               # snapshot

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    submitted_date = models.DateTimeField(null=True, blank=True)
    withdrawn_date = models.DateTimeField(null=True, blank=True)

    # Link into the existing onboarding flow, set on award (program-linked solicitations only).
    program_application = models.ForeignKey(ProgramApplication, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "llo_entity"], name="one_application_per_llo")
        ]


class ApplicationAnswer(BaseModel):
    """An applicant's response to a single question."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(SolicitationQuestion, on_delete=models.CASCADE)
    answer = models.JSONField(null=True, blank=True)  # text / number / choice / date
    file = models.FileField(upload_to="solicitations/answers/", null=True, blank=True)  # for FILE questions


class ReviewerAssignment(BaseModel):
    """Grants a user scoped access to review ONE solicitation (Decision 4)."""

    class Role(models.TextChoices):
        REVIEWER = "reviewer", _("Reviewer")   # can score
        OBSERVER = "observer", _("Observer")   # read-only

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="reviewer_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.REVIEWER)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "user"], name="one_assignment_per_user")
        ]


class Review(BaseModel):
    """A single reviewer's scoring of one application."""

    class Recommendation(models.TextChoices):
        APPROVE = "approve", _("Approve")
        REJECT = "reject", _("Reject")
        NEEDS_REVISION = "needs_revision", _("Needs Revision")
        UNDER_REVIEW = "under_review", _("Under Review")

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    recommendation = models.CharField(
        max_length=20, choices=Recommendation.choices, default=Recommendation.UNDER_REVIEW
    )
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # computed /100
    notes = models.TextField(blank=True)
    tags = models.JSONField(null=True, blank=True)
    reward_budget = models.PositiveBigIntegerField(null=True, blank=True)  # suggested award amount
    submitted_date = models.DateTimeField(null=True, blank=True)  # null = draft

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["application", "reviewer"], name="one_review_per_reviewer")
        ]


class CriterionScore(BaseModel):
    """Per-criterion score within a review; these roll up into Review.overall_score."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="criterion_scores")
    criterion = models.ForeignKey(EvaluationCriterion, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)


class Award(BaseModel):
    """A decision to award an applicant. A solicitation may have multiple awards."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="awards")
    application = models.ForeignKey(Application, on_delete=models.CASCADE)
    awarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    awarded_date = models.DateTimeField(auto_now_add=True)
    award_amount = models.PositiveBigIntegerField()
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, null=True)
    notes = models.TextField(blank=True)
    # Back-linked later once the existing flow spins up the opportunity (Decision 6, downstream).
    linked_managed_opportunity = models.ForeignKey(
        ManagedOpportunity, on_delete=models.SET_NULL, null=True, blank=True
    )
    # Phase 2 reserves: contract = models.OneToOneField("Contract", ...)
```

**Phase 2 only (not built in v1):** a `Contract` (per award; status, signed copy,
e-signature provider fields) and a program-level versioned `ContractTemplate`.

### 3.5 Status lifecycles

- **Solicitation:** `draft` → `active` (on publish) → `closed` (deadline passed, or PM
  closes early with a reason) → `awarded` (once ≥1 award is made). `cancelled` is a
  reason-tagged terminal action from `draft`/`active`. Drafts are freely editable; once
  `active`, structural fields (questions/criteria) lock while descriptive copy and deadline
  extensions stay editable.
- **Application:** `draft` → `submitted` → `under_review` → `shortlisted` →
  `awarded` | `rejected`. `withdrawn` is an applicant action allowed only before the window
  closes. Submission is one-shot.
- **Review:** `draft` (saved) → `submitted` (finalized). Other reviewers' scores stay hidden
  until submission when `hide_scores_until_submit` is on.
- **Contract:** none in v1 (Phase 2).

### 3.6 Permissions & access (summary)

| Surface | Gate |
|---|---|
| Public marketplace | none — unauthenticated; only `public` + `active` solicitations |
| Apply flow | authenticated; applicant picks an affiliated `LLOEntity` or creates one inline (which also creates a backing org) |
| PM workspace | `@org_program_manager_required` |
| Review screens | non-org-scoped; a `ReviewerAssignment` for that solicitation (reviewer must also be a member of the posting org) |
| Whole module | global feature switch `solicitations` (Release Path 3) |

### 3.7 Notifications & emails

All sent asynchronously via the existing `send_mail_async.delay(...)` task
(`utils/tasks.py`), following the pattern in `organization/tasks.py`, with absolute-URI
links into the relevant screen.

- **To applicants** — on every status change: submission confirmation, under-review,
  shortlisted, awarded, rejected (templated, including bulk rejection).
- **To PMs** — a weekly digest (Celery beat) of how many applications await review per open
  solicitation, plus scores for already-scored applications. Follows the existing
  `WEEKLY_PERFORMANCE_REPORT` flag pattern. Plus a PM-triggered "email all applicants"
  broadcast for solicitation updates.
- **To invited orgs** — a PM-sent link to a new solicitation's public page.

### 3.8 Phase 2 (reserved, not built now)

Contract management: first a manual signed-PDF upload per award, then a program-level
versioned `ContractTemplate`, generate-on-award draft documents, e-signature provider
integration with status-callback webhooks, and two-party counter-signature. Designed to also
support contracts for solicitations that happened off-platform. (Google Docs signing is one
option to explore.) Other future scope from the brief: AI criteria generation and AI
application/review helpers, reviewer blinding, opt-in notifications for new solicitations,
external-funder posting, per-RFP FAQs, and a funder analytics dashboard.
