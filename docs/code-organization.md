# Code organization

Where code goes. Most of this doc covers code that isn't a view, model, form, table or template. [Thin layers](#thin-layers) covers those, and the last sections cover models, JavaScript and tests.

## Existing code

This doc describes how code should look. Most existing code was written before these rules and doesn't look like this yet, so the examples name where code should live, not always where it lives today. Follow the doc, not the current location.

Follow the rules in new code. When you touch existing code that breaks them and the fix is small, refactor it in the same PR, in its own `refactor:` commit. Leave larger refactors for a separate PR.

When no rule fits, follow the closest one, say which in the PR description, and add the case to this doc in the same PR.

## Roles

Each app splits this code by what it does:

| Package          | Holds                                                                                                 | Test                                                                    |
| ---------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `services`       | Operations that change state or enforce business rules: billing, approvals, HQ sync, emails           | Does it write to the database or an external system, or enforce a rule? |
| `queries`        | Read-only code: lookups, report and table data, calculations that run queries                         | Does it only read and shape data?                                       |
| `permissions`    | Access levels and visibility                                                                          | Does it decide who may see or do something?                             |
| `exports`        | Files people download (CSV/XLSX, PDFs) and the template context those files need                      | Does it turn data it was given into a file?                             |
| `utils`          | Pure functions with domain knowledge that neither read nor write data, e.g. `generate_invoice_number` | Does it work only on the values it was given?                           |
| `dataclasses.py` | Plain data types, e.g. `Money`, `LineItem`                                                            | Is it a plain data container?                                           |
| `exceptions.py`  | Exception classes that other modules catch, e.g. `TaskAlreadyAssignedError`                           | Is it caught outside the module that raises it?                         |

An app's `utils` is pure. `commcare_connect/utils/` is different: it holds generic code that would work in another project and may use the database (see [Code shared between apps](#code-shared-between-apps)).

Start a role as one module (`queries.py`) while the app covers one topic. Turn it into a package with one module per topic (`services/billing.py`, `services/approvals.py`) when a second, unrelated topic arrives. `opportunity` already covers many topics, so its roles start as packages.

`helpers.py` goes away. Don't add new ones, and move code out of the existing ones into its role module. The same applies to any module that mixes roles, such as `opportunity/visit_import.py`: split it by role when you change it.

Clients for external systems stay in the app that owns the integration, as their own module: `commcarehq/`, `ocs_provider/`, `connect_id_client/`.

## Placement

### Services, queries, permissions and exports

These go in their role module or package even when only one place calls them. A reader should be able to guess where one lives from what it does. For example, `rollback_invoice_line_items` is only called from one view, but it reverses an invoice's billing, so it belongs in `opportunity/services/`.

Two exceptions keep small code next to its caller:

- A single-object lookup used in one module, such as a `.get()` that raises a domain error when the row is missing, stays a private function in that module.
- A helper that only serves functions in one role module stays private in that module, even when it writes.

A module of classes that each calculate a value by running queries, such as `audit/chc_indicators.py`, is a `queries` module.

### Utilities

A pure calculation worked out from one model instance's fields, such as a derived date or status, is a method or property on that model.

Any other utility with one caller stays as a private function in the caller's module. When a second module needs it, move it to `utils/`:

- The app's `utils/` package if it has domain knowledge, e.g. `generate_invoice_number`. Other apps import it from there.
- `commcare_connect/utils/<topic>.py` if it would work in another project.

Functions that build ORM expressions without running them (`Subquery`, `Coalesce`, annotations) count as utilities.

### Exceptions

- Raised and caught in one module: stays in that module.
- Caught in another module: the app's `exceptions.py`.

### Code shared between apps

- Code that imports no app from this repo goes in `commcare_connect/utils/<topic>.py`. It may import Django and third-party packages, including their models. It must not import this repo's apps, because five apps' models import `utils/db.py` and an app import there causes circular imports. The one exception is a module that holds only constants, such as `flags/switch_names.py`, when there's no other way.
- Code that depends on an app's models stays in that app. Other apps import it from there.
- An app doesn't write another app's models directly, whether from a view, form or task. It calls a service in the app that owns the model.
- An operation that spans apps, such as merging two organizations, is a service in the app that starts it. That service calls the other apps' services for their models.

### QuerySet filters and annotations

Reusable filters and annotations are methods on a custom `QuerySet`, exposed with `QuerySet.as_manager()` or `Manager.from_queryset()`, e.g. `Opportunity.objects.accessible_to(org)`. Put them on the `QuerySet`, not only the `Manager`, so they can be chained after `.filter()`. `queries` holds the larger builders that call them.

- A filter that limits rows to what a user may see is a `QuerySet` method. `permissions` calls it when it needs the check.
- A filter that depends on another app's models goes in the `queries` of the app that owns that relation. For example, users who hold a work area go in `microplanning/queries.py`, not on `User`.

### Emails

Rendering an email's templates and sending it is a service. The generic render-and-send function goes in `commcare_connect/utils/email.py`. The function that picks the recipients and builds the context goes in the app's `services`. Celery tasks only queue the send.

### Template context

- Used by one view: stays in the view.
- Used by a template rendered from several places: goes with that template's rendering, e.g. `exports/invoices.py` for the invoice PDF.
- Built the same way by many views, such as breadcrumb trails: a function in the `utils` of the app that owns the objects it names, e.g. `opportunity/utils/breadcrumbs.py`. Other apps import it from there.
- A display value worked out from one object: a model property or a template filter.
- Needed on every page: `web/context_processors.py`.

Exports don't query or write. The caller gets the data from `queries` and passes it in. A context builder may call `queries` too, but doesn't write its own queries.

## Thin layers

Views, forms, tables, filter sets, serializers, admin classes, view decorators, Celery tasks and management commands are thin layers.

### No business logic

Thin layers take input, call services, queries, permissions and exports, and return output. They don't contain the logic themselves. That keeps the logic testable without a request, a worker or a command line, and reusable by all of them.

- Tasks only handle Celery concerns: retries, progress reporting, file storage and failures.
- Management commands only handle arguments and output. Code shared between commands goes in the app's role modules, not in another command module.
- A filter set or form may build choice querysets, using `QuerySet` methods.
- A view decorator that checks access calls a function in `permissions`.

### Shared base classes, mixins and fields

Base classes, mixins, columns and field factories go with their kind: form code in `forms.py`, table code in `tables.py`, and so on.

- Used in one module: stays in that module.
- Used by another app and depends on the owning app's models, e.g. a currency field: stays in the owning app's module. Other apps import it from there.
- Generic: `commcare_connect/utils/<kind>.py`. `utils/forms.py` and `utils/tables.py` already exist. Check them before writing a new column or field, e.g. `select_column`.

## Models

A model method reads or changes only its own model's fields. Models don't import `services` or `queries`. When an operation also has to call another app, an external system or a task, write a service that calls the model method and then the other steps. That way, saving a model in a test, migration or admin action has no hidden effects. `AssignedTask.assign` and `AssignedTask.mark_completed` in `opportunity/models.py` predate this rule.

Abstract models and model mixins follow the same placement as functions. One app uses them: that app's `models.py`. Several apps use them and they import no app: `commcare_connect/utils/db.py`, next to `BaseModel`.

## Migrations

Data migrations don't import production code. Keep the logic in the migration and use `apps.get_model`. The only exception is generic code in `commcare_connect/utils/` that uses no models, such as `batched`.

## JavaScript

JavaScript lives in `.js` files, not in templates.

- A helper used in one file stays private in that file.
- When a second file needs it, move it to its own module in `static/js/` and import it. Only webpack entries (`webpack/base.config.js`) can import, so a script loaded directly with `{% static %}`, such as `static/prelogin/app.js`, has to become an entry first.

## Tests

`helpers.py` goes away only in production code. Tests use these homes:

- Setup used by one test module: a fixture in that module.
- Setup used by several modules of one app: a fixture in the app's `tests/conftest.py`.
- Setup used by several apps: a fixture in `commcare_connect/conftest.py`.
- Builder functions that aren't fixtures, e.g. a function that builds form JSON: the app's `tests/helpers.py`.

Test modules don't import from other test modules. Move the shared code to a `conftest.py` or `tests/helpers.py`.

Factories create consistent related objects themselves, e.g. a `UserVisitFactory` given `opportunity_access` takes its `user` and `opportunity` from it with `factory.SelfAttribute`. Tests don't pass values that equal a factory's defaults.

Use `pytest.mark.parametrize` when tests differ only in inputs and expected values. Keep separate tests when each case needs its own setup or explanation.
