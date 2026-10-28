# Code organization

Where code goes when it isn't a view, model, form, table or template.

## Existing code

Most existing code was written before these rules. Follow them in new code. When your change touches existing code that breaks them, move that code as part of your change. Don't move code that nobody is working on.

These rules don't cover every case yet. When they don't, follow the closest rule and say so in the PR description, so this doc can be updated.

## Roles

Each app splits this code by what it does:

| Package          | Holds                                                                                                 | Test                                                                    |
| ---------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `services`       | Operations that change state or enforce business rules: billing, approvals, HQ sync, Jira submissions | Does it write to the database or an external system, or enforce a rule? |
| `queries`        | Read-only builders that combine queries into table or report data                                     | Does it only read and shape data?                                       |
| `permissions`    | Access levels and visibility                                                                          | Does it decide who may see or do something?                             |
| `exports`        | Files people download (CSV/XLSX, PDFs) and the template context those files need                      | Does it turn data it was given into a file?                             |
| `utils`          | Pure functions that neither read nor write data, including domain calculations                        | Does it work only on the values it was given?                           |
| `dataclasses.py` | Plain data types, e.g. `Money`, `LineItem`                                                            | Is it a plain data container?                                           |
| `exceptions.py`  | Exception classes that other modules catch, e.g. `TaskAlreadyAssignedError`                           | Is it caught outside the module that raises it?                         |

Each role is a package with one module per topic inside, e.g. `services/billing.py` and `services/approvals.py`.

`helpers.py` goes away. Don't add new ones, and move code out of the existing ones into its role package.

Clients for external systems stay in the app that owns the integration, as their own module: `commcarehq/`, `ocs_provider/`, `connect_id_client/`.

## Placement

### Services, queries, permissions and exports

These go in their role module even when only one place calls them. A reader should be able to guess where one lives from what it does. For example, `rollback_invoice_line_items` is only called from one view, but it reverses an invoice's billing, so it belongs in `opportunity/services/`.

### Utilities

A pure calculation worked out from one model instance's fields, such as a derived date or status, is a method or property on that model.

Any other utility with one caller stays as a private function in the caller's module. When a second module needs it, move it to `utils/`:

- The app's `utils/` package if it has domain knowledge, e.g. `generate_invoice_number`. Other apps import it from there.
- `commcare_connect/utils/<topic>.py` if it would work in another project.

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

Exports don't query or write. The caller gets the data from `queries` and passes it in. A context builder may call `queries` too, but doesn't write its own queries.

## Views and tasks

Views and Celery tasks call services, queries and exports; they don't contain the logic themselves. That keeps the logic testable without a request or a worker, and reusable by both.

Tasks only handle Celery concerns: retries, progress reporting, file storage and failures.

## Migrations

Data migrations don't import production code. Keep the logic in the migration and use `apps.get_model`. The only exception is generic code in `commcare_connect/utils/` that uses no models, such as `batched`.
