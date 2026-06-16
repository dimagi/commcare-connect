# Solicitations Module — Design Doc

**Status:** Draft for review
**Date:** 2026-06-09
**Author:** Charl Smit (with Claude)
**Release path:** 3 — gated by a global **feature switch**.

This document is split into three parts to aid building a mental model before detailing the lower level solution. As such it's recommended to read it from top to bottom:
  - [Part 1](#part-1--overview-the-problem-and-the-idea) — Overview: explains the problem - no public discovery; Delivery team stuck on Google Forms + a manual spreadsheet - the idea, and v1 goals.
  - [Part 2](#part-2--how-it-works-the-flow-by-persona) — The flow by persona: a single narrative walk through the whole lifecycle — PM creates → org discovers & applies → reviewers score → PM shortlists & awards → awarded LLOs flow into onboarding — with the four personas introduced up front and why each step works the way
  it does.
  - [Part 3](#part-3--technical-design) — Technical design (start here if you already have context on the problem): module/URLs, the key decisions each framed as "problem it solves → decision → benefit", the data model, lifecycles, permissions, notifications, and Phase 2

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

### Definitions

A quick glossary of the terms used throughout this doc.

- **Solicitation** — the posting itself: an RFP or EOI shown on the marketplace. The core new
  object in this module. Hey folks, just a reminder that I'm offline for the next two weeks.
- **Funder org** — the organization that posts solicitations. (Posting is gated on the
  `program_manager` flag today; migrates to the forthcoming "Funder" designation in
  CCCT-2494 — see §3.3.)
- **Applicant** — the LLO-side user submitting an application, either a returning Connect
  user or a brand-new sign-up who found a public solicitation.
- **PM** (Program Manager) — the funder-org user who authors, publishes, and awards
  solicitations.
- **Reviewer** — a colleague in the same funding org, assigned to score a specific
  solicitation's applications; can score but not publish or award.
- **ProgramApplication** — the *existing* invite-driven onboarding record. An award sets it
  to *accepted*, unlocking the normal join flow. The handoff point between this module and
  the rest of Connect.
- **Marketplace** — the public listing where solicitations are browsed and filtered.
- **Application** — one org's submission to a solicitation: answers to the question template,
  saved as a draft or submitted.
- **Evaluation criterion** — a weighted dimension reviewers score against; criteria weights
  must total 100% on publish.
- **Shortlist** — a reversible PM-selected subset of applications under consideration before
  award.
- **Award** — the record that an applicant won, including the award amount; the trigger for
  downstream onboarding.

---

## Part 2 — How it works: the flow, by persona

This section walks the whole lifecycle once, in order, before meeting the data model. The four people involved:

| Persona | Who they are |
|---|---|
| **Program Manager (PM)** | A user in a *funding* organization who creates and runs solicitations. |
| **Reviewer** | A colleague in the same funding org, assigned to score a specific solicitation's applications. Can score, but not publish or award. |
| **Applicant (LLO)** | Someone applying on behalf of a local organization — either a returning Connect user or a brand-new sign-up who found a public solicitation. |
| **Public visitor** | An anonymous browser who can see published, public solicitations but nothing internal. |

### Step 1 — A PM creates and publishes a solicitation

The PM (in a funder org) opens their workspace and creates a draft. They give it a title, choose **RFP** or
**EOI**, write the scope of work, set a budget range, pick country / delivery type, and set
an application deadline. They build a **question template** — the questions applicants must
answer — and define **evaluation criteria** with weights (e.g. "Technical Expertise — 25%")
that reviewers will later score against. Each question can be tied to a criterion so
reviewers know which criterion a given answer informs.

They may optionally **link the solicitation to a Program** (some EOIs are exploratory and
have no program yet — that's allowed). They mark it **public** (shown on the open marketplace to
everyone) or **private** (invite-only), assign one or more reviewers, and **publish**. For a
**private** solicitation they **invite specific organizations** — each invite creates an access
record — and only invited orgs can see or apply.


### Step 2 — An organization discovers it and applies

A **public visitor** browses the marketplace, filtering by type, country, delivery type, or
deadline, and opens a solicitation to read its scope, budget range, and deadline. A **logged-in**
user additionally sees an **"Invited to you"** section listing the private solicitations their
orgs have been invited to. The **questions and the internal criteria are not shown** at this
stage — questions appear only after they sign in to apply, and criteria are never shown to
applicants at all (they're an internal scoring tool).

When they click **Apply**, they go through the **standard Connect sign-up/login flow** (the
shared signup owned by CCCT-2494) and then choose which **organization** they're applying *as*:
a returning user **picks one of their organizations**; a brand-new user **creates a new
organization** as part of signup (it starts *probationary*, pending Dimagi verification, but
that doesn't block them from applying). The application is keyed on that **organization**. They
answer the questions and either save a draft or submit.
After submitting they can track status (*submitted → under review → awarded/rejected*, with a
**shortlisted** flag surfaced while still under review) and can **withdraw** — including after an
award, which releases that award so the PM can re-award. They're emailed on every status change.

### Step 3 — Reviewers score the applications

Each assigned **reviewer** sees only the solicitations they were added to. They open an
application, score it against each evaluation criterion (with the PM's guidance shown
beside each), add notes/tags, and set an overall recommendation
(*approve / reject / needs revision*). By default a reviewer can't see other reviewers'
scores until they've submitted their own, to reduce anchoring bias.

### Step 4 — The PM shortlists and awards

The PM sees a dashboard of all applications with their status, the reviewer-averaged score,
and recommendations. They can **shortlist** a subset in bulk (which emails those applicants),
refine that set freely — shortlisting is reversible and reviewers keep scoring throughout —
and then **award** one or more applicants; for a clear-cut RFP they can also award straight
from review without shortlisting. Awarding records the award amount per winner. The PM can **bulk-reject** the
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
- If the solicitation has **no Program**, the award is simply recorded.

> v1 ends at the award. There is **no contract *generation* or e-signature** in v1 (that's
> Phase 2) — but the award carries a placeholder field where a PM can **manually upload an
> out-of-band signed contract PDF**.

---

## Part 3 — Technical design

Everything below serves the flow in Part 2. Each decision notes the problem it solves.

### 3.1 Where the code lives, and the URL surfaces

A new Django app, `commcare_connect/solicitation/`, modelled on the existing `program`
app. The feature has four distinct audiences, so it has four URL surfaces:

| Surface | URL | Why it's separate |
|---|---|---|
| **Public marketplace** | top-level `/solicitations/`, `/solicitations/<id>/` | Must be reachable with no login, like the existing `prelogin` marketing pages. Shows only public, active solicitations. |
| **Apply flow** | `/solicitations/<id>/apply/` (authenticated) | Standard login/signup (owned by CCCT-2494); the applicant picks one of their organizations or creates a new (probationary) org during signup. The application is keyed on that organization. |
| **PM workspace** | `/a/<org_slug>/solicitations/…` | Org-scoped management screens; reuses Connect's standard per-org URL pattern and permissions. |
| **Review screens** | org-scoped `/a/<org_slug>/solicitations/reviews/` (list), `/a/<org_slug>/solicitations/<id>/review/` (scoring) | Lists only the current org's solicitations the user is assigned to. A `ReviewerAssignment` record determines which solicitations the reviewer sees. A reviewer assigned in several orgs switches org context to see each. |

The whole module sits behind a global **feature switch** named `solicitations`, following
the existing `flags/switch_names.py` pattern (Release Path 3).

### 3.2 Pages to build and their audiences

The screens below, grouped by the four URL surfaces above. "Audience" is who the page is
*for*; "Reached via" is how a user navigates to it; auth requirements follow the surface it
belongs to. Each maps back to a step in Part 2.


**Public marketplace** (`public` + `active` shown to everyone; logged-in users also see private solicitations their orgs are invited to)

| Page | Audience | Reached via | Purpose |
|---|---|---|---|
| Marketplace list | Public visitor / prospective applicant | New **"Explore opportunities"** item in the public site nav + home CTA | Browse + filter (type, country, delivery type, deadline) published `public` solicitations as scannable cards. *(Part 2, Step 2)* |
| "Invited to you" section | Logged-in member of an invited org | Marketplace (logged-in only) | Lists `private` solicitations the user's orgs hold a `SolicitationInvitation` for. *(Part 2, Step 2)* |
| Solicitation detail (public) | Public visitor / prospective applicant | Marketplace cards → detail | Read scope, budget range, deadline. **No questions, no criteria.** "Apply" CTA → sign-up/login. (Private detail reachable only by invited orgs.) |

**Apply flow** (authenticated applicant; standard sign-up/login via CCCT-2494, applies as an `Organization` — an existing membership or a new probationary org)

| Page | Audience | Reached via | Purpose |
|---|---|---|---|
| Application form | Applicant (LLO) | **"Apply" CTA** on the public detail page (routes through login/signup) | Pick which organization to apply as (or create one during signup), then answer the question template; save draft or submit. Questions visible here for the first time. *(Part 2, Step 2)* |
| "My applications" list | Applicant (LLO) | **Org sidebar** ("Solicitations" → "My applications") | The current org's applications, with status; filter by type. Org-scoped. |
| Application status / detail | Applicant (LLO) | From "My applications" | View one application's status + answers; withdraw before deadline. |

**PM workspace** (`@org_program_manager_required`)

| Page | Audience | Reached via | Purpose |
|---|---|---|---|
| Solicitations dashboard (org) | Program Manager | New **"Solicitations"** item in the org sidebar, beside Programs | At-a-glance list of the org's solicitations: status, deadline, response counts. *(Part 2, Step 4)* |
| Create / edit solicitation | Program Manager | From the Solicitations dashboard | Multi-part form: scope/budget/dates + contact email + question builder + criteria editor (weights) + program link + public/private + reviewer assignment + score-visibility toggle (`hide_scores_until_submit`, default on). Draft vs publish. *(Part 2, Step 1)* |
| Applications dashboard (per solicitation) | Program Manager | From the Solicitations dashboard | All applications with status, a **shortlisted** flag (column + filter; shown alongside `under_review`, since shortlisting doesn't change status), reviewer-averaged score, recommendation; shortlist/un-shortlist + bulk-reject actions. *(Part 2, Step 4)* |
| Application review detail (PM view) | Program Manager | From the Applications dashboard | Read one application + all reviewers' scores/notes (PM sees everything). |
| Award screen | Program Manager | From the Applications dashboard | Award one or more applicants: amount in the solicitation's currency (budget check), confirm → downstream onboarding. *(Part 2, Steps 4–5)* |
| Reviewer management | Program Manager | From the Solicitations dashboard (also on the create/edit form) | Add/remove reviewers (and observers) on a solicitation. |
| Close / cancel dialog | Program Manager | From the Solicitations dashboard | Close early or cancel with a reason. |

**Review screens** (org-scoped under `/a/<org_slug>/`; gated by `ReviewerAssignment`)

| Page | Audience | Reached via | Purpose |
|---|---|---|---|
| My assigned solicitations | Reviewer / Observer | New **"My reviews"** entry in the org sidebar | Lists the **current org's** solicitations the user is assigned to. A reviewer assigned in several orgs switches org context to see each. *(Part 2, Step 3)* |
| Application scoring | Reviewer | From "My assigned solicitations" | Score each criterion (with guidance), notes/tags, recommendation; submit. Other reviewers' scores hidden until own submitted. Observers get read-only. |

> **Note:** the public nav label **"Explore opportunities"** is provisional and **to be
> confirmed with Product** — it overlaps with Connect's existing "Opportunities" concept, so
> the wording may change. The URL stays `/solicitations/` and the internal/PM naming stays
> "Solicitations" regardless.

### 3.3 Key design decisions (with the reasoning behind each)

**Decision 1 — Budget lives on the Solicitation; awards are hard-capped by the Solicitation budget (and the Program budget when linked).**
*Problem it solves:* budget needs to be shown to applicants even when a solicitation has no
Program (so we can't rely on a Program always existing), but we also don't want to invent a
new "fund" model. *Decision:* store `budget_min` / `budget_max` + `currency` directly on the
Solicitation. A solicitation can produce several awards (especially EOIs), so the **sum of all
**active** award amounts on a solicitation must fit within its `budget_max`** — an award that
would push the cumulative awarded total over `budget_max` is **rejected (hard block)**. (Awards
released by a post-award withdrawal don't count toward the total — see §3.5.) The cap is enforced
on the actual `Award.award_amount` totals at award time; the award amount is set by the PM
(reviewers do not suggest amounts). When a Program *is*
linked, the award must **also** fit within the Program's remaining budget — the Program remains the
financial source of truth. Both caps apply where relevant; an award must satisfy whichever is
binding.

**Decision 2 — Applicants apply as an `Organization`; signup and org creation are delegated to CCCT-2494.**
*Problem it solves:* a marketplace applicant must resolve to a Connect `Organization` (the
downstream `ProgramApplication` is `Organization`-keyed), but a brand-new external applicant may
have no account and no org yet — and historically such signups were **blocked pending manual
Dimagi unblocking**. *Decision:* the apply flow does **not** build its own identity/signup
mechanism. It reuses the canonical flow from the *Restructuring Programs, Opportunities and
Organizations* work (**CCCT-2494**), which exists precisely to let the public respond to
solicitations without manual unblocking:
  - **Authenticate** via the standard Connect login/signup.
  - **Resolve the applying `Organization`:** a user who already belongs to one or more orgs
    **picks which org to apply as** (their own memberships only — the org list is private, so
    there is no search of other orgs); a user with no org **creates a new probationary org**
    through CCCT-2494's org-creation flow (becoming its first Org Admin). A probationary org is
    pending System-Admin verification, but the user is **not blocked** — they can submit the
    application immediately.

The **`Application` is keyed on the resolved `Organization`** — `unique(solicitation,
organization)`, one application per org per solicitation. There is **no `LLOEntity` in this
flow** (the model is being deprecated; CCCT-2494 is org-centric).
*Benefit:* one identity model shared with the rest of Connect; a real `Organization` always
exists by submit time, so there's nothing to create lazily at award.

> **Award gates on verification.** A probationary (unverified) org may *apply* and be *reviewed*,
> but an **award is blocked until the org is verified** — the award commits budget and flips
> `ProgramApplication → accepted` (see §3.5).
>
> **Dependency on CCCT-2494.** The returning-user path (apply as an existing org) works without
> CCCT-2494; the **brand-new external applicant path depends on CCCT-2494's signup + probationary
> org creation** — the gap CCCT-2494 is built to close. E3-T1 is tagged accordingly.

**Decision 3 — Each question can link to a criterion; scoring rolls up by weight.**
*Problem it solves:* reviewers need to know which criterion an answer informs, and the PM
sets relative importance. *Decision:* a `SolicitationQuestion` links to at most one
`EvaluationCriterion` (and one criterion can cover several questions). Reviewers score each criterion on a **fixed
1–10** scale (not configurable per criterion); each criterion's **weight is a percentage**, and a solicitation's weights are
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


### 3.4 Data model

All new models extend the project's `BaseModel` (which already provides `created_by`,
`modified_by`, `date_created`, `date_modified`) and follow the existing UUID + integer-PK
convention. The sketches below show structure and intent; exact field types, lengths,
`db_index`, and `on_delete` choices are finalized in the technical spec.

**Reused as-is (no schema change):** `Organization` (the applicant identity — an existing
membership or a new org created during CCCT-2494 signup; carried for the award handoff),
`User`, `Program`,
`Currency`, `Country`, `DeliveryType`, `ProgramApplication` (set to *accepted* on award). The
solicitation module does **not** reference `ManagedOpportunity` — the existing flow builds and owns
the opportunity downstream of the `ProgramApplication` handoff.

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
        CANCELLED = "cancelled", _("Cancelled")

    solicitation_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=10, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    public = models.BooleanField(default=False)

    description = models.TextField()  # the scope of work; UI may label this "Scope of work" or "Description"

    budget_min = models.PositiveBigIntegerField(null=True, blank=True)  # informational lower bound shown to applicants
    budget_max = models.PositiveBigIntegerField(null=True, blank=True)  # ceiling: cumulative Award.award_amount must not exceed it (Decision 1)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)  # required; awards inherit this currency
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
    closure_reason = models.TextField(blank=True)  # reason given when a PM closes early or cancels


class EvaluationCriterion(BaseModel):
    """Internal-only scored dimension. Never shown to applicants or on public pages."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="criteria")
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)  # reviewer guidance ("what good looks like")
    weight = models.DecimalField(max_digits=5, decimal_places=2)  # percentage; weights total 100% per solicitation
    display_order = models.PositiveSmallIntegerField(default=0)
    # Scoring scale is a fixed 1–10; see CriterionScore.score.


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
    # Internal link only: a question maps to at most one criterion;
    # a criterion may cover many questions.
    criterion = models.ForeignKey(
        EvaluationCriterion, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions"
    )


class SolicitationAttachment(BaseModel):
    """Supporting documents (scope PDFs, reference materials) for applicants."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="solicitations/attachments/")
    filename = models.CharField(max_length=255)


class SolicitationInvitation(BaseModel):
    """Grants an organization access to a PRIVATE (invite-only) solicitation. Invited orgs see it
    in the marketplace's "Invited to you" section and may apply. Public solicitations need none."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="invitations")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="solicitation_invitations"
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "organization"], name="one_invitation_per_org")
        ]


class Application(BaseModel):
    """One submission from an organization in response to a solicitation."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        UNDER_REVIEW = "under_review", _("Under Review")
        AWARDED = "awarded", _("Awarded")
        REJECTED = "rejected", _("Rejected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    application_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="applications")
    # The applying Connect Organization (an existing membership, or a new probationary org
    # created during CCCT-2494 signup). This is the application's KEY — it matches the
    # Organization-keyed ProgramApplication downstream. No LLOEntity is involved (Decision 2).
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_applications")

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    submitter_name = models.CharField(max_length=255)   # snapshot, kept even if user changes
    submitter_email = models.EmailField()               # snapshot

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    # Shortlisting is orthogonal to status, not a successor state: a shortlisted application stays
    # `under_review` (reviewers keep scoring). PM curation toggles this flag; reviews are gated on
    # status == under_review, not on this flag. The UI shows "Shortlisted" when this is set.
    shortlisted = models.BooleanField(default=False, db_index=True)  # dashboard filters on this
    submitted_date = models.DateTimeField(null=True, blank=True)
    withdrawn_date = models.DateTimeField(null=True, blank=True)

    # Link into the existing onboarding flow, set on award (program-linked solicitations only).
    program_application = models.ForeignKey(ProgramApplication, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "organization"], name="one_application_per_org")
        ]


class ApplicationAnswer(BaseModel):
    """An applicant's response to a single question."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(SolicitationQuestion, on_delete=models.CASCADE)
    answer = models.JSONField(null=True, blank=True)  # text / number / choice / date
    file = models.FileField(upload_to="solicitations/answers/", null=True, blank=True)  # for FILE questions

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["application", "question"], name="one_answer_per_question")
        ]


class ReviewerAssignment(BaseModel):
    """Grants a user scoped access to review ONE solicitation."""

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

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    recommendation = models.CharField(max_length=20, choices=Recommendation.choices, blank=True)
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # computed /100
    notes = models.TextField(blank=True)
    tags = models.JSONField(null=True, blank=True)
    submitted_date = models.DateTimeField(null=True, blank=True)  # null = draft

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["application", "reviewer"], name="one_review_per_reviewer")
        ]


class CriterionScore(BaseModel):
    """Per-criterion score within a review; these roll up into Review.overall_score."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="criterion_scores")
    criterion = models.ForeignKey(EvaluationCriterion, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField()  # fixed 1–10 scale (validators MinValue(1)/MaxValue(10))
    comment = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review", "criterion"], name="one_score_per_criterion")
        ]


class Award(BaseModel):
    """A decision to award an applicant. A solicitation may have multiple awards."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="awards")
    awarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    # Set when an awarded applicant withdraws: the award is released (budget freed) and the PM may
    # re-award. Released awards are retained for audit and excluded from the budget cap (Decision 1).
    released_date = models.DateTimeField(null=True, blank=True)
    award_amount = models.PositiveBigIntegerField()  # in the Solicitation's currency
    comment = models.TextField(blank=True)
    # v1 placeholder: PM manually uploads an out-of-band signed contract PDF.
    # Also covers contracts for solicitations run off-platform. Phase 2 replaces this
    # with generated documents + e-signature (a dedicated Contract model).
    contract_file = models.FileField(upload_to="solicitations/contracts/", null=True, blank=True)
    # No link to ManagedOpportunity: the award hands off via ProgramApplication only (Part 2,
    # Step 5); the existing flow owns the opportunity and the solicitation module never references it.
    # Phase 2 reserves: contract = models.OneToOneField("Contract", ...)

    class Meta:
        constraints = [
            # At most one live award per application; released awards are retained for audit.
            models.UniqueConstraint(
                fields=["application"],
                condition=models.Q(released_date__isnull=True),
                name="one_active_award_per_application",
            )
        ]
```

**Phase 2 only (not built in v1):** a `Contract` (per award; status, signed copy,
e-signature provider fields) and a program-level versioned `ContractTemplate`.

### 3.5 Status lifecycles

- **Solicitation:** `draft` → `active` (on publish) → `closed` (deadline passed, or PM
  closes early with a reason). `cancelled` is a reason-tagged terminal action from `draft`/`active`. Drafts are freely
  editable; once `active`, structural fields (questions/criteria) lock while descriptive copy
  and deadline extensions stay editable.
  - **Deadline close is automatic.** A daily Celery-beat task flips `active → closed` for any
    solicitation whose `application_deadline` has passed (PMs can also close early by hand).
- **Application:** `draft` → `submitted` → `under_review` → `awarded` | `rejected`.
  `withdrawn` is an applicant action allowed at any point the application is still live —
  **including after an award** (see the post-award withdrawal bullet below). Submission is
  one-shot. **Shortlisting is not a status** — it's a separate `shortlisted` boolean toggled
  while the application stays `under_review` (see the shortlisting bullet); the displayed
  status an applicant sees is derived (a shortlisted `under_review` application shows as
  "Shortlisted").
  - **`submitted → under_review` is a manual reviewer action.** A reviewer moves an
    application into `under_review` when they pick it up to score (this triggers the
    applicant's "under review" email, §3.7); it is not flipped automatically.
  - **Reviews are gated on `under_review`.** A `Review`/`CriterionScore` can only be created or
    edited while the application's status is `under_review`. Once it leaves that state
    (`awarded` / `rejected` / `withdrawn`) scoring is blocked. This is the clean boundary the
    `shortlisted` boolean buys us — shortlisting no longer changes status, so it never silently
    opens or closes the review window.
  - **Shortlisting is a flag, not a transition.** A PM-only curation step, done **in bulk** from
    the per-solicitation Applications dashboard (reviewers cannot shortlist). It sets
    `shortlisted = True` while the application **stays `under_review`**, and **emails each
    affected applicant** (§3.7).
  - **Not mandatory before award.** `under_review → awarded` is allowed, so a PM can award a
    clear-cut RFP without shortlisting first; the dashboard still nudges toward the
    shortlist-then-award path.
  - **Award requires a verified org.** A probationary (unverified) applicant org can apply and be
    reviewed, but `→ awarded` is **blocked until a System Admin verifies the org** (CCCT-2494) —
    the award commits budget and flips `ProgramApplication → accepted` (Decision 2).
  - **Post-award withdrawal releases the award.** An awarded applicant may withdraw; doing so
    **releases the `Award`** (sets `released_date`, frees its budget) and moves the application to
    `withdrawn`. Any `ProgramApplication` created by that award is reverted out of `accepted`. The
    PM may then **re-award** another applicant. The solicitation drops from `awarded` back to its
    prior state (`active`/`closed`) if no active awards remain, and stays `awarded` if other awards
    do.
  - **Reversible.** Un-shortlisting (`shortlisted` back to `False`) is allowed any time before the
    application is awarded or rejected. It **emails the affected applicant** so the change is
    communicated, like every other application transition (§3.7).
  - **Does not freeze review.** Reviewers can keep scoring a shortlisted application — because the
    status is still `under_review`, the review window stays open. Shortlisting is PM curation, not
    a review lock.
  - **No completion gate.** The PM can shortlist at any time, even on partial reviews; the
    dashboard surfaces per-application review-completion progress so the call is informed.
- **Review:** `draft` (saved) → `submitted` (finalized). Other reviewers' scores stay hidden
  until submission when `hide_scores_until_submit` is on.
- **Contract:** no contract *lifecycle* in v1 — the `Award` just holds a manually-uploaded `contract_file`. Generation, status tracking, and e-signature are Phase 2.

### 3.6 Notifications & emails

- **To applicants** — on every status change, **and** on shortlist / un-shortlist (a
  `shortlisted` flag toggle, which is not itself a status change).
- **To PMs** — a weekly digest (Celery beat) of how many applications await review per open
  solicitation, plus scores for already-scored applications. Follows the existing
  `WEEKLY_PERFORMANCE_REPORT` flag pattern. Plus a PM-triggered "email all applicants"
  broadcast for solicitation updates.
- **To invited orgs** — when invited to a **private** solicitation (a `SolicitationInvitation`
  is created), an email with a link to it; for **public** solicitations the PM can still broadcast
  a link to selected orgs.

