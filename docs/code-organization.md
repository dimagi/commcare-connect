# Code organization

Where code goes. Most of this doc covers code that isn't a view, model, form, table or template. [Thin layers](#thin-layers) covers those, and the last sections cover models, JavaScript and tests.

## Existing code

This doc describes how code should look. Most existing code was written before these rules and doesn't look like this yet, so the examples name where code should live, not always where it lives today. Follow the doc, not the current location.

All code your branch adds or changes follows these rules, even when the code around it doesn't. Check it against this doc, not against nearby code. When you touch existing code that breaks them and the fix is small, refactor it in the same PR, in its own `refactor:` commit. Leave larger refactors for a separate PR.

When no rule fits, follow the closest one, say which in the PR description, and add the case to this doc in the same PR.

## Terms

- **Caller:** a module, not a function. Test modules don't count.
- **Reused:** used by two or more modules. Tests don't count.
- **Topic:** the business noun the code is about, such as a model or workflow: `invoices`, `payments`, `assignments`, `work_areas`. Not a page (`dashboard`) and not a kind of code (`aggregates`, `helpers`).
- **Pure:** makes no manager, queryset or related-manager calls, doesn't iterate a queryset, and calls no external system. Reading attributes of model instances it was given counts as pure; the caller loads relations with `select_related`. Reading the clock or generating random values is allowed.
- **Change state / write:** see step 2 under [Roles](#roles). Reading and filling a cache for a cached read is part of the read, so it goes in `queries`.
- **Connect concept:** code that names opportunities, visits, payment units, invoices, work areas, programs and the like. Code without one would make sense in any Django project.
- **Small** (a refactor done in the same PR): one `refactor:` commit with no behaviour change, touching only files the PR already changes plus their tests, under about 150 changed lines. Anything bigger is a separate PR.
- **Plain data type:** a dataclass, NamedTuple or TypedDict that holds values. Methods that compute from its own fields are fine (`Money.__add__`). Anything that queries is not a plain data type.

## Roles

Each app splits this code by what it does:

| Package          | Holds                                                                            |
| ---------------- | -------------------------------------------------------------------------------- |
| `services`       | Operations that change state: billing, approvals, HQ sync, emails                |
| `queries`        | Read-only code: lookups, report and table data, calculations that run queries    |
| `permissions`    | Access levels and visibility                                                     |
| `exports`        | Files people download (CSV/XLSX, PDFs) and the template context those files need |
| `utils`          | Pure functions that name a Connect concept, e.g. `generate_invoice_number`       |
| `dataclasses.py` | Plain data types, e.g. `Money`, `LineItem`                                       |
| `exceptions.py`  | Exception classes that other modules catch, e.g. `TaskAlreadyAssignedError`      |

To decide where a function goes, ask these questions in order. The first yes decides. If different parts of a function get different answers, split the function.

1. **Does Django, DRF, Celery or another library call it for us?** It is a thin layer (see [Thin layers](#thin-layers)).
2. **Does it change state?** That means writing rows (other than a model saving its own row), writing files to storage, calling an external system, sending a message, or dispatching a task. It goes in `services`. A check that guards the change lives in the same services module, and the service calls it.
3. **Does it decide whether a user may see or do something?** It goes in `permissions`.
4. **Does it turn data into a file people download?** It goes in `exports`. It returns the file's content. Saving it to storage is the caller's job.
5. **Does it read the database?** That means calling a manager, a queryset method or a related manager, or iterating a queryset. It goes in `queries`. A filter or annotation on one model's rows that two modules use, or that mirrors a model property, is a method on that model's `QuerySet` instead.
6. **Is it worked out from one instance's own fields, as a value the domain uses** (a status, a derived date, an allowed transition)? It is a model method or property.
7. **Otherwise it is pure, so it is a utility.**
   - While its one caller is not a thin layer, it stays private in that caller's module. When a second module needs it, or its only caller is a thin layer, it moves to a `utils` package.
   - That is the app's `utils/` if it names a Connect concept, or `commcare_connect/utils/<topic>.py` if it doesn't.

Which role may call which: services may call permissions, queries, exports and utils. Permissions and exports may call queries and utils. Queries may call `QuerySet` methods and utils. Only thin layers and other services call services.

An app's `utils` is pure. `commcare_connect/utils/` is different: it holds code that names no Connect concept and may use the database (see [Code shared between apps](#code-shared-between-apps)).

Start a role as one module (`queries.py`) while the app covers one topic. When it passes about 300 lines, turn it into a package with one module per topic (`services/billing.py`, `services/approvals.py`). Decide this per role in each app. `opportunity` already covers many topics, so its roles start as packages.

`helpers.py` goes away. Don't add new ones, and move code out of the existing ones into its role module. The same applies to any module that mixes roles, such as `opportunity/visit_import.py`: split it by role when you change it. Decide the role of each public function. Each private helper goes with the public function it serves, whatever the helper does, and moves out only when another module needs it.

Clients for external systems stay in the app that owns the integration, as their own module: `commcarehq/`, `ocs_provider/`, `connect_id_client/`.

## Placement

### Services, queries, permissions and exports

These go in their role module or package even when only one place calls them. A reader should be able to guess where one lives from what it does. For example, `rollback_invoice_line_items` is only called from one view, but it reverses an invoice's billing, so it belongs in `opportunity/services/`.

Two exceptions keep small code next to its caller:

- A single-object lookup used in one role module, such as a `.get()` that raises a domain error when the row is missing, stays a private function in that module. Thin layers run simple reads themselves (see [Thin layers](#thin-layers)).
- A helper that only serves functions in one role module stays private in that module, even when it writes.

A module of classes that each calculate a value by running queries, such as `audit/chc_indicators.py`, is a `queries` module.

A request body for an external system is a private function of the service that sends it. The format belongs to the integration, not to the model, so it isn't a model method.

### Utilities

A pure calculation worked out from one model instance's fields, such as a derived date or status, is a method or property on that model.

Any other utility with one caller stays as a private function in the caller's module, unless that caller is a thin layer. When a second module needs it, or its only caller is a thin layer, move it to `utils/`:

- The app's `utils/` package if it names a Connect concept, e.g. `generate_invoice_number`. Other apps import it from there.
- `commcare_connect/utils/<topic>.py` if it names no Connect concept.

### Classes

Classes follow the same placement as functions. Classify a class by what its public methods do, using the questions under [Roles](#roles), and split it if they get different answers.

- A value type whose methods compute only from its own fields goes in `dataclasses.py`, e.g. `Money`.
- A class used by one module stays in that module.

### Exceptions

- Raised and caught in one module: stays in that module.
- Caught in another module: the app's `exceptions.py`.

### Constants

- Constants and enums used by more than one module go in the app's `const.py`.
- Constants used by one module stay at the top of that module.
- `TextChoices` used for a model field, and constants a model needs, stay in `models.py`. `const.py` may import its own app's models, so models can't import from it.

### Checks

A check that guards a change lives next to that change in `services`, and the service calls it (see step 2 under [Roles](#roles)).

- Checks raise `django.core.exceptions.ValidationError`. When the error is about one input, pass a dict keyed by the form field name. Forms call the check in `clean()`, and serializers call it in `validate()`. The service calls the same check again before it writes.
- Forms and serializers check the shape of the input themselves: required fields, types, ranges, and consistency between fields such as start ≤ end. Checks that need the database, or other objects' state, are service checks called from `clean()` or `validate()`.

### Code shared between apps

Apps are ordered by what their models import. Each app's `models.py` imports only apps to its left:

`utils` → `commcarehq` → `users` → `organization` → `opportunity` → `program`, `microplanning`, `audit` → `solicitation`, `flags`

Client apps that import no app (`connect_id_client`, `jira_service_desk`, `commcarehq_provider`, `ocs_provider`) sit at the left with `utils`. Entry-point apps (`form_receiver`, `data_export`, `reports`, `multidb`, `web`, `prelogin`, `deid`) sit at the right.

- Code lives in the app whose feature it serves, unless it uses models from an app further right. Then it moves right, to the first app that is at or past all of them. Other apps import it from there.
- Models and role modules import other apps only from their left. Thin layers may import any app. Existing code breaks this in many places. Don't add new imports that point right.
- String foreign keys and imports inside a class body or function may point right, such as `Opportunity.program`. Don't add new ones without a reason.
- Code that names no Connect concept and imports no app from this repo goes in `commcare_connect/utils/<topic>.py`. Code that names a Connect concept goes in the app the concept belongs to, even when it imports no app; other apps import it from there. `commcare_connect/utils/` may import Django and third-party packages, including their models. It must not import this repo's apps, because five apps' models import `utils/db.py` and an app import there causes circular imports. The one exception is a module that holds only constants, such as `flags/switch_names.py`, when there's no other way.
- An app doesn't write another app's models directly, whether from a view, form or task. It calls a service in the app that owns the model. Reading another app's models is fine anywhere a read is allowed.
- An operation that spans apps, such as merging two organizations, is a service in the app that starts it. That service calls the other apps' services for their models.

### QuerySet methods and ORM expressions

A function that takes one model's queryset and returns it filtered or annotated is a method on a custom `QuerySet` once two modules use it. While one module uses it, it stays private in that module. Expose the `QuerySet` with `QuerySet.as_manager()` or `Manager.from_queryset()`, e.g. `Opportunity.objects.accessible_to(org)`. Put these methods on the `QuerySet`, not only the `Manager`, so they can be chained after `.filter()`. `queries` holds the larger builders that call them.

- A function that builds an expression for another query (`Subquery`, `Case`, `Q` with lookups) goes in `queries`. A `Q` or `Case` that refers to no model, only field names and values, is pure, so it may be a utility.
- Near-identical builders, such as a family of `Subquery` functions, stay together in one module, placed where the most-shared one would go.
- An SQL version of a model property is a `QuerySet` method next to the property, with a test that checks the two agree.
- A filter that limits rows to what a user may see is a `QuerySet` method. `permissions` calls it when it needs the check.
- A filter that depends on another app's models goes in the `queries` of the app the app order picks (see [Code shared between apps](#code-shared-between-apps)). For example, users who hold a work area go in `microplanning/queries.py`, not on `User`.

### Celery tasks

Services dispatch the background work they need with `transaction.on_commit(partial(task.delay, ...))`, so every caller gets it. Import the task inside the function, because `tasks.py` imports services at module level. A task calls one service and handles only Celery concerns.

### Notifications

This covers email, SMS and push.

- The service of the app whose event it is picks the recipients and builds the message.
- The service queues the send after commit (see [Celery tasks](#celery-tasks)). Email goes through `utils/tasks.send_mail_async`, SMS through `utils/sms.send_sms`, and push through `connect_id_client.main.send_message`.
- Rendering an email's text and HTML templates is generic code, in `commcare_connect/utils/email.py`.
- App tasks don't build messages.

### Template context

- Used by one view: stays in the view.
- Used by a template rendered from several places: goes with that template's rendering, e.g. `exports/invoices.py` for the invoice PDF.
- Built the same way by many views, such as breadcrumb trails: a function in the `utils` of the app that owns the objects it names, e.g. `opportunity/utils/breadcrumbs.py`. Other apps import it from there.
- A display value worked out from one object: a model property or a template filter.
- Needed on every page: `web/context_processors.py`.
- JSON for our own pages, such as GeoJSON or htmx JSON, follows the same rules: the view builds it, its reads come from `queries`, and pure shaping that a second module needs goes in the app's `utils/`.

Exports don't use the ORM directly. The caller gets the data from `queries` and passes it in. When the caller can't easily pass everything in, such as the context for each PDF in a batch, the export calls a `queries` function. A context builder may call `queries` too, but doesn't write its own queries.

### File imports

- Reading an uploaded file into rows is a utility. It takes bytes or a file object and returns rows, or raises a parse error defined in `exceptions.py`.
- Checking the rows against the database and saving them is a service, in `services/<topic>_import.py`.
- The view or task only opens the file and calls the service.

## Thin layers

Views, forms, tables, filter sets, serializers, admin classes, view decorators, Celery tasks and management commands are thin layers.

### No business logic

Thin layers take input, call services, queries, permissions and exports, and return output. They don't contain the logic themselves. They keep only code that works with the request, the form, the task's arguments or the response. Anything else moves out, even when the thin layer is its only caller: a pure function goes to the app's `utils/`, and code that reads the database goes to `queries`, except for simple reads. That keeps the logic testable without a request, a worker or a command line, and reusable by all of them.

- Thin layers may run simple reads themselves: `get_object_or_404`, or a `.get()`, `.filter()`, `.exists()` or `.count()` on one model using values from the request, or a related manager such as `organization.flag_set`. Reads that use `annotate`, `aggregate`, `Subquery`, `Prefetch` or raw SQL, or that repeat a filter written elsewhere, go in `queries`.
- When two thin layers hold copies of the same logic that differ, the logic still moves out. If the difference isn't intended, merge the copies into one service. If it is, write two service functions that share private helpers, with no behaviour flags.
- Tasks only handle Celery concerns: retries, progress reporting, file storage and failures.
- Management commands only handle arguments and output. Code shared between commands goes in the app's role modules, not in another command module.
- A filter set or form may build choice querysets, using `QuerySet` methods.
- A view decorator that checks access calls a function in `permissions`.

### Shared base classes, mixins and fields

Base classes, mixins, columns and field factories go with their kind: form code in `forms.py`, table code in `tables.py`, and so on.

- Used in one module: stays in that module.
- Used by another app and depends on the owning app's models, e.g. a currency field: stays in the owning app's module. Other apps import it from there.
- Generic: `commcare_connect/utils/<topic>.py`, where a framework layer can be the topic (`forms.py`, `tables.py`, `email.py`). `utils/forms.py` and `utils/tables.py` already exist. Check them before writing a new column or field, e.g. `select_column`.

## Models

A model method reads or changes only its own model's fields. Models don't import `services` or `queries`. When an operation also has to call another app, an external system or a task, write a service that calls the model method and then the other steps. That way, saving a model in a test, migration or admin action has no hidden effects. `AssignedTask.assign` and `AssignedTask.mark_completed` in `opportunity/models.py` predate this rule.

Abstract models and model mixins follow the same placement as functions. One app uses them: that app's `models.py`. Several apps use them and they import no app: `commcare_connect/utils/db.py`, next to `BaseModel`.

## Migrations

Data migrations don't import production code. Keep the logic in the migration and use `apps.get_model`. The only exception is generic code in `commcare_connect/utils/` that uses no models, such as `batched`.

Before you move or delete a function, grep `*/migrations/*.py` for imports of it. Copy it into each migration that imports it, in the same commit.

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
