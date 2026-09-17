# Solicitations Module — Technical Spec

## Abstract

Program Managers can only bring organizations they already know into a program, and the Delivery team finds new
partners through Google Forms and a hand-maintained spreadsheet that live outside Connect. This spec proposes a
Solicitations module: a public listing on Connect where Program Managers post requests for proposals and expressions
of interest, organizations apply, colleagues score the applications against weighted criteria, and the Program
Manager awards the work. When a solicitation is linked to a program, an award marks the winning organization as
accepted into that program, which hands it to the existing onboarding flow instead of building a second one.

## Problem Statement

A Program Manager (PM) runs a program: a funded body of work with a budget, currency and country. The work itself is
carried out by partner organizations, set up as managed opportunities. Today the only way to add a partner
organization to a program is the invite-only `ProgramApplication` flow: the PM must already know the organization,
invite it, and move it through *invited → applied → accepted*.

This leaves two problems:

1. **Organizations cannot find work.** There is no public place where an organization can see upcoming work and put
   itself forward, so programs only reach partners the PM already has contact with.
2. **Partner selection happens off-platform.** The Delivery team runs RFPs (Requests for Proposals — a call for bids on
   defined work) and EOIs (Expressions of Interest — a lighter call to gauge who is interested and able) through Google
   Forms and a manual spreadsheet. The applications, scores and decisions are unstructured, hard to audit, and invisible
   to the rest of Connect.

## Proposed solution

### Overview

A new Django app, `commcare_connect/solicitation/`, gated by a global waffle switch named `solicitations`.

A **solicitation** is a posting: an RFP or EOI with a scope of work, budget range, country, delivery type and an
application deadline date. The PM who creates it also defines the **questions** applicants must answer and the
**evaluation criteria** reviewers score against (each with a percentage weight). A solicitation is either **public**
(listed for everyone, including logged-out visitors) or **private** (visible only to organizations the PM invites). It
can optionally be linked to a program; exploratory EOIs often have none.

An applicant always applies on behalf of an **organization**. A returning user picks one of the organizations they
belong to. A new user signs up through the standard Connect signup and creates an organization as part of it. That new
organization is *probationary* — it can apply straight away, but a Dimagi system admin must verify it before it can
win an award. This signup and probationary-organization flow is being built separately in CCCT-2494; returning users
don't depend on it.

**Reviewers** are members of the PM's organization assigned to a specific solicitation. They score each application on
each criterion, and the scores roll up into a weighted score out of 100. The PM shortlists promising applications,
awards one or more of them, and rejects the rest.

The module has four URL areas, one per audience:

| Area | URL | Access |
|---|---|---|
| Public listing and detail | `/solicitations/`, `/solicitations/<id>/` | Anyone. Logged-in users also see private solicitations their organizations are invited to. Questions and criteria are never shown here. |
| Apply | `/solicitations/<id>/apply/` | Logged-in users applying as one of their organizations. |
| PM workspace | `/a/<org_slug>/solicitations/…` | PMs of the posting organization. Create, publish, shortlist, award, reject, close. |
| Review | `/a/<org_slug>/solicitations/reviews/`, `/a/<org_slug>/solicitations/<id>/review/` | Users with an `ApplicationReviewer` assignment on the solicitation. |

Applicants are emailed on every application status change and when they are shortlisted or un-shortlisted. Invited
organizations are emailed when invited to a private solicitation. PMs get a weekly digest of applications waiting for
review.

### Data model

All new models extend `BaseModel`. Existing models used without schema changes: `Organization`, `User`, `Program`,
`Currency`, `Country`, `DeliveryType`, `ProgramApplication`, `Opportunity`.

```python
class Solicitation(BaseModel):
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
    description = models.TextField()  # scope of work

    budget_min = models.PositiveBigIntegerField(null=True, blank=True)  # shown to applicants; must be <= budget_max
    budget_max = models.PositiveBigIntegerField()  # cap on the total of active award amounts
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)  # forced to program.currency when linked
    country = models.ForeignKey(Country, on_delete=models.PROTECT, null=True)
    delivery_type = models.ForeignKey(DeliveryType, on_delete=models.PROTECT, null=True, blank=True)

    expected_start_date = models.DateField(null=True, blank=True)
    expected_end_date = models.DateField(null=True, blank=True)
    application_deadline = models.DateField()  # last day applications can be submitted
    estimated_scale = models.CharField(max_length=255, blank=True)  # e.g. "5,000 households, 3 districts"
    contact_email = models.EmailField()

    program = models.ForeignKey(Program, on_delete=models.PROTECT, null=True, blank=True)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)  # the posting organization

    hide_scores_until_submit = models.BooleanField(default=True)
    closure_reason = models.TextField(blank=True)


class EvaluationCriterion(BaseModel):
    """Internal only. Never shown to applicants."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="criteria")
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)  # guidance shown to reviewers
    weight = models.DecimalField(max_digits=5, decimal_places=2)  # percentage; a solicitation's weights total 100
    display_order = models.PositiveSmallIntegerField(default=0)


class SolicitationQuestion(BaseModel):
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
    choice_options = models.JSONField(null=True, blank=True)
    required = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    # Tells reviewers which criterion this answer informs. One criterion can cover many questions.
    criterion = models.ForeignKey(
        EvaluationCriterion, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions"
    )


class SolicitationAttachment(BaseModel):
    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="solicitations/attachments/")
    filename = models.CharField(max_length=255)


class SolicitationInvitation(BaseModel):
    """Gives an organization access to a private solicitation."""

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="invitations")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_invitations")
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "organization"], name="one_invitation_per_org")
        ]


class Application(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        UNDER_REVIEW = "under_review", _("Under Review")
        AWARDED = "awarded", _("Awarded")
        REJECTED = "rejected", _("Rejected")
        WITHDRAWN = "withdrawn", _("Withdrawn")

    application_id = models.UUIDField(editable=False, default=uuid4, unique=True)
    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="applications")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="solicitation_applications")

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    submitter_name = models.CharField(max_length=255)  # copied at submit time
    submitter_email = models.EmailField()  # copied at submit time

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    shortlisted = models.BooleanField(default=False, db_index=True)  # separate from status; see status rules
    submitted_date = models.DateTimeField(null=True, blank=True)
    withdrawn_date = models.DateTimeField(null=True, blank=True)

    # Set on award when the solicitation is linked to a program.
    program_application = models.ForeignKey(ProgramApplication, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "organization"], name="one_application_per_org")
        ]


class ApplicationAnswer(BaseModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(SolicitationQuestion, on_delete=models.CASCADE)
    answer = models.JSONField(null=True, blank=True)  # text, number, choice or date
    file = models.FileField(upload_to="solicitations/answers/", null=True, blank=True)  # FILE questions

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["application", "question"], name="one_answer_per_question")
        ]


class ApplicationReviewer(BaseModel):
    """Gives a member of the posting organization access to review one solicitation."""

    class Role(models.TextChoices):
        REVIEWER = "reviewer", _("Reviewer")  # can score
        OBSERVER = "observer", _("Observer")  # read-only

    solicitation = models.ForeignKey(Solicitation, on_delete=models.CASCADE, related_name="reviewer_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.REVIEWER)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["solicitation", "user"], name="one_assignment_per_user")
        ]


class Review(BaseModel):
    class Recommendation(models.TextChoices):
        APPROVE = "approve", _("Approve")
        REJECT = "reject", _("Reject")
        NEEDS_REVISION = "needs_revision", _("Needs Revision")

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    recommendation = models.CharField(max_length=20, choices=Recommendation.choices, blank=True)
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # out of 100
    notes = models.TextField(blank=True)
    tags = models.JSONField(null=True, blank=True)
    submitted_date = models.DateTimeField(null=True, blank=True)  # null while a draft

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["application", "reviewer"], name="one_review_per_reviewer")
        ]


class CriterionScore(BaseModel):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="criterion_scores")
    criterion = models.ForeignKey(EvaluationCriterion, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])
    comment = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["review", "criterion"], name="one_score_per_criterion")
        ]


class Award(BaseModel):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="awards")
    awarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    award_amount = models.PositiveBigIntegerField()  # in the solicitation's currency
    comment = models.TextField(blank=True)
    contract_file = models.FileField(upload_to="solicitations/contracts/", null=True, blank=True)  # signed off-platform

    # Set when the awarded organization withdraws. A released award no longer counts toward any budget.
    released_date = models.DateTimeField(null=True, blank=True)
    # The ProgramApplication status before this award changed it, so a withdrawal can undo only what the award did.
    # Null when the award created the ProgramApplication.
    previous_program_application_status = models.CharField(
        max_length=20, choices=ProgramApplicationStatus.choices, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["application"],
                condition=models.Q(released_date__isnull=True),
                name="one_active_award_per_application",
            )
        ]
```

#### Solicitation rules

- **Status:** `draft` → `active` (publish) → `closed`. `cancelled` can be reached from `draft` or `active`, with a
  reason. Awards never change a solicitation's status.
- **Publishing** requires the criteria weights to total 100. Once `active`, questions and criteria are locked; the
  description and deadline can still be edited.
- **Deadline:** applicants can submit up to and including `application_deadline`. A daily Celery beat task closes
  `active` solicitations whose deadline is before today. The PM can also close early, with a reason.
- **Currency:** when a program is linked, `currency` is set to the program's currency and can't be edited.

#### Application rules

- **Status:** `draft` → `submitted` → `under_review` → `awarded` or `rejected`.
  - `submitted → under_review` is done by a reviewer when they start scoring.
  - `submitted → rejected` is also allowed, so the PM can reject applications nobody picked up. Bulk-reject covers
    both `submitted` and `under_review`.
  - Reviews can only be created or edited while the application is `under_review`.
- **Shortlisting** is the `shortlisted` flag, not a status. Only the PM sets it, in bulk, while the application is
  `under_review`. It can be undone until the application is awarded or rejected. Reviewers keep scoring shortlisted
  applications, and the applicant sees the status "Shortlisted". Shortlisting is not required before awarding.
- **Awarding** requires the applicant organization to be verified (not probationary) and the amount to fit both budget
  caps (below). If the solicitation is linked to a program, the award creates or updates the organization's
  `ProgramApplication` to `accepted` and stores its previous status on the `Award`.
- **Withdrawing** is allowed at any point while the application is not rejected or already withdrawn, with no deadline
  cutoff. After an award, it releases the award (`released_date` set) and undoes the program change:

  | `ProgramApplication` before the award | On withdrawal |
  |---|---|
  | None (the award created it) | Set to `declined` |
  | `accepted` | Left as `accepted` |
  | Any other status | Restored to that status |

  Withdrawal is **blocked** once the organization has a managed opportunity in the linked program
  (`Opportunity.objects.filter(managed=True, program=..., organization=...)`), because the organization is already
  delivering work.

#### Budget rules

An award must pass both checks:

1. **Solicitation cap:** the total of active (not released) award amounts on the solicitation, including the new one,
   must not exceed `budget_max`.
2. **Program cap** (only when a program is linked): the award must fit within the program's remaining budget:

   ```
   remaining = program.budget
             − sum of total_budget for the program's opportunities
             − sum of active award amounts in the program whose organization has no managed opportunity in the program yet
   ```

   Once an organization has an opportunity, its budget counts instead of its award, so the same money is not counted
   twice. The existing opportunity-creation check (`program/api/serializers.py:172` and the managed opportunity form)
   moves to this same shared calculation, so creating an opportunity can't spend money already awarded to another
   organization.

#### Review rules

- Reviewers and observers must be members of the solicitation's organization; this is validated at assignment.
- Each criterion is scored from 1 to 10. A review's `overall_score` is the sum of `score / 10 × weight` across criteria,
  giving a score out of 100. The PM dashboard shows the average `overall_score` of submitted reviews.
- When `hide_scores_until_submit` is on, a reviewer can't see other reviewers' scores until they submit their own. This
  stops early scores from influencing later ones.

### Why this solves the problem

The public listing gives organizations a place to find work, so programs are no longer limited to partners the PM
already knows. Questions, weighted criteria and per-reviewer scores replace the Google Forms and spreadsheet process
with structured data that lives in Connect and can be audited. The module stops at the award and hands off through the
existing `ProgramApplication` status, so onboarding and opportunity setup keep working exactly as they do today.
Applicants resolve to a real `Organization` by the time they submit, so no identity or organization records need to be
created later. Checking awards against both the solicitation and program budgets, with one shared calculation, means
the new route into a program can't overspend it.

### Examples

**Score roll-up.** Four criteria, each scored out of 10:

| Criterion | Weight | Score | Contribution |
|---|---|---|---|
| Technical Expertise | 25% | 8 | 20 |
| Past Performance | 25% | 6 | 15 |
| Cost | 20% | 9 | 18 |
| Local Presence | 30% | 7 | 21 |
| **Overall** | **100%** | | **74 / 100** |

**Budget across an award and its opportunity.** A program has a budget of 100,000 and one existing opportunity of
30,000. The PM awards Org A 40,000: remaining is 100,000 − 30,000 = 70,000, so it passes. The PM then tries to award Org
B 35,000: remaining is 100,000 − 30,000 − 40,000 = 30,000, so it's blocked. The PM creates Org A's opportunity with a
budget of 40,000: Org A's award stops counting and the opportunity counts instead, so remaining stays 30,000.

**Withdrawal of an organization already in the program.** Org C was invited and accepted into the program last year,
then wins a solicitation linked to the same program. Its `ProgramApplication` was already `accepted`, so the award
stores `accepted` as the previous status. If Org C withdraws before any opportunity is created for it, the award is
released and the `ProgramApplication` stays `accepted`, so Org C keeps its existing place in the program.

### Risks

| Risk | Impact | Mitigation |
|---|---|---|
| New external applicants depend on CCCT-2494 (public signup and probationary organization creation). | Until it ships, only users who already belong to an organization can apply. | Build the returning-user path first; enable new-applicant signup when CCCT-2494 is available. |
| The existing opportunity-creation budget check starts subtracting active awards. | A PM may be blocked from creating an opportunity that would have been allowed before. | Show awarded amounts in the program budget summary and in the validation message so the reason is clear. |
| Awards wait on manual verification of probationary organizations. | A chosen applicant can't be awarded until a system admin verifies them, which can delay onboarding. | Show the verification state on the PM dashboard and the award screen. |
| Public, unauthenticated pages and applicant file uploads. | Criteria, questions, draft solicitations or uploaded files could leak to the wrong people. | Public querysets filter to `public` and `active`; questions and criteria are only loaded in authenticated apply and review views; answer files are served through permission-checked views. |
| The provisional nav label "Explore opportunities" overlaps with Connect's existing opportunity concept. | Users may confuse solicitations with opportunities. | Confirm the label with Product before release; the URL and internal naming stay "solicitations". |
