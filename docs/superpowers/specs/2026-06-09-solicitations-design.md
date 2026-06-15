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
have no program yet — that's allowed). They mark it **public** (shows on the open
marketplace) or **private** (only invited orgs), assign one or more reviewers, and
**publish**. They can also email selected existing orgs a link to the public page.

> **Note:** the exact **public vs. private** access semantics (likely "private" = *unlisted* —
> not on the marketplace but reachable by direct link — for v1) are still being worked out and
> will be specced shortly. The narrative above and the data model don't yet fully agree on how
> private solicitations are gated.


### Step 2 — An organization discovers it and applies

A **public visitor** browses the marketplace, filtering by type, country, delivery type, or
deadline, and opens a solicitation to read its scope, budget range, and deadline. The
**questions and the internal criteria are not shown** at this stage — questions appear only
after they sign in to apply, and criteria are never shown to applicants at all (they're an
internal scoring tool).

When they click **Apply**, they go through the **standard Connect sign-up/login flow** (the
shared signup owned by CCCT-2494) and then choose which **organization** they're applying *as*:
a returning user **picks one of their organizations**; a brand-new user **creates a new
organization** as part of signup (it starts *probationary*, pending Dimagi verification, but
that doesn't block them from applying). The application is keyed on that **organization**. They
answer the questions and either save a draft or submit.
After submitting they can track status (*submitted → under review → shortlisted →
awarded/rejected*) and can withdraw before the deadline. They're emailed on every status
change.

### Step 3 — Reviewers score the applications

Each assigned **reviewer** sees only the solicitations they were added to. They open an
application, score it against each evaluation criterion (with the PM's guidance shown
beside each), add notes/tags, suggest an award amount, and set an overall recommendation
(*approve / reject / needs revision*). By default a reviewer can't see other reviewers'
scores until they've submitted their own, to reduce anchoring bias.

### Step 4 — The PM shortlists and awards

The PM sees a dashboard of all applications with their status, the reviewer-averaged score,
and recommendations. They can **shortlist** a subset in bulk (which emails those applicants),
refine that set freely — shortlisting is reversible and reviewers keep scoring throughout —
and then **award** one or more applicants; for a clear-cut RFP they can also award straight
from review without shortlisting. RFPs usually produce one award, EOIs often several. Awarding records the award amount per winner. The PM can **bulk-reject** the
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
| **Review screens** | org-scoped `/a/<org_slug>/solicitations/reviews/` (list), `/a/<org_slug>/solicitations/<id>/review/` (scoring) | Org-scoped like the rest of Connect: lists only the current org's solicitations the user is assigned to. A `ReviewerAssignment` for the solicitation is still the per-screen gate (plain org membership isn't enough). A reviewer assigned in several orgs switches org context to see each. |

The whole module sits behind a global **feature switch** named `solicitations`, following
the existing `flags/switch_names.py` pattern (Release Path 3).

### 3.2 Pages to build and their audiences

The screens below, grouped by the four URL surfaces above. "Audience" is who the page is
*for*; "Reached via" is how a user navigates to it; auth requirements follow the surface it
belongs to. Each maps back to a step in Part 2.


**Public marketplace** (unauthenticated; shows only `public` + `active` solicitations)

| Page | Audience | Reached via | Purpose |
|---|---|---|---|
| Marketplace list | Public visitor / prospective applicant | New **"Explore opportunities"** item in the public site nav + home CTA | Browse + filter (type, country, delivery type, deadline) published solicitations as scannable cards. *(Part 2, Step 2)* |
| Solicitation detail (public) | Public visitor / prospective applicant | Marketplace cards → detail | Read scope, budget range, deadline. **No questions, no criteria.** "Apply" CTA → sign-up/login. |

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
| Applications dashboard (per solicitation) | Program Manager | From the Solicitations dashboard | All applications with status, reviewer-averaged score, recommendation; shortlist + bulk-reject actions. *(Part 2, Step 4)* |
| Application review detail (PM view) | Program Manager | From the Applications dashboard | Read one application + all reviewers' scores/notes (PM sees everything). |
| Award screen | Program Manager | From the Applications dashboard | Award one or more applicants — drawn from the shortlist, or directly from under-review for a clear-cut RFP: amount/currency (budget check), confirm → downstream onboarding. *(Part 2, Steps 4–5)* |
| Reviewer management | Program Manager | From the Solicitations dashboard (also on the create/edit form) | Add/remove reviewers (and observers) on a solicitation. |
| Close / cancel dialog | Program Manager | From the Solicitations dashboard | Close early or cancel with a reason. |

**Review screens** (org-scoped under `/a/<org_slug>/`; gated by `ReviewerAssignment`)

| Page | Audience | Reached via | Purpose |
|---|---|---|---|
| My assigned solicitations | Reviewer / Observer | New **"My reviews"** entry in the org sidebar | Lists the **current org's** solicitations the user is assigned to. A reviewer assigned in several orgs switches org context to see each. *(Part 2, Step 3)* |
| Application scoring | Reviewer | From "My assigned solicitations" | Score each criterion (with guidance), notes/tags, suggested reward, recommendation; submit. Other reviewers' scores hidden until own submitted. Observers get read-only. |

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
award amounts on a solicitation must fit within its `budget_max`** — an award that would push the
cumulative awarded total over `budget_max` is **rejected (hard block)**. (Reviewers' suggested
amounts — `Review.reward_budget` — are advisory and are *not* validated against the budget; the
cap is enforced only on the actual `Award.award_amount` totals at award time.) When a Program *is*
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
exists by submit time, so there's nothing to create lazily at award; duplicate orgs are expected
and resolved later by System-Admin **merge** (so the apply flow needs no name-collision
handling); and the award step simply creates/updates the `ProgramApplication` for that org.

> **Award gates on verification.** A probationary (unverified) org may *apply* and be *reviewed*,
> but an **award is blocked until the org is verified** — the award commits budget and flips
> `ProgramApplication → accepted` (see §3.5).
>
> **Dependency on CCCT-2494.** The returning-user path (apply as an existing org) works without
> CCCT-2494; the **brand-new external applicant path depends on CCCT-2494's signup + probationary
> org creation** — the gap CCCT-2494 is built to close. E3-T1 is tagged accordingly.

**Decision 3 — Reuse the existing `program_manager` capability to decide who can post.**
*Problem it solves:* who is a "Funder" allowed to post solicitations? *Decision:* for v1,
reuse the existing rule — admins of an organization flagged `program_manager=True`, via the
existing `@org_program_manager_required` decorator. No new permission concept. Letting
external funders post (without managing programs) is future scope.

> **Forthcoming "Funder org" designation (external dependency — CCCT-2494).** The separate
> *Restructuring Programs, Opportunities and Organizations* work introduces a dedicated
> **Funder** organization designation — a System-Admin-applied tag, analogous to the **Program
> Org** designation, where a Funder may be the parent of one or more Programs. **That work, not
> this one, implements it.** When it lands, "who may post a solicitation" should migrate from
> the `program_manager` flag to the Funder / Program-Org designation. Until then, v1 keeps the
> `program_manager` rule above. This *posting-gate* alignment is forward-looking only (no
> dependency) — but note the **apply flow's new-external-applicant path does depend on CCCT-2494**
> (see Decision 2). (That work also reworks org roles — Program / Advising / Supervising /
> Delivering Org — which solicitations terminology ("PM") will eventually align with.)

**Decision 4 — Reviewer visibility is scoped per-solicitation via a ReviewerAssignment record.**
*Problem it solves:* the requirement is "a reviewer sees only the solicitations they've been
added to," but Connect's access model is all-or-nothing per organization — it can't express
"these two solicitations but not the others this org runs." *Decision:* a
`ReviewerAssignment(user, solicitation, role)` record *is* the authorization; review screens
check it rather than relying on org membership alone. Reviewers must still be members of the
funding org that posted the solicitation (cross-org reviewers — assigning someone outside the
posting org — are out of scope for v1). The review surface is **org-scoped**, like every other
authenticated page on Connect: it lives under `/a/<org_slug>/solicitations/reviews/`, and a
user's "My assigned solicitations" list shows only the **current org's** solicitations they're
assigned to. A reviewer who belongs to several funding orgs and is assigned in more than one
uses the standard org switcher to move between them. The per-screen gate is still the
`ReviewerAssignment` for that solicitation (plain org membership isn't enough — not every org
member reviews every solicitation), additionally scoped to the active org. `role` covers
reviewer (can score) vs observer (read-only).

**Decision 5 — Evaluation criteria are strictly internal.**
*Problem it solves:* the prototype showed criteria to applicants, but the brief says they're
an internal scoring tool. *Decision:* criteria are visible only to PMs and assigned
reviewers — never on public pages or the apply form.

**Decision 6 — Each question can link to a criterion; scoring rolls up by weight.**
*Problem it solves:* reviewers need to know which criterion an answer informs, and the PM
sets relative importance. *Decision:* a `SolicitationQuestion` links to at most one
`EvaluationCriterion` (and one criterion can cover several questions — matching the
prototype's shared "Technical Expertise (25%)"). Reviewers score each criterion on a **fixed
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

**Decision 7 — No AI features in v1; contracts limited to a manual upload placeholder.**
The prototype teased "Generate criteria with AI" and "AI Comparative Review"; the brief
lists AI as future scope, so they're excluded. Contract *automation* (generate-on-award,
e-signature, counter-signature) is also out of v1 — but the `Award` carries a single
`contract_file` field so a PM can **manually upload an out-of-band signed PDF** (the brief's
v1 placeholder). Because it's just a file on the award, it also serves solicitations that
were run off-platform. The fuller contract model (a dedicated `Contract` record +
program-level `ContractTemplate`) is reserved for Phase 2.

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
        AWARDED = "awarded", _("Awarded")
        CANCELLED = "cancelled", _("Cancelled")

    solicitation_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    title = models.CharField(max_length=255)
    type = models.CharField(max_length=10, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    public = models.BooleanField(default=False)

    description = models.TextField()  # the scope of work; UI may label this "Scope of work" or "Description"

    budget_min = models.PositiveBigIntegerField(null=True, blank=True)  # informational lower bound shown to applicants
    budget_max = models.PositiveBigIntegerField(null=True, blank=True)  # ceiling: cumulative Award.award_amount must not exceed it (Decision 1)
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


class Application(BaseModel):
    """One submission from an organization in response to a solicitation."""

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
    # The applying Connect Organization (an existing membership, or a new probationary org
    # created during CCCT-2494 signup). This is the application's KEY — it matches the
    # Organization-keyed ProgramApplication downstream. No LLOEntity is involved (Decision 2).
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
            models.UniqueConstraint(fields=["solicitation", "organization"], name="one_application_per_org")
        ]


class ApplicationAnswer(BaseModel):
    """An applicant's response to a single question."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(SolicitationQuestion, on_delete=models.CASCADE)
    answer = models.JSONField(null=True, blank=True)  # text / number / choice / date
    file = models.FileField(upload_to="solicitations/answers/", null=True, blank=True)  # for FILE questions


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
    score = models.PositiveSmallIntegerField()  # fixed 1–10 scale (validators MinValue(1)/MaxValue(10))
    comment = models.TextField(blank=True)


class Award(BaseModel):
    """A decision to award an applicant. A solicitation may have multiple awards."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="awards")
    application = models.ForeignKey(Application, on_delete=models.CASCADE)
    awarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    awarded_date = models.DateTimeField(auto_now_add=True)
    award_amount = models.PositiveBigIntegerField()
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, null=True)
    comment = models.TextField(blank=True)
    # v1 placeholder: PM manually uploads an out-of-band signed contract PDF.
    # Also covers contracts for solicitations run off-platform. Phase 2 replaces this
    # with generated documents + e-signature (a dedicated Contract model).
    contract_file = models.FileField(upload_to="solicitations/contracts/", null=True, blank=True)
    # No link to ManagedOpportunity: the award hands off via ProgramApplication only (Part 2,
    # Step 5); the existing flow owns the opportunity and the solicitation module never references it.
    # Phase 2 reserves: contract = models.OneToOneField("Contract", ...)
```

**Phase 2 only (not built in v1):** a `Contract` (per award; status, signed copy,
e-signature provider fields) and a program-level versioned `ContractTemplate`.

### 3.5 Status lifecycles

- **Solicitation:** `draft` → `active` (on publish) → `closed` (deadline passed, or PM
  closes early with a reason). `awarded` is set once ≥1 award is made, and can be reached from
  either `active` (a PM may award before the deadline — Part 2, Step 4) or `closed`.
  `cancelled` is a reason-tagged terminal action from `draft`/`active`. Drafts are freely
  editable; once `active`, structural fields (questions/criteria) lock while descriptive copy
  and deadline extensions stay editable.
  - **Deadline close is automatic.** A daily Celery-beat task flips `active → closed` for any
    solicitation whose `application_deadline` has passed (PMs can also close early by hand).
- **Application:** `draft` → `submitted` → `under_review` → `shortlisted` →
  `awarded` | `rejected`. The PM may also award or reject directly from `under_review`
  (shortlisting is the normal path, not a gate). `withdrawn` is an applicant action allowed
  only before the window closes. Submission is one-shot.
  - **`submitted → under_review` is a manual reviewer action.** A reviewer moves an
    application into `under_review` when they pick it up to score (this triggers the
    applicant's "under review" email, §3.7); it is not flipped automatically.
  - **Shortlisting** is a PM-only curation step, done **in bulk** from the per-solicitation
    Applications dashboard (reviewers cannot shortlist). It moves the selected applications
    `under_review → shortlisted` and **emails each affected applicant** (§3.7).
  - **Not mandatory before award.** `under_review → awarded` is allowed, so a PM can award a
    clear-cut RFP without shortlisting first; the dashboard still nudges toward the
    shortlist-then-award path.
  - **Award requires a verified org.** A probationary (unverified) applicant org can apply and be
    reviewed, but `→ awarded` is **blocked until a System Admin verifies the org** (CCCT-2494) —
    the award commits budget and flips `ProgramApplication → accepted` (Decision 2).
  - **Reversible.** `shortlisted → under_review` (un-shortlist) is allowed any time before the
    application is awarded or rejected. Un-shortlisting **emails the affected applicant** so the
    status change is communicated, like every other application transition (§3.7).
  - **Does not freeze review.** Reviewers can keep scoring a shortlisted application —
    shortlisting is PM curation, not a review lock.
  - **No completion gate.** The PM can shortlist at any time, even on partial reviews; the
    dashboard surfaces per-application review-completion progress so the call is informed.
- **Review:** `draft` (saved) → `submitted` (finalized). Other reviewers' scores stay hidden
  until submission when `hide_scores_until_submit` is on.
- **Contract:** no contract *lifecycle* in v1 — the `Award` just holds a manually-uploaded `contract_file`. Generation, status tracking, and e-signature are Phase 2.

### 3.6 Permissions & access (summary)

| Surface | Gate |
|---|---|
| Public marketplace | none — unauthenticated; only `public` + `active` solicitations |
| Apply flow | authenticated (login/signup via CCCT-2494); applicant applies as one of their orgs, or a new probationary org |
| PM workspace | `@org_program_manager_required` |
| Review screens | org-scoped under `/a/<org_slug>/`; current-org membership **plus** a `ReviewerAssignment` for that solicitation (the reviewer is a member of the posting org) |
| Whole module | global feature switch `solicitations` (Release Path 3) |

### 3.7 Notifications & emails

All sent asynchronously via the existing `send_mail_async.delay(...)` task
(`utils/tasks.py`), following the pattern in `organization/tasks.py`, with absolute-URI
links into the relevant screen.

- **To applicants** — on every status change: submission confirmation, under-review,
  shortlisted, un-shortlisted (back to under-review), awarded, rejected (templated, including
  bulk rejection).
- **To PMs** — a weekly digest (Celery beat) of how many applications await review per open
  solicitation, plus scores for already-scored applications. Follows the existing
  `WEEKLY_PERFORMANCE_REPORT` flag pattern. Plus a PM-triggered "email all applicants"
  broadcast for solicitation updates.
- **To invited orgs** — a PM-sent link to a new solicitation's public page.

### 3.8 Phase 2 (reserved, not built now)

Contract management, building on v1's manual `contract_file` upload: a program-level
versioned `ContractTemplate`, generate-on-award draft documents, e-signature provider
integration with status-callback webhooks, and two-party counter-signature. Designed to also
support contracts for solicitations that happened off-platform. (Google Docs signing is one
option to explore.) Other future scope from the brief: AI criteria generation and AI
application/review helpers, reviewer blinding, opt-in notifications for new solicitations,
external-funder posting (aligns with the forthcoming Funder org designation, CCCT-2494 — see
Decision 3), per-RFP FAQs, and a funder analytics dashboard.

---

## Assumptions

This section reconciles this design doc against the source brief ("*Solicitations on
Connect*", Mary Rocheleau / Claude). It has two parts: **(A)** decisions this doc made where
the brief was silent or ambiguous, and **(B)** points where the two documents actively
disagree (including places the brief contradicts *itself* and this doc had to pick a side).
Items already flagged inline above are noted as such.

### A. Assumptions made (brief silent or ambiguous → this doc decided)

- **Who may post = `program_manager` org admins.** The brief says "any permissioned user"
  with a generic "Funder" role "assigned to any Connect user (Dimagi or external) in a
  funding organization." This doc (Decision 3) assumes that means **admins of an org flagged
  `program_manager=True`** via the existing `@org_program_manager_required` decorator — no new
  permission concept, and the "Funder" persona is collapsed into "PM." A dedicated **Funder org**
  designation is being introduced separately (CCCT-2494); posting eligibility should align with it
  once it lands — see the note under Decision 3.

- **Applicant identity is an `Organization`; signup is delegated to CCCT-2494.** The brief lists
  the applicant identity as "LLO entity, Organization, User" but never says where the
  `Organization` comes from. This doc (Decision 2) drops the `LLOEntity` and resolves identity to
  an `Organization` — an existing membership, or a new probationary org created via CCCT-2494's
  signup flow — so a real org always exists by submit time and the award handoff needs nothing
  created lazily. *Affiliation* means org membership (the orgs a user can apply as are the ones
  they belong to).

- **`Application` is keyed on `Organization`, one per organization per solicitation.** The brief
  implies one submission per applicant but doesn't define the key. This doc keys on the resolved
  `Organization` (`unique(solicitation, organization)`), matching the Organization-keyed
  `ProgramApplication` downstream (see Decision 2). `LLOEntity` is not used.

- **Scoring scale is a fixed 1–10; weights are percentages totalling 100%; overall score is
  normalized to /100.** The brief says only "evaluation criteria with weights." This doc
  (Decision 6) assumes a non-configurable 1–10 per-criterion scale, weights validated to sum to
  100% on publish, and a weighted-average overall score scaled to 100.

- **Reviewers must be members of the posting org; no cross-org reviewers in v1.** The brief's
  Reviewer persona allows "any Connect user (Dimagi or external)." This doc (Decision 4)
  assumes reviewers are members of the funding org and defers external/cross-org reviewers.

- **Award stops at `ProgramApplication = accepted`; the opportunity is not auto-built.** The
  brief says awarded LLOs flow in "via ProgramApplication **and/or ManagedOpportunity**." This
  doc assumes the marketplace deliberately stops at flipping `ProgramApplication` to *accepted*
  (Step 5) and lets the existing flow build the `ManagedOpportunity`.

- **Program-less solicitations: award is just recorded.** The brief always speaks of "updating
  the ProgramApplication" on award. This doc assumes that for a standalone (no-Program) EOI
  there is no `ProgramApplication` to touch and the `Award` is simply recorded.

- **Automatic deadline close.** The brief only mentions PMs closing early. This doc assumes a
  daily Celery-beat task auto-flips `active → closed` when `application_deadline` passes.

- **`submitted → under_review` is a manual reviewer action** (triggers the applicant email),
  and **structural fields lock once `active`** (questions/criteria freeze; descriptive copy and
  deadline extensions stay editable). The brief specifies neither.

- **The personal lists are org-scoped, in the left sidebar.** "My applications" and "My reviews"
  show only the current org's items and live in the org sidebar, matching every other
  authenticated Connect page; a user with several orgs switches org context to see each. The
  brief doesn't address placement. *(Earlier drafts made these non-org-scoped in the header
  dropdown; scoped to orgs per review feedback.)*

- **Added Solicitation fields not named in the brief:** `contact_email`, `estimated_scale`,
  `expected_start_date` / `expected_end_date`, and `hide_scores_until_submit` as a stored
  toggle. The brief implies score-visibility is "configurable" but doesn't name the rest.

- **Naming/placement specifics:** the Django app is `solicitation`, the feature switch is
  `solicitations`, and the public nav label is provisionally **"Explore opportunities"**
  (flagged TBC with Product in §3.2). The brief specifies none of these.

### B. Where the two documents disagree

- **Evaluation-criteria visibility.** The brief's PM story says criteria "are available to be
  viewed along with the solicitation," implying applicant/public visibility. **This doc makes
  criteria strictly internal** (Decision 5) — never on public pages or the apply form. *Direct
  conflict; this doc sides with the brief's "internal scoring tool" framing over its
  "viewable" wording.*

- **Is a solicitation always scoped to a Program?** The brief contradicts itself: the Goals say
  "EOI/RFP can be created with no Program affiliation," but the *Reuse vs. new entities* table
  says "Each solicitation **is** scoped to a Program." **This doc makes `program` optional**
  (`null=True`), siding with the Goals.

- **Private-solicitation access model.** The brief defines private as "only visible to invited
  orgs" — an access-control mechanism. The data model here has only a `public` boolean and **no
  invited-org gating**; this doc leans toward private = *unlisted* (reachable by direct link).
  *Unresolved — already flagged in §Part 2 Step 1 and noted as needing its own spec.*

- **"Invite users/orgs to apply" as a formal mechanism.** The brief lists PM stories to "invite
  one or more users and/or organizations to apply." This doc reduces invitation to **emailing a
  link to the public page** (§3.7) — there is no invitation/access-grant record. Coupled to the
  private-access gap above.

- **Download-a-template / upload-a-markdown-draft collaboration flow.** The brief's PM story
  ("download a template and upload a draft … suggested file type markdown") describes authoring
  the RFP/EOI *language* off-product. **This doc does not spec it** — `SolicitationAttachment`
  covers supporting docs *for applicants*, not round-tripping the solicitation's own copy.
