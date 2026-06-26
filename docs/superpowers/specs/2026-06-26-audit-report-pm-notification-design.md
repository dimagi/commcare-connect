# Notify Program Managers when a new audit report is generated

## Summary

When audit reports are generated, notify the Program Manager (PM) admins
associated with the affected opportunities by email. Each email lists the new
report(s) — opportunity name, reporting period, and a link to the report detail
page. Reports are batched **per PM org per run**: when multiple reports belong
to the same PM org, its admins receive a single email listing all of them
rather than one email per report.

## Background

Audit reports live in `commcare_connect/audit/`. They are created by the
weekly Celery task `generate_audit_reports`
(`audit/tasks.py`, scheduled Mondays 02:00 UTC), which calls the service
`generate_audit_report_for_opportunity()` (`audit/services.py`) for each
in-scope opportunity. A report is created with `status=PENDING`; a reviewer
later works through flagged entries and marks it `COMPLETED`.

The task selects opportunities via the `WEEKLY_PERFORMANCE_REPORT` waffle flag:
an opportunity is in scope if it is listed directly on the flag, or if its
`program` is in the flag's programs:

```python
opportunities = Opportunity.objects.filter(
    Q(pk__in=flag.opportunities.values_list("pk", flat=True)) | Q(program__in=flag.programs.all()),
    active=True,
).distinct()
```

### Relevant data model (post CCCT-2494)

- `Opportunity.program` — direct, nullable FK to `program.Program`
  (`opportunity/models.py:119`). This is the canonical link from an
  opportunity to its program. (`ManagedOpportunity.program` was renamed to
  `program_old` and is being deprecated — do not use it.)
- `Program.organization` — the Program Manager organization
  (`program/models.py:22`).
- `Organization.program_manager` — boolean marking an org as a PM org.
- `UserOrganizationMembership.role` — `admin` / `member` / `viewer`. A user is
  a "program manager" when they are an **admin** of a `program_manager` org
  (`UserOrganizationMembership.is_program_manager`).

### Existing email pattern

- `send_mail_async` (`utils/tasks.py`) is the generic Celery task that performs
  the actual SMTP send. Callers invoke `send_mail_async.delay(...)`.
- Notification helpers in `program/tasks.py` (e.g. `send_opportunity_created_email`)
  are **plain functions** that resolve recipients, render paired `.txt`/`.html`
  templates, and call `send_mail_async.delay(...)`. They are invoked from within
  other Celery tasks.
- Request-less absolute URLs are built with
  `build_absolute_uri(None, reverse(...))` (from `allauth.utils`).
- Email templates are paired `.txt` + `.html` files extending
  `email/base.txt` / `email/base.html`, with content in `{% block content %}`.

## Decisions

| Decision | Choice |
|----------|--------|
| **Trigger point** | On report **creation** (PENDING), via the weekly task path. |
| **Recipients** | **Admins** of the opportunity's PM org only (`role=admin`). These are exactly the users who can open the report link. |
| **Non-program opportunities** | If `opportunity.program_id is None`, no PM org exists → that report is skipped (no email). |
| **Batching** | One email per PM org per run. When several reports in a run belong to the same PM org, the org's admins receive a **single** email listing all of them — never one email per report. |
| **Email content** | Minimal, per report listed: opportunity name, reporting period (start–end), link to report detail. No data-dependent stats. |
| **New Celery task?** | No. The notification helper is a plain function running inside the already-running `generate_audit_reports` task; the SMTP send is deferred to the existing `send_mail_async` task. |

### Why group by PM org (not by program)

Recipients are an organization's admins. A PM org can own multiple programs,
and a program can have multiple opportunities — all resolving to the same set
of admins. Grouping by **PM org** is therefore what prevents a given admin from
receiving multiple emails. Grouping by program would not (two programs under
one PM org would still produce two emails to the same people).

## Design

### Control flow

```
generate_audit_reports            (existing @task — already running)
  ├─ reports = []
  ├─ for each in-scope opportunity:
  │      report = generate_audit_report_for_opportunity(opp)   (unchanged)
  │      reports.append(report)
  └─ send_new_audit_report_notifications(reports)              (new plain fn)
         └─ for each PM org group:
                send_mail_async.delay(...)                     (existing @task — SMTP send)
```

Reports are collected during the loop, then a single call after the loop sends
the batched notifications. The post-loop call is wrapped in its own `try/except`
so a mail failure is logged but never propagates out of the task. Per-report
generation keeps its own existing `try/except` (a generation failure just means
that report is absent from the list).

### Component 1: `send_new_audit_report_notifications(reports)`

New plain function in `commcare_connect/audit/tasks.py`, modelled on
`send_opportunity_expiry_reminder_emails` in `program/tasks.py` (group by PM
org → one email per org listing multiple items).

Behaviour:

1. **Group reports by PM org.** Iterate `reports`; skip any whose
   `report.opportunity.program_id is None`. Key the group by
   `opportunity.program.organization` (the PM org). Build, per org, a list of
   report items:
   ```python
   from collections import defaultdict

   pm_orgs = {}                       # org_id -> Organization
   reports_by_org = defaultdict(list) # org_id -> [ {name, period_start, period_end, url}, ... ]

   for report in reports:
       opportunity = report.opportunity
       if opportunity.program_id is None:
           continue
       pm_org = opportunity.program.organization
       pm_orgs[pm_org.id] = pm_org
       report_url = build_absolute_uri(
           None,
           reverse(
               "opportunity:audit:audit_report_detail",
               kwargs={
                   "org_slug": pm_org.slug,
                   "opp_id": opportunity.opportunity_id,
                   "audit_report_id": report.audit_report_id,
               },
           ),
       )
       reports_by_org[pm_org.id].append(
           {
               "opportunity_name": opportunity.name,
               "period_start": report.period_start,
               "period_end": report.period_end,
               "url": report_url,
           }
       )
   ```

2. **Resolve admin emails per org in one query** (avoid N+1):
   ```python
   org_emails = defaultdict(list)
   for org_id, email in UserOrganizationMembership.objects.filter(
       organization_id__in=reports_by_org.keys(),
       role=UserOrganizationMembership.Role.ADMIN,
   ).values_list("organization_id", "user__email"):
       if email:
           org_emails[org_id].append(email)
   ```

3. **Send one email per org.** For each `org_id` in `reports_by_org`: skip if no
   admin emails; otherwise render the `.txt`/`.html` templates with context
   `{"organization": pm_org, "reports": [...]}` and queue the send. Wrap each
   org's send in its own `try/except` so one org's failure does not block the
   others:
   ```python
   send_mail_async.delay(
       subject=subject,                       # see "Subject line" below
       message=message,
       recipient_list=org_emails[org_id],
       html_message=html_message,
   )
   ```

### Subject line

Count-aware, via `ngettext` (matching the expiry-reminder helper):

- 1 report: `New audit report for {opportunity_name}` (the previously approved
  single-report wording).
- N reports: `{N} new audit reports are ready for review`.

### Query efficiency

`report.opportunity` is the in-memory instance passed into
`generate_audit_report_for_opportunity`, so it is already loaded. Each distinct
opportunity resolves `program.organization` lazily (cheap, and bounded by the
number of in-scope opportunities). Admin emails are fetched in a single grouped
query (step 2). No per-report email queries.

### Component 2: Email templates

Each template iterates over the `reports` list, so the same template serves the
one-report and many-report cases.

`commcare_connect/templates/audit/email/new_audit_report_notification.txt`:

```
{% extends "email/base.txt" %}
{% block content %}Hi,

New audit reports have been generated for {{ organization.name }}:
{% for report in reports %}
{{ report.opportunity_name }} ({{ report.period_start }} to {{ report.period_end }})
{{ report.url }}
{% endfor %}{% endblock %}
```

`commcare_connect/templates/audit/email/new_audit_report_notification.html`:

```
{% extends "email/base.html" %}
{% block content %}
  <p>Hi,</p>
  <p>New audit reports have been generated for <strong>{{ organization.name }}</strong>:</p>
  <ul>
    {% for report in reports %}
      <li>
        <a href="{{ report.url }}">{{ report.opportunity_name }}</a>
        ({{ report.period_start }} to {{ report.period_end }})
      </li>
    {% endfor %}
  </ul>
{% endblock %}
```

### Component 3: Wire into the task

In `generate_audit_reports`, collect the generated reports, then send the
batched notifications once after the loop:

```python
generated_reports = []
for opportunity in opportunities:
    try:
        report = generate_audit_report_for_opportunity(
            opportunity,
            period_start=period_start,
            period_end=period_end,
        )
    except Exception:
        logger.exception("Failed to generate weekly report for opportunity %s", opportunity.pk)
        continue
    generated_reports.append(report)

try:
    send_new_audit_report_notifications(generated_reports)
except Exception:
    logger.exception("Failed to send audit report notifications")
```

## Link accessibility (no new work required)

The report link uses the PM org slug. The audit detail view
(`audit_report_detail`) is guarded by `org_program_manager_required`, which
requires the requester be an **admin** of the `program_manager` org named by
`org_slug`. Recipients are exactly those admins, so every recipient can open
the link. This is the same access path PMs already use to reach audit reports
in the UI; no view or permission changes are needed.

## Testing

Unit tests for `send_new_audit_report_notifications` (and one task-level test),
in `commcare_connect/audit/tests/`:

1. **Single report for a PM org → one email.** Assert one message is sent,
   recipients are exactly the PM-org admins, the subject contains the
   opportunity name, and the body contains the report URL and the period.
2. **Multiple reports for the same PM org → one email listing all of them.**
   Assert a single message (not one per report) is sent to the org's admins,
   the count-based subject is used, and the body lists every opportunity name,
   period, and URL.
3. **Reports across two different PM orgs → one email per org**, each listing
   only that org's reports, addressed only to that org's admins.
4. **Recipient filtering.** PM-org members and viewers are **excluded**; only
   admins receive the email.
5. **Report whose `opportunity.program is None` → skipped** (no email for it;
   does not affect other orgs' emails).
6. **PM org with no admins (or blank emails) → no email for that org.**
7. **Per-org resilience.** A failure while sending one org's email does not
   prevent other orgs' emails from being sent.
8. **Task resilience.** A failure in `send_new_audit_report_notifications` does
   not propagate out of `generate_audit_reports` (generation still succeeds).

Test mechanics: email is sent via `send_mail_async.delay`; under the test
settings' eager Celery configuration this runs synchronously and populates
`django.core.mail.outbox`. If eager execution is not configured, assert on a
mock of `send_mail_async.delay` instead. Confirm which during implementation.

## Out of scope

- No email on report **completion** (only on creation).
- No summary statistics (flagged counts, etc.) in the email.
- No changes to who can view audit reports (view permissions unchanged).
- No new opportunity-level notification preferences / opt-out.
