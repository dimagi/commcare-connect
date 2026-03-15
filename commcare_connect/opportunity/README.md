# Opportunity App

The opportunity app is the core domain of CommCare Connect. It manages paid programs where mobile users learn skills and deliver health services in exchange for payment.

## Key Concepts

- **Opportunity** — A paid program created by an organization. It has a learn app (training), a deliver app (fieldwork), a budget, and a time window.
- **OpportunityAccess** — Links a user to an opportunity. Tracks their journey: invited → accepted → learning → delivering → paid.
- **OpportunityClaim** — Created when a user commits to delivering work. Allocates visit limits per payment unit.
- **PaymentUnit** — Defines what a user gets paid for and how much. Can be hierarchical (parent/child).
- **DeliverUnit** — A type of form submission within a delivery app (e.g., "household visit", "follow-up").
- **UserVisit** — A single form submission from a mobile user during delivery.
- **CompletedWork** — Aggregates visits for a specific entity (beneficiary) and payment unit. Tracks approval status and payment.
- **Payment** — A confirmed payment to a user for approved work.

## User Journey

```text
Invited → Accepted → Learning → Claimed → Delivering → Paid
```

### 1. Invitation

An organization invites mobile users to an opportunity. This creates an access record and sends an SMS with an invite link.

### 2. Acceptance

The user clicks the invite link and accepts the opportunity.

### 3. Learning

The user opens the learn app in CommCare. Forms submitted in CommCare HQ are sent to Connect via the form receiver. Each completed module and assessment is recorded. Once all modules are complete, the user can proceed to claiming.

### 4. Claiming

After completing learning, the user claims the opportunity via the API. This allocates their share of the budget as visit limits per payment unit. A CommCareHQ mobile worker account is created if one doesn't exist.

### 5. Delivery

The user submits forms through the deliver app in CommCare. Each form submission is processed into a visit record, checked against verification flags, and aggregated into completed work. Payment amounts are calculated and cached.

### 6. Payment

Approved work accrues payment. Payments can be distributed manually via CSV upload or through the invoicing workflow for managed opportunities.

## Verification Flags

When a delivery form is received, it is checked against the opportunity's `OpportunityVerificationFlags`. Any failures are stored in `UserVisit.flag_reason` as a JSON list, and `UserVisit.flagged` is set to `True`.

| Flag                     | Check                            |
| ------------------------ | -------------------------------- |
| `duration`               | Form completed too quickly       |
| `gps`                    | GPS location missing             |
| `location`               | Visit too close to another visit |
| `duplicate`              | Entity already visited           |
| `form_submission_period` | Outside allowed time window      |
| `attachment_missing`     | Required attachments missing     |
| `form_value_not_found`   | Custom form validation failed    |
| `catchment_areas`        | Outside allowed geographic area  |
| `user_suspended`         | User is suspended                |

If `auto_approve_visits` is enabled and the visit is not flagged, the visit is automatically approved.

## Auto-Approval

Two levels of auto-approval can be enabled per opportunity:

- **`auto_approve_visits`** — Unflagged visits are automatically set to `approved`
- **`auto_approve_payments`** — `CompletedWork` is automatically approved when all visits are approved (runs via `bulk_approve_completed_work` Celery task)

## Model Relationships

```text
Organization
  └── Opportunity
        ├── CommCareApp (learn_app, deliver_app)
        │     ├── LearnModule
        │     └── DeliverUnit → PaymentUnit
        ├── PaymentUnit (amount, limits)
        │     └── PaymentUnit (child, optional)
        ├── OpportunityVerificationFlags (1:1)
        └── OpportunityAccess (per user)
              ├── OpportunityClaim (1:1)
              │     └── OpportunityClaimLimit (per PaymentUnit)
              ├── CompletedModule (per LearnModule)
              ├── Assessment (per attempt)
              ├── CompletedWork (per entity + PaymentUnit)
              ├── UserVisit (per form submission → CompletedWork)
              └── Payment (per PaymentUnit)
```

## Key Files

| File                            | Purpose                                                               |
| ------------------------------- | --------------------------------------------------------------------- |
| `models.py`                     | All model definitions                                                 |
| `api/views.py`                  | Mobile API: claim, progress, delivery                                 |
| `views.py`                      | Web UI: create, edit, finalize, dashboard                             |
| `tasks.py`                      | Celery tasks: invites, notifications, auto-approval, exports          |
| `visit_import.py`               | Bulk visit/payment import, exchange rates                             |
| `utils/completed_work.py`       | `CompletedWorkUpdater`: status calculation and payment accrual        |
| `../form_receiver/processor.py` | Processes forms from CommCareHQ into modules, assessments, and visits |
