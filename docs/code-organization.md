# Code organization

Where code goes when it isn't a view, model, form, table or template. The reasoning behind these rules is in [refactor-notes/utility-module-layout.md](refactor-notes/utility-module-layout.md).

## Existing code

Most existing code was written before these rules. Follow them in new code. When your change touches existing code that breaks them:

- If fixing it is a small refactor, do it in its own `refactor:` commit before your change. Keep it small: move one function or module, not a whole app.
- Otherwise leave the existing code alone. Another PR can move it.

Don't move code that nobody is working on.

These rules don't cover every case yet. When they don't, follow the closest rule and say so in the PR description, so this doc can be updated.

## Roles

Each app splits this code by what it does:

| Module           | Holds                                                                                                 | Test                                                                    |
| ---------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `services`       | Operations that change state or enforce business rules: billing, approvals, HQ sync, Jira submissions | Does it write to the database or an external system, or enforce a rule? |
| `queries`        | Read-only builders that combine queries into table or report data                                     | Does it only read and shape data?                                       |
| `permissions`    | Access levels and visibility                                                                          | Does it decide who may see or do something?                             |
| `exports`        | Files people download (CSV/XLSX, PDFs) and the template context those files need                      | Does it turn data it was given into a file?                             |
| `utils`          | Small helpers that neither read nor write data                                                        | Does it work on the values it was given only?                           |
| `dataclasses.py` | Plain data types, e.g. `Money`, `LineItem`                                                            | Is it a plain data container?                                           |
| `exceptions.py`  | Exception classes that other modules catch, e.g. `TaskAlreadyAssignedError`                           | Is it caught outside the module that raises it?                         |

Start each role as a single module (`services.py`). Turn it into a package (`services/billing.py`, `services/approvals.py`) once it holds several unrelated topics.

Don't add new `helpers.py` modules.

Clients for external systems stay in the app that owns the integration, as their own module: `commcarehq/`, `ocs_provider/`, `connect_id_client/`.

## Placement

### Services, queries, permissions and exports

These go in their role module even when only one place calls them. A reader should be able to guess where one lives from what it does. For example, `rollback_invoice_line_items` is only called from one view, but it reverses an invoice's billing, so it belongs in `opportunity/services/`.

### Utilities

Where a utility goes depends on who calls it:

- One caller: a private function in the caller's module.
- Callers in several modules of the same app: the app's `utils`.
- Callers in several apps: `commcare_connect/utils/<topic>.py`, as long as it imports no app (see below).

### Exceptions

- Raised and caught in one module: stays in that module.
- Caught in another module: the app's `exceptions.py`.

### Code shared between apps

- Code that imports no app goes in `commcare_connect/utils/<topic>.py`. That package must never import an app: five apps' models import `utils/db.py`, so an app import there causes circular imports.
- Code that depends on an app's models stays in that app. Other apps import it from there.
- An app doesn't write another app's models directly, whether from a view, form or task. It calls a service in the app that owns the model.

### QuerySet filters and annotations

Reusable filters and annotations are methods on a custom `QuerySet`, exposed with `QuerySet.as_manager()` or `Manager.from_queryset()`, e.g. `Opportunity.objects.accessible_to(org)`. Put them on the `QuerySet`, not only the `Manager`, so they can be chained after `.filter()`. `queries` holds the larger builders that call them.

### Template context

- Used by one view: stays in the view.
- Used by a template rendered from several places: goes with that template's rendering, e.g. `exports/invoices.py` for the invoice PDF.
- A display value worked out from one object: a model property or a template filter.
- Needed on every page: `web/context_processors.py`.

Exports and context builders don't query or write themselves. The caller gets the data from `queries` and passes it in.

## Views and tasks

Views and Celery tasks call services, queries and exports; they don't contain the logic themselves. That keeps the logic testable without a request or a worker, and reusable by both.

Tasks only handle Celery concerns: retries, progress reporting, file storage and failures.

## Migrations

Data migrations don't import production code. Keep the logic in the migration and use `apps.get_model`. The only exception is generic code in `commcare_connect/utils/` that uses no models, such as `batched`.
