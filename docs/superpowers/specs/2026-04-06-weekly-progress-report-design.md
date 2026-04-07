# Weekly Progress Report — Design Spec

## Overview

A weekly progress report generated every Sunday night for opportunities that have the feature enabled via a waffle flag. The report evaluates each active worker's performance using a set of predefined calculations and surfaces workers who fall outside acceptable ranges for reviewer action.

## Data Models

All models live in the new `commcare_connect/audit/` Django app and extend `BaseModel`.

### WeeklyReport (the audit record)

| Field | Type | Notes |
|-------|------|-------|
| `weekly_report_id` | UUID | Primary identifier |
| `opportunity` | FK(Opportunity) | The opportunity this report covers |
| `period_start` | DateField | Monday of the report week |
| `period_end` | DateField | Sunday of the report week |
| `status` | CharField(choices) | `pending` or `completed` |
| `completed_by` | FK(User, null) | User who completed the audit |
| `completed_date` | DateTimeField(null) | When the audit was completed |

Inherits `created_by`, `modified_by`, `date_created`, `date_modified` from `BaseModel`.

### WeeklyReportEntry (one per OpportunityAccess per report)

| Field | Type | Notes |
|-------|------|-------|
| `weekly_report_entry_id` | UUID | Primary identifier |
| `weekly_report` | FK(WeeklyReport) | Parent report |
| `opportunity_access` | FK(OpportunityAccess) | The worker access record |
| `results` | JSONField | Maps calculation name to result object (see below) |
| `flagged` | BooleanField | True if any calculation is out of range (denormalized) |
| `reviewed` | BooleanField | Starts False; set True when reviewer takes action |
| `review_action` | CharField(choices, null) | `none` or `tasks_assigned` |

### Results JSON structure

```json
{
  "form_value_match_rate": {
    "value": 0.72,
    "has_sufficient_data": true,
    "in_range": false,
    "label": "Form Value Match Rate"
  },
  "another_calc": {
    "value": null,
    "has_sufficient_data": false,
    "in_range": true,
    "label": "Another Calc"
  }
}
```

Historical reports always display exactly what was calculated at generation time, regardless of subsequent data or calculation changes.

## Calculation System

### Location

`commcare_connect/audit/calculations.py`

### Result dataclass

```python
@dataclass
class CalculationResult:
    name: str        # registry key, e.g. "form_value_match_rate"
    label: str       # human-readable column header
    value: Any       # computed value (number, percentage, etc.)
    has_sufficient_data: bool
    in_range: bool
```

### Registry

A decorator-based registry. Each calculation is a function with signature:

```python
@register_calculation
def form_value_match_rate(opportunity_access, period_start, period_end) -> CalculationResult:
    ...
```

At generation time, all registered calculations are iterated and called for each `OpportunityAccess`.

### Future extension: per-opportunity calculations

When needed, add an `enabled_calculations` JSONField (or similar config) on `Opportunity` listing which calculation names to run. If empty, all registered calculations run (backwards compatible). No structural changes to report models needed — the `results` JSON field already handles variable sets.

## Report Generation (Celery Task)

### Schedule

Sunday night (e.g., 11 PM UTC) via a Celery beat periodic task. The `CrontabSchedule` + `PeriodicTask` are created in a migration, following the existing pattern (e.g., `opportunity/migrations/0078_fetch_rates_task.py`).

### Task logic

1. Query all active `Opportunity` records that have the `WEEKLY_PROGRESS_REPORT` waffle flag enabled.
2. For each opportunity:
   a. Compute `period_start` (Monday) and `period_end` (Sunday) for the preceding 7 days.
   b. Create a `WeeklyReport` with `status=pending`.
   c. Query all active/accepted `OpportunityAccess` records.
   d. For each access, run all registered calculations and collect results.
   e. Bulk-create `WeeklyReportEntry` records. Set `flagged=True` if any calculation has `in_range=False`.
3. If no active accesses exist, still create the `WeeklyReport` (empty report confirms the audit occurred).

### Error handling

If generation fails for one opportunity, log the error and continue to the next. Do not fail the entire batch.

## Feature Flag

A new waffle flag constant `WEEKLY_PROGRESS_REPORT` in `commcare_connect/flags/flag_names.py`. The flag is toggled per-opportunity using the existing `Flag.opportunities` M2M relationship.

The flag gates:
- Report generation (only opportunities with the flag get reports)
- UI visibility (nav links and pages hidden when flag is inactive)

## UI — Report List Page

**URL:** `/a/<org_slug>/opportunity/<opp_id>/weekly-reports/`

**Content:** A table listing all `WeeklyReport` records for the opportunity, most recent first.

| Column | Content |
|--------|---------|
| Period | e.g., "Mar 30 - Apr 5, 2026" |
| Status | `pending` / `completed` |
| Completed by | User name (if completed) |
| Completed date | Datetime (if completed) |

Each row links to the detail page.

## UI — Weekly Report Detail Page

**URL:** `/a/<org_slug>/opportunity/<opp_id>/weekly-reports/<report_id>/`

### Top table: "Action Required"

Rows: `WeeklyReportEntry` where `flagged=True` and `reviewed=False`.

| Column | Content |
|--------|---------|
| User | Worker's display name |
| [One column per calculation] | Value, or "N/A" if insufficient data. Cell colored red if `in_range=False` |
| Action | Button to open the task assignment modal |

Filterable by user name.

### Bottom table: "No Further Action"

Rows: `WeeklyReportEntry` where `flagged=False` OR `reviewed=True`.

| Column | Content |
|--------|---------|
| User | Worker's display name |
| [One column per calculation] | Same display as above |
| Action | "Done" for reviewed rows, empty for unflagged rows |

Filterable by user name.

### Row transition

When a reviewer completes the modal action for a flagged row:
- `reviewed` is set to True, `review_action` is set accordingly
- The row moves from the top table to the bottom table (htmx swap or page reload)
- The Action column shows "Done"

### Complete Audit button

At the bottom of the page:
- Disabled while any `flagged=True, reviewed=False` entries remain
- On click: sets `WeeklyReport.status=completed`, `completed_by`, `completed_date`
- After completion, the page becomes read-only

## UI — Task Assignment Modal

**Trigger:** Clicking the action button on a flagged row.

**Content:**
- Header with the worker's name
- List of the opportunity's `TaskType` records, each with a checkbox
- "No action needed" button

**Actions:**

- **Assign selected tasks:** Creates `AssignedTask` records linking selected `TaskType`s to the user. Sets `review_action=tasks_assigned`, `reviewed=True`. Closes modal, moves row to bottom table.
- **No action needed:** Sets `review_action=none`, `reviewed=True`. Closes modal, moves row to bottom table. No `AssignedTask` records created.

**Implementation:** Alpine.js modal + htmx POST, following the existing pattern in `edit_assigned_task_modal.html`.

## File Organization

### New app: `commcare_connect/audit/`

```
commcare_connect/audit/
  __init__.py
  admin.py
  apps.py
  calculations.py        # Calculation registry + functions
  models.py              # WeeklyReport, WeeklyReportEntry
  tasks.py               # Celery periodic task
  urls.py
  views.py               # List, detail, modal action, complete audit
  templates/audit/
    weekly_report_list.html
    weekly_report_detail.html
    weekly_report_task_modal.html
  tests/
    __init__.py
    factories.py
    test_calculations.py
    test_tasks.py
    test_views.py
  migrations/
    __init__.py
    0001_initial.py      # Models
    0002_weekly_report_periodic_task.py  # CrontabSchedule + PeriodicTask
```

### Modified files

| File | Change |
|------|--------|
| `commcare_connect/flags/flag_names.py` | Add `WEEKLY_PROGRESS_REPORT` constant |
| `config/settings/base.py` | Add `commcare_connect.audit` to `LOCAL_APPS` |
| `commcare_connect/opportunity/urls.py` | Include audit URL patterns under the opportunity prefix |
