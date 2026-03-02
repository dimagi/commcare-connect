# Opportunity Active Field — Audit Trail & Auto-Deactivation Design

**Date:** 2026-03-02

## Background

The `Opportunity.active` boolean field controls whether an opportunity is shown as Active, Ended, or Inactive in the UI. Currently there is no history of who changed this field or when. Two gaps exist:

1. Opportunities are never automatically deactivated after they end.
2. There is no record of whether a change was made by a user or the system.

## Requirements

1. The `active` flag should be automatically set to `False` 30 days after `end_date`.
2. Every change to `active` (manual or automated) must be auditable — storing who or what made the change and when.
3. Users can still manually toggle `active` via the existing opportunity edit form.
4. The opportunity dashboard shows who last changed the `active` field, with a "View history" button that opens a modal with the full change history.
5. No notification is sent when auto-deactivation occurs.

## Design

### 1. Data Model

Add `@pghistory.track(fields=["active"])` to `Opportunity`. This is consistent with how `PaymentInvoice` tracks its `status` field.

```python
@pghistory.track(fields=["active"])
class Opportunity(BaseModel):
    active = models.BooleanField(default=True)
    ...
```

pghistory generates an `OpportunityActiveEvent` table with:

| Column | Description |
|---|---|
| `pgh_id` | Auto-increment PK |
| `pgh_created_at` | Timestamp of the change |
| `pgh_label` | `"insert"` or `"update"` |
| `pgh_obj_id` | FK to `Opportunity.id` |
| `pgh_context_id` | FK to `pghistory.Context` |
| `active` | The new value of the field |

The existing `CustomPGHistoryMiddleware` stores `user` (id), `username`, and `user_email` in the context on every web request — so manual changes are automatically attributed. System changes (Celery) use `pghistory.context(user=None, username="system", user_email="")`.

A migration is required.

### 2. Auto-Deactivation Celery Task

A new periodic task runs daily:

```python
@celery_app.task()
def auto_deactivate_ended_opportunities():
    cutoff = datetime.date.today() - datetime.timedelta(days=30)
    opportunities = Opportunity.objects.filter(active=True, end_date__lte=cutoff)
    with pghistory.context(user=None, username="system", user_email=""):
        opportunities.update(active=False)
```

Registered in the DB-backed beat schedule alongside `send_notification_inactive_users`. Runs daily (e.g. midnight UTC).

### 3. Manual Toggle

No changes to `OpportunityForm` or its view. The `active` checkbox is already present. The middleware context is already attached on every POST, so every manual toggle is captured automatically in `OpportunityActiveEvent`.

### 4. Dashboard UI

On the opportunity dashboard, next to the Active/Ended/Inactive badge:

- **Latest change line:** "Last changed by `<username>` on `<date>`" — drawn from the most recent `OpportunityActiveEvent` row.
  - If `pgh_context.username == "system"` (or context is null), display "System" as the actor.
- **"View history" button:** Opens a modal (htmx or Alpine.js) listing all `OpportunityActiveEvent` rows in reverse chronological order.

Modal table columns: **Changed at** | **Changed to** (Active / Inactive) | **By** (username or "System").

The view passes the latest event and a queryset of all events to the template context.

## Files to Change

| File | Change |
|---|---|
| `commcare_connect/opportunity/models.py` | Add `@pghistory.track(fields=["active"])` to `Opportunity` |
| `commcare_connect/opportunity/tasks.py` | Add `auto_deactivate_ended_opportunities` task + beat schedule entry |
| `commcare_connect/opportunity/views.py` | Pass active-field event history to dashboard context |
| `commcare_connect/templates/opportunity/dashboard.html` | Add "last changed by" line + "View history" button + modal |
| `commcare_connect/opportunity/migrations/` | New migration for pghistory event table + triggers |
| `commcare_connect/opportunity/tests/` | Tests for Celery task and dashboard context |

## Out of Scope

- Notifications on auto-deactivation
- Changes to the opportunities list view
- API exposure of the audit trail
