# Opportunity Active Field — Audit Trail & Auto-Deactivation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Track every change to `Opportunity.active` (by user or system), auto-deactivate opportunities 30 days after they end, and show the latest actor + a full history modal on the dashboard.

**Architecture:** Add `@pghistory.track(fields=["active"])` to `Opportunity` — the same pattern already used for `PaymentInvoice.status`. A daily Celery task performs bulk deactivation inside a `pghistory.context(username="system")` block. The dashboard view passes the latest event and all events to the template; a small Alpine.js modal renders the full history.

**Tech Stack:** Django 4.2, pghistory 3.x, django-celery-beat (DB-backed schedules via migration), Alpine.js (already used throughout), pytest + factory-boy.

---

### Task 1: Add pghistory tracking to `Opportunity.active`

**Files:**
- Modify: `commcare_connect/opportunity/models.py` (line 82 — the `Opportunity` class definition)

**Step 1: Write the failing test**

Add to `commcare_connect/opportunity/tests/test_models.py`:

```python
@pytest.mark.django_db
class TestOpportunityActiveTracking:
    def test_pghistory_records_initial_active_state(self):
        from commcare_connect.opportunity.models import OpportunityActiveEvent
        opp = OpportunityFactory()
        events = OpportunityActiveEvent.objects.filter(pgh_obj=opp)
        assert events.count() == 1
        assert events.first().active is True

    def test_pghistory_records_manual_deactivation(self):
        from commcare_connect.opportunity.models import OpportunityActiveEvent
        opp = OpportunityFactory()
        opp.active = False
        opp.save()
        events = OpportunityActiveEvent.objects.filter(pgh_obj=opp).order_by("pgh_created_at")
        assert events.count() == 2
        assert events.last().active is False

    def test_pghistory_context_is_none_without_request(self):
        from commcare_connect.opportunity.models import OpportunityActiveEvent
        opp = OpportunityFactory()
        event = OpportunityActiveEvent.objects.filter(pgh_obj=opp).first()
        # No request context in tests
        assert event.pgh_context is None
```

**Step 2: Run test to verify it fails**

```bash
pytest commcare_connect/opportunity/tests/test_models.py::TestOpportunityActiveTracking -v
```

Expected: `ImportError` or `AttributeError` — `OpportunityActiveEvent` does not exist yet.

**Step 3: Add pghistory tracking to the model**

In `commcare_connect/opportunity/models.py`, change line 82 from:

```python
class Opportunity(BaseModel):
```

to:

```python
@pghistory.track(fields=["active"])
class Opportunity(BaseModel):
```

`pghistory` is already imported at line 6. No other imports needed.

**Step 4: Generate the migration**

```bash
./manage.py makemigrations opportunity
```

Expected: Creates `commcare_connect/opportunity/migrations/0114_opportunityactiveevent.py` (or similar). The migration will include `CreateModel` for `OpportunityActiveEvent` and two `AddTrigger` operations (INSERT and UPDATE).

**Step 5: Run the migration**

```bash
./manage.py migrate
```

Expected: Applied successfully.

**Step 6: Run the tests to verify they pass**

```bash
pytest commcare_connect/opportunity/tests/test_models.py::TestOpportunityActiveTracking -v
```

Expected: All 3 tests PASS.

**Step 7: Commit**

```bash
git add commcare_connect/opportunity/models.py commcare_connect/opportunity/migrations/0114_*.py commcare_connect/opportunity/tests/test_models.py
git commit -m "feat: track Opportunity.active field changes with pghistory"
```

---

### Task 2: Add the `auto_deactivate_ended_opportunities` Celery task

**Files:**
- Modify: `commcare_connect/opportunity/tasks.py`
- Create: `commcare_connect/opportunity/migrations/0115_create_auto_deactivate_periodic_task.py`

**Context:** Periodic tasks in this project are registered via Django migrations using `django-celery-beat`. See `0026_create_send_inactive_notification_periodic_task.py` for the exact pattern.

**Step 1: Write the failing test**

Add to `commcare_connect/opportunity/tests/test_tasks.py`:

```python
import datetime
import pytest
from commcare_connect.opportunity.models import OpportunityActiveEvent
from commcare_connect.opportunity.tasks import auto_deactivate_ended_opportunities
from commcare_connect.opportunity.tests.factories import OpportunityFactory


@pytest.mark.django_db
class TestAutoDeactivateEndedOpportunities:
    def test_deactivates_opportunities_30_days_after_end(self):
        cutoff = datetime.date.today() - datetime.timedelta(days=30)
        opp_to_deactivate = OpportunityFactory(active=True, end_date=cutoff)
        opp_still_active = OpportunityFactory(active=True, end_date=datetime.date.today())
        opp_already_inactive = OpportunityFactory(active=False, end_date=cutoff)

        auto_deactivate_ended_opportunities()

        opp_to_deactivate.refresh_from_db()
        opp_still_active.refresh_from_db()
        opp_already_inactive.refresh_from_db()

        assert opp_to_deactivate.active is False
        assert opp_still_active.active is True
        assert opp_already_inactive.active is False  # unchanged

    def test_records_pghistory_event_with_system_context(self):
        cutoff = datetime.date.today() - datetime.timedelta(days=30)
        opp = OpportunityFactory(active=True, end_date=cutoff)

        auto_deactivate_ended_opportunities()

        # Should have 2 events: initial create + the deactivation
        events = OpportunityActiveEvent.objects.filter(pgh_obj=opp).order_by("pgh_created_at")
        assert events.count() == 2
        deactivation_event = events.last()
        assert deactivation_event.active is False
        assert deactivation_event.pgh_context is not None
        assert deactivation_event.pgh_context.metadata["username"] == "system"
```

**Step 2: Run test to verify it fails**

```bash
pytest commcare_connect/opportunity/tests/test_tasks.py::TestAutoDeactivateEndedOpportunities -v
```

Expected: `ImportError` — `auto_deactivate_ended_opportunities` does not exist yet.

**Step 3: Add the task to `tasks.py`**

At the top of `commcare_connect/opportunity/tasks.py`, `pghistory` is not yet imported. Add it to the existing imports block:

```python
import pghistory
```

Then add the task function after `send_notification_inactive_users` (around line 256):

```python
@celery_app.task()
def auto_deactivate_ended_opportunities():
    cutoff = datetime.date.today() - datetime.timedelta(days=30)
    opportunities = Opportunity.objects.filter(active=True, end_date__lte=cutoff)
    with pghistory.context(user=None, username="system", user_email=""):
        opportunities.update(active=False)
```

**Step 4: Run the tests to verify they pass**

```bash
pytest commcare_connect/opportunity/tests/test_tasks.py::TestAutoDeactivateEndedOpportunities -v
```

Expected: All 2 tests PASS.

**Step 5: Create the migration to register the periodic task**

Create `commcare_connect/opportunity/migrations/0115_create_auto_deactivate_periodic_task.py`:

```python
from django.db import migrations
from django_celery_beat.models import CrontabSchedule, PeriodicTask


def create_auto_deactivate_periodic_task(apps, schema_editor):
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="0",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        name="auto_deactivate_ended_opportunities",
        defaults={
            "crontab": schedule,
            "task": "commcare_connect.opportunity.tasks.auto_deactivate_ended_opportunities",
        },
    )


def delete_auto_deactivate_periodic_task(apps, schema_editor):
    PeriodicTask.objects.filter(name="auto_deactivate_ended_opportunities").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0114_opportunityactiveevent"),  # adjust number to match actual migration name
    ]

    operations = [
        migrations.RunPython(
            create_auto_deactivate_periodic_task,
            delete_auto_deactivate_periodic_task,
            hints={"run_on_secondary": False},
        )
    ]
```

> **Note:** Confirm the dependency name matches the migration generated in Task 1 by running `ls commcare_connect/opportunity/migrations/0114*`.

**Step 6: Run the migration**

```bash
./manage.py migrate
```

Expected: Applied successfully.

**Step 7: Commit**

```bash
git add commcare_connect/opportunity/tasks.py commcare_connect/opportunity/migrations/0115_*.py commcare_connect/opportunity/tests/test_tasks.py
git commit -m "feat: add daily task to auto-deactivate opportunities 30 days after end date"
```

---

### Task 3: Pass active-field event history to the dashboard view

**Files:**
- Modify: `commcare_connect/opportunity/views.py` — `OpportunityDashboard.get_context_data` (starts at line 407)

**Step 1: Write the failing test**

Add to `commcare_connect/opportunity/tests/test_views.py`:

```python
@pytest.mark.django_db
class TestOpportunityDashboardActiveHistory:
    def test_dashboard_context_includes_active_events(self, client, org_user_admin, opportunity):
        from commcare_connect.opportunity.models import OpportunityActiveEvent
        client.force_login(org_user_admin.user)
        # Trigger a second event by changing active
        opportunity.active = False
        opportunity.save()

        url = reverse(
            "opportunity:detail",
            kwargs={"org_slug": org_user_admin.organization.slug, "opp_id": opportunity.opportunity_id},
        )
        response = client.get(url)

        assert response.status_code == 200
        assert "active_latest_event" in response.context
        assert "active_events" in response.context
        assert response.context["active_latest_event"].active is False
        assert response.context["active_events"].count() == 2
```

**Step 2: Run test to verify it fails**

```bash
pytest commcare_connect/opportunity/tests/test_views.py::TestOpportunityDashboardActiveHistory -v
```

Expected: `KeyError` — `active_latest_event` not in context yet.

**Step 3: Update `OpportunityDashboard.get_context_data`**

In `commcare_connect/opportunity/views.py`, inside `OpportunityDashboard.get_context_data` (after line ~433), add before the final `return context`:

```python
from commcare_connect.opportunity.models import OpportunityActiveEvent  # add to top-of-file imports instead

active_events = OpportunityActiveEvent.objects.filter(pgh_obj=object).select_related(
    "pgh_context"
).order_by("-pgh_created_at")
context["active_events"] = active_events
context["active_latest_event"] = active_events.first()
```

> **Note:** Move the import to the top-level imports of `views.py`, alongside the other `opportunity.models` imports (around line 38+).

**Step 4: Run the tests to verify they pass**

```bash
pytest commcare_connect/opportunity/tests/test_views.py::TestOpportunityDashboardActiveHistory -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add commcare_connect/opportunity/views.py commcare_connect/opportunity/tests/test_views.py
git commit -m "feat: pass active field event history to opportunity dashboard context"
```

---

### Task 4: Add "last changed by" line and history modal to the dashboard template

**Files:**
- Modify: `commcare_connect/templates/opportunity/dashboard.html`

**Context:** The active/ended/inactive badge is in `dashboard.html` at lines 107–115. The template already uses Alpine.js and htmx. Other modals in the project (e.g. `export_modal.html`) use `x-data="{show: false}"` + `x-show="show"` patterns. The `pgh_context` on an event has `metadata` dict containing `username` and `user_email` (set by `CustomPGHistoryMiddleware`). If context is `null` or `username == "system"`, show "System".

**Step 1: Replace the badge block with the badge + last-changed line + history button**

In `commcare_connect/templates/opportunity/dashboard.html`, replace lines 107–115:

```html
                  <div>
                    {% if object.is_active %}
                        <span class="badge badge-md positive-dark">Active</span>
                    {% elif object.has_ended %}
                        <span class="badge badge-md warning-dark">Ended</span>
                    {% else %}
                        <span class="badge badge-md negative-dark">Inactive</span>
                    {% endif %}
                  </div>
```

with:

```html
                  <div x-data="{ showActiveHistory: false }">
                    <div class="flex items-center gap-2">
                      {% if object.is_active %}
                          <span class="badge badge-md positive-dark">Active</span>
                      {% elif object.has_ended %}
                          <span class="badge badge-md warning-dark">Ended</span>
                      {% else %}
                          <span class="badge badge-md negative-dark">Inactive</span>
                      {% endif %}
                    </div>
                    {% if active_latest_event %}
                    <div class="flex items-center gap-2 mt-1">
                      <p class="text-xs text-slate-500">
                        Last changed by
                        {% with ctx=active_latest_event.pgh_context %}
                          {% if not ctx or ctx.metadata.username == "system" %}
                            <span class="font-medium">System</span>
                          {% else %}
                            <span class="font-medium">{{ ctx.metadata.username }}</span>
                          {% endif %}
                        {% endwith %}
                        on {{ active_latest_event.pgh_created_at|date:"d M Y" }}
                      </p>
                      <button
                        type="button"
                        @click="showActiveHistory = true"
                        class="text-xs text-brand-cornflower-blue underline underline-offset-2">
                        View history
                      </button>
                    </div>
                    {% endif %}

                    <!-- Active field history modal -->
                    <div x-show="showActiveHistory" x-cloak class="modal-backdrop" @click.self="showActiveHistory = false">
                      <div class="modal">
                        <div class="header text-lg font-semibold text-brand-deep-purple">
                          <h1>Active Status History</h1>
                          <button @click="showActiveHistory = false">
                            <i class="fa-solid fa-xmark text-xl"></i>
                          </button>
                        </div>
                        <div class="content">
                          <div class="modal-body overflow-y-auto max-h-96">
                            <table class="w-full text-sm text-left text-brand-deep-purple">
                              <thead class="text-xs uppercase bg-slate-100">
                                <tr>
                                  <th class="px-4 py-2">Changed at</th>
                                  <th class="px-4 py-2">Changed to</th>
                                  <th class="px-4 py-2">By</th>
                                </tr>
                              </thead>
                              <tbody>
                                {% for event in active_events %}
                                <tr class="border-b border-slate-200">
                                  <td class="px-4 py-2">{{ event.pgh_created_at|date:"d M Y H:i" }}</td>
                                  <td class="px-4 py-2">
                                    {% if event.active %}
                                      <span class="badge badge-sm bg-green-600/20 text-green-600">Active</span>
                                    {% else %}
                                      <span class="badge badge-sm bg-slate-100 text-slate-400">Inactive</span>
                                    {% endif %}
                                  </td>
                                  <td class="px-4 py-2">
                                    {% with ctx=event.pgh_context %}
                                      {% if not ctx or ctx.metadata.username == "system" %}
                                        System
                                      {% else %}
                                        {{ ctx.metadata.username }}
                                      {% endif %}
                                    {% endwith %}
                                  </td>
                                </tr>
                                {% empty %}
                                <tr>
                                  <td colspan="3" class="px-4 py-4 text-center text-slate-400">No history available.</td>
                                </tr>
                                {% endfor %}
                              </tbody>
                            </table>
                          </div>
                          <div class="footer">
                            <button @click="showActiveHistory = false" class="button button-md outline-style">Close</button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
```

**Step 2: Smoke-test manually**

```bash
./manage.py runserver
```

Navigate to any opportunity dashboard page. Verify:
- The badge renders correctly.
- "Last changed by System on …" (or username) appears below it.
- "View history" button opens the modal.
- The modal table lists all events.

**Step 3: Commit**

```bash
git add commcare_connect/templates/opportunity/dashboard.html
git commit -m "feat: show active status last-changed attribution and history modal on opportunity dashboard"
```

---

### Task 5: Run the full test suite and fix any issues

**Step 1: Run all opportunity tests**

```bash
pytest commcare_connect/opportunity/tests/ -v
```

Expected: All pass. If any existing test creates an `Opportunity` and the new pghistory trigger causes issues (e.g. DB constraint in test isolation), check whether `OpportunityActiveEvent` is being cleaned up correctly between tests. The `pytest-django` DB fixtures handle this automatically via transactions.

**Step 2: Run pre-commit checks**

```bash
pre-commit run -a
```

Fix any issues (black formatting, isort, flake8).

**Step 3: Final commit if any fixes were needed**

```bash
git add -u
git commit -m "fix: pre-commit and test suite cleanup"
```
