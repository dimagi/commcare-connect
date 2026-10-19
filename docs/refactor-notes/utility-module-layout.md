# Where shared logic should live

An investigation into how this codebase organises logic outside views and models, and a proposal for where it should go. Checked against commit `3d3417e4`.

## 1. Why I looked

I ran [Treepeat](treepeat-findings.md) over the production code to find duplicated logic. Merging a duplicate means putting the shared version somewhere, and the codebase gives no clear answer to where:

- Some apps have `helpers.py` (`opportunity`, `program`, `microplanning`, `reports`, `users`).
- Some have `utils.py` or a `utils/` package (`program`, `opportunity`), and there's a shared `commcare_connect/utils/`.
- No doc says what goes in which. `AGENTS.md` only says to prefer class-based views for complex logic, and to extract functions from views so they can be tested.

So before merging duplicates, I looked at what these modules actually contain.

## 2. What I found

### The names don't describe the contents

Reading every function, I sorted them into four roles:

- **service**: writes to the database, changes state in an external system, or enforces a business rule
- **selector**: read-only; builds querysets, table data or report data
- **permission**: works out access levels or visibility
- **util**: pure function with no domain knowledge

Most modules mix several roles, whatever their name:

| Module | Mostly | Also contains |
|---|---|---|
| `opportunity/helpers.py` | selectors | a generic util (`TieredQueryset`) |
| `program/helpers.py` | selectors | two functions only tests call |
| `microplanning/helpers.py` | selectors | services (exclude, assign and unassign work areas, with HQ sync), utils (`pct`, `ratio`) |
| `reports/helpers.py` | selectors | date utils |
| `users/helpers.py` | HQ API client and service | a request-to-org resolver used by middleware |
| `program/utils.py` | permissions | lookups, cache invalidation, a migration-only backfill |
| `opportunity/utils/` | services (billing, completed work) and selectors | utils, PDF and Jira payload builders |

Most write logic isn't in any of these. It's in views, forms and tasks; for example, visit approval is in `opportunity/views.py:1218`.

### Shared domain code stays in its app

When several apps need domain code, they already import it from the app that owns it:

- `program/utils.py` (permissions) is used by `data_export`, `microplanning`, `opportunity`, `organization` and `users`.
- `opportunity/visit_import.update_payment_accrued_for_user` is used by `form_receiver`.
- `users/helpers.create_hq_user_and_link` is used by `opportunity`.

### The shared `utils/` package isn't app-agnostic

Three modules in `commcare_connect/utils/` are domain or integration code:

- `utils/flags.py` holds the visit validation flags and imports `opportunity.models` at the top of the file. `opportunity/models.py` imports it back inside the `UserVisit.flags` property, which avoids a circular import at load time. The shared package depends on an app, and that app depends on it.
- `utils/commcarehq_api.py` is an HQ client, used by 15 modules. A `commcarehq` app already exists.
- `utils/ocs_api.py` is an Open Chat Studio client. An `ocs_provider` app already exists.

### Migrations depend on production code

Old migrations should keep working however the code changes. Some import production functions instead:

- `opportunity/0092` and `program/0004` import `populate_currency_and_country_fk_for_model` from `program/utils.py`. It's a finished one-off backfill sitting in a permissions module.
- `opportunity/0059` and `0067` import `get_exchange_rate` from `opportunity/visit_import.py`. It uses the live `ExchangeRate` model and, when no rate is stored, calls the openexchangerates API, so running these migrations can make network calls.
- `opportunity/0040` and `0045` import live enums from `opportunity/models.py`.
- Three migrations import `batched` from `utils/itertools.py`. That's harmless: it's generic and has no model dependencies.

## 3. Proposal

### One package per role

Each app splits its non-view, non-model code by role, with topic modules inside each package:

| Package | Holds | Test |
|---|---|---|
| `services/` | Operations that change state or enforce business rules: billing, approvals, HQ sync, Jira submissions | Does it write (to the database or an external system), or enforce a rule? |
| `selectors/` | Read-only builders that combine queries into table or report data | Does it only read and shape data? |
| `exports/` | Files people download: CSV/XLSX, PDFs, and the template context those documents need | Does it turn data it was given into a file? |
| `utils/` | Pure functions with no domain knowledge | Would it work in another project? |
| `dataclasses.py` | Domain data types, e.g. `Money`, `LineItem` | Is it a plain data container? |

`helpers.py` goes away. "Services" and "selectors" come from the [HackSoft Django Styleguide](https://github.com/HackSoftware/Django-Styleguide). I chose `exports/` over a broad `services/` so that "service" keeps meaning "changes state", and because the codebase already uses the word (`opportunity/export.py`, the `data_export` app).

A service is defined by what it does, not by who calls it. Most services are only used inside their own app.

### Queryset filters and annotations go on the model

Reusable filters and annotations become methods on a custom `QuerySet`, exposed with `QuerySet.as_manager()` or `Manager.from_queryset()`, e.g. `Opportunity.objects.accessible_to(org)`. They go on the `QuerySet`, not only the `Manager`, so they can be chained after `.filter()`. `selectors/` keeps the larger, view-specific builders that call them, such as `get_payment_report_data`.

### Shared code goes where its dependencies point

- Code that imports no app goes in `commcare_connect/utils/<topic>.py`.
- Code that depends on an app's models or concepts stays in that app. Other apps import it from there.

This matches what the codebase already does. Moving domain code into `commcare_connect/utils/` would make that package import app models, and five apps' models import `utils/db.py`, which invites circular imports. So the three domain modules move out:

- `utils/flags.py` → `opportunity/`
- `utils/commcarehq_api.py` → `commcarehq/`
- `utils/ocs_api.py` → `ocs_provider/`

### Exports and context builders receive their data

Exports don't query or write. A task or view calls a selector and passes the result to the export. A context builder may call a selector, but doesn't write its own queries.

Template context goes where its users are:

- Used by one view: stays in the view.
- Used for a template rendered in several places: goes with that template's rendering. `get_invoice_pdf_context` feeds the invoice template in two places, the `download_invoice` view (`opportunity/views.py:2160`) and `render_invoice_pdf` for the PDF zip, so it goes in `exports/invoices.py`.
- A display value worked out from one object: a model property or a template filter.
- Needed on every page: `web/context_processors.py`.

### Tasks only handle Celery concerns

Tasks handle retries, progress reporting, file storage and failures. The work itself lives in services, selectors and exports, so it can be tested without Celery and reused by views. For example, the finance request submission moves out of the `submit_invoice_finance_request` task (`opportunity/tasks.py:280`) into `opportunity/services/finance_request.py`, together with its Jira payload builders.

### Migrations don't import production code

A data migration keeps its own logic and uses `apps.get_model`. The only exception is generic, model-free code in `commcare_connect/utils/`, like `batched`.

## 4. What this looks like

### `opportunity/utils/` split by role

- **QuerySet methods:** `get_billable_completed_works_qs`, `billable_works_qs`, `filter_invoices_by_month`
- **`services/`:** `bill_invoice`, `rollback_invoice_line_items`, `CompletedWorkUpdater`, `update_status`, `update_work_payment_date`, `InvoiceWorkflow`, `finance_request.py` (the Jira submission and its payload builders)
- **`selectors/`:** `get_billable_line_items`, `get_invoice_line_items`, `get_invoice_service_summary`, `get_invoice_delivery_rows_for_export`, `get_billable_delivery_rows_for_export`, `get_exportable_invoices`, `get_start_date_for_invoice`, `get_invoice_month_options`, `get_previous_finance_request`, `suggest_new_vendor`, `group_line_items`
- **`exports/invoices.py`:** `render_invoice_pdf`, `get_invoice_pdf_context`, `invoice_pdf_filename`, `build_invoice_pdf_zip`, `build_invoice_summary_dataset`
- **`utils/`:** `split_month_options`, `with_invoice_month`, `generate_invoice_number`, `get_end_date_for_invoice`
- **`commcare_connect/utils/`:** `_neutralize_formula`
- **`dataclasses.py`:** `Money`, `LineItem`, `WorkPayRow`, `ServiceSummaryLine`

### Exports

```
opportunity/exports/
  visits.py      UserVisitExporter, export_user_visit_review_data       (from opportunity/export.py)
  workers.py     export_user_status_table, export_deliver_status_table,
                 export_work_status_table, export_empty_payment_table     (from opportunity/export.py)
  invoices.py    render_invoice_pdf, get_invoice_pdf_context, invoice_pdf_filename,
                 build_invoice_pdf_zip, build_invoice_summary_dataset    (from opportunity/utils/invoice_export.py)
  datasets.py    get_dataset, append_row_data

audit/exports.py stream_audit_report_csv and its formatting helpers      (from audit/services.py)
```

The queries inside these functions move to selectors.

## 5. First application: the Treepeat duplicates

The duplicates Treepeat marked as worth refactoring were merged using these rules:

| Duplicate | Shared code now lives in | Why there |
|---|---|---|
| OCS and HQ OAuth adapters | `commcare_connect/utils/oauth_adapters.py` (`ProfileOAuth2Adapter`) | Imports no app |
| Three "delivery" push notifications (`opportunity` and `microplanning` tasks) | `opportunity/services/notifications.py` (`send_delivery_notification`) | Changes external state; depends on `OpportunityAccess` |
| `Opportunity.currency_code` and `Program.currency_code` | `opportunity/models.py` (`CurrencyCodeMixin`) | Depends on `Currency`, which `opportunity` owns |
| `CompletedWork` and `UserVisit` `__setattr__` | `commcare_connect/utils/db.py` (`StatusModifiedDateMixin`) | The mixin imports no app |
| Program invite emails, area importers, form helpers, coverage tables, tile views, `data_export` lookups, organization merge | A private helper or base class in the same module | Used by one module only |
| Dashboard pie charts | A shared `createPieChart` in `static/js/dashboard.js` | Same file |

Two groups were left as they are:

- **`DeliveryType` and `PrimarySector`** have the same three fields by coincidence. They're unrelated concepts in different apps, and a shared base would couple them for no gain.
- **The three pghistory event admins** already share `AuditEventAdmin`. What's left differs in every field list.

## 6. Recommendation

Don't restructure the codebase in one go. Instead:

1. **Handle the Treepeat duplicates.** Land the merges in section 5.
2. **Add these rules to `AGENTS.md`**, so new code follows them. Also reword "Prefer class-based views for complex business logic" so it doesn't point logic at views.
3. **Refactor the rest as we go.** When a change touches code that breaks these rules, move that code as part of the change. Don't move code nobody is working on.

### Known cleanups

These are the issues this investigation found. Do each one when you are next working in that area:

- Remove migration dependencies on production code: inline the currency backfill into `opportunity/0092` and `program/0004`, inline a database-only rate lookup into `opportunity/0059` and `0067`, and replace the enum imports in `opportunity/0040` and `0045` with literal values.
- Delete the functions in `program/helpers.py` that no production code reaches (`get_annotated_managed_opportunity` and `get_delivery_performance_report`, which only tests call, and `calculate_safe_percentage`, which only those two call) and their tests.
- Move `pct` and `ratio` to `commcare_connect/utils/`, and check each caller before pointing `safe_percent` (`opportunity/views.py`) and `_percent` (`audit/chc_indicators.py`) at them. They handle a zero denominator differently.
- Move `utils/flags.py`, `utils/commcarehq_api.py` and `utils/ocs_api.py` into their apps.
- Split `opportunity/utils/` by role as described in section 4, including the exports and the finance request service.
