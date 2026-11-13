# Opportunity App

The opportunity app is the core domain of Connect. It manages paid programs where mobile users learn skills and deliver health services in exchange for payment.

## Key Concepts

- **Opportunity** — A paid program created by an organization. It has a learn app (training), a deliver app (fieldwork), a budget, and a time window.
- **OpportunityAccess** — Links a user to an opportunity. Tracks their journey: invited → accepted → learning → claimed → delivering → paid.
- **OpportunityClaim** — Created when a user commits to delivering work. Allocates visit limits per payment unit.
- **PaymentUnit** — Defines what a user gets paid for and how much. Can be hierarchical (parent/child).
- **PaymentUnitRate** — Pay rate history of a payment unit, one row per pay rate. Rows are never deleted except with their opportunity.
- **DeliverUnit** — A type of form submission within a delivery app (e.g., "household visit", "follow-up").
- **Task** — A named task within a learn/deliver app, distinct from a LearnModule.
- **TaskUnit** — A task module from the delivery app XML. A task unit links to a task.
- **UserVisit** — A single form submission from a mobile user during delivery.
- **CompletedWork** — Aggregates visits for a specific entity (beneficiary) and payment unit. Tracks approval status and payment.
- **AssignedTask** — Tracks completion of individual tasks by a user (assigned → completed).
- **Payment** — A confirmed payment to a user for approved work.
- **PaymentInvoice** — An invoice for a managed opportunity. Tracks status through NM/PM review workflow and links to completed work and payments.

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

Approved work accrues payment. Payments can be distributed manually via CSV upload or through the invoicing workflow for managed opportunities. See [PAYMENTS.md](PAYMENTS.md) for how accrual is calculated and how invoicing works.

## Verification Flags

When a delivery form is received, it is checked against the opportunity's `OpportunityVerificationFlags`. Any failures are stored in `UserVisit.flag_reason` as a JSON list, and `UserVisit.flagged` is set to `True`.

| Flag                     | Check                            | Configured on                  |
| ------------------------ | -------------------------------- | ------------------------------ |
| `duplicate`              | Entity already visited           | `OpportunityVerificationFlags` |
| `gps`                    | GPS location missing             | `OpportunityVerificationFlags` |
| `location`               | Visit too close to another visit | `OpportunityVerificationFlags` |
| `form_submission_period` | Outside allowed time window      | `OpportunityVerificationFlags` |
| `duration`               | Form completed too quickly       | `DeliverUnitFlagRules`         |
| `attachment_missing`     | Required attachments missing     | `DeliverUnitFlagRules`         |
| `form_value_not_found`   | Custom form validation failed    | `FormJsonValidationRules`      |
| `user_suspended`         | User is suspended                | `OpportunityAccess.suspended`  |
| `pending_task`           | Worker has an incomplete task    | Always on (`AssignedTask`)     |

Flag codes are defined in `commcare_connect/utils/flags.py`. It also keeps `catchment`, whose check has been removed, so that old `flag_reason` values still have a label. `user_suspended` and `pending_task` reject the visit outright; see the visit status table in `../form_receiver/README.md`. If `auto_approve_visits` is enabled and the visit is not flagged, the visit is automatically approved.

## Auto-Approval

Two levels of auto-approval can be enabled per opportunity:

- **`auto_approve_visits`** — Unflagged visits are automatically set to `approved`
- **`auto_approve_payments`** — `CompletedWork` is automatically approved when all visits are approved (runs via `bulk_approve_completed_work` Celery task)

## Model Relationships

```text
Organization
  ├── CommCareApp
  │     ├── LearnModule
  │     ├── Task
  │     └── DeliverUnit → PaymentUnit
  └── Opportunity (refs learn_app, deliver_app)
        ├── PaymentUnit (amount, limits)
        │     └── PaymentUnit (child, optional)
        ├── OpportunityVerificationFlags (1:1)
        ├── DeliverUnitFlagRules (per DeliverUnit)
        ├── FormJsonValidationRules (per DeliverUnit, M2M)
        ├── PaymentInvoice
        │     └── Payment (1:1)
        └── OpportunityAccess (per user)
              ├── OpportunityClaim (1:1)
              │     └── OpportunityClaimLimit (per PaymentUnit)
              ├── CompletedModule (per LearnModule)
              ├── AssignedTask (per Task)
              ├── Assessment (per attempt)
              ├── CompletedWork (per entity + PaymentUnit)
              │     └── UserVisit (per form submission)
              └── Payment (per PaymentUnit, for FLW payments)
```

## Key Files

| File                            | Purpose                                                                                          |
| ------------------------------- | ------------------------------------------------------------------------------------------------ |
| `models/`                       | Models, one module per domain; import them from the package root                                 |
| `api/views.py`                  | Mobile API: claim, progress, delivery                                                            |
| `views/`                        | Web UI; invoice views in `invoices.py`, worker pages in `worker.py`, the rest in `__init__.py`   |
| `tasks.py`                      | Celery tasks: invites, notifications, auto-approval, exports                                     |
| `forms/`                        | Django forms; invoice forms in `invoices.py`, the rest in `__init__`                             |
| `tables/`                       | Tables; invoice tables in `invoices.py`, worker tables in `worker.py`, the rest in `__init__.py` |
| `export.py`                     | Exports: visits, payments, user status, work status                                              |
| `visit_import.py`               | Bulk visit/payment import, exchange rates                                                        |
| `deletion.py`                   | `delete_opportunity()` for stale opportunity cleanup                                             |
| `utils/completed_work.py`       | `CompletedWorkUpdater`: status calculation and payment accrual                                   |
| `utils/invoice.py`              | Invoice number generation and start date utilities                                               |
| `../form_receiver/processor.py` | Processes forms from CommCareHQ into modules, assessments, and visits                            |

`views/`, `forms/` and `tables/` are being split by feature. A feature module such as `invoices.py` or `worker.py`
imports shared helpers from its package's `__init__.py`; the `__init__.py` never imports it back, so import its names
from the submodule itself.
