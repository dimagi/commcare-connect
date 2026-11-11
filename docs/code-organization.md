# Code organization

Where code goes. Read this file first, then the topic files your change needs.

| Topic file                                                               | Read it when your change touches                                                  |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| [`cross-app.md`](code-organization/cross-app.md)                         | code that uses or writes more than one app's models, or `commcare_connect/utils/` |
| [`queries.md`](code-organization/queries.md)                             | database reads, `QuerySet` methods, ORM expressions or caches                     |
| [`services.md`](code-organization/services.md)                           | writes, validation checks, Celery tasks, notifications or file imports            |
| [`thin-layers.md`](code-organization/thin-layers.md)                     | views, forms, tables, serializers, admin, commands, templates, exports            |
| [`models-and-migrations.md`](code-organization/models-and-migrations.md) | models or migrations                                                              |
| [`tests.md`](code-organization/tests.md)                                 | tests                                                                             |
| [`javascript.md`](code-organization/javascript.md)                       | JavaScript                                                                        |

Most existing code predates these rules, so examples name where code should live, not always where it is today. All code your branch adds or changes follows these rules, even when nearby code doesn't. When you touch code that breaks them, ask the user whether to refactor it. If they agree and the fix is small, do it in its own `refactor:` commit; larger fixes get a separate PR. When no rule fits, follow the closest one, say which in the PR description, and add the case to these docs.

## Terms

- **Caller:** a module, not a function. Tests don't count.
- **Topic:** the business noun the code is about (`invoices`, `payments`, `work_areas`), not a page (`dashboard`) or a kind of code (`helpers`).
- **Pure:** makes no manager, queryset or related-manager calls, doesn't iterate a queryset, and calls no external system. Reading attributes of instances it was given is fine (the caller uses `select_related`), as are the clock and random values.
- **Connect concept:** opportunities, visits, payment units, invoices, work areas, programs and the like. Code without one would make sense in any Django project.
- **Small:** one `refactor:` commit with no behaviour change, in files the PR already touches plus their tests, under about 150 changed lines.
- **Plain data type:** a dataclass, NamedTuple or TypedDict. Methods that compute from its own fields are fine; anything that queries isn't one.

## Roles

| Package          | Holds                                                                            |
| ---------------- | -------------------------------------------------------------------------------- |
| `services`       | Operations that change state: billing, approvals, HQ sync, emails                |
| `queries`        | Read-only code: lookups, report and table data, calculations that run queries    |
| `permissions`    | Access levels and visibility                                                     |
| `exports`        | Files people download (CSV/XLSX, PDFs) and the template context those files need |
| `utils`          | Pure functions that name a Connect concept, e.g. `generate_invoice_number`       |
| `dataclasses.py` | Plain data types, e.g. `Money`, `LineItem`                                       |
| `exceptions.py`  | Exception classes that other modules catch, e.g. `TaskAlreadyAssignedError`      |

To place a function, ask these questions in order. The first yes decides. Split a function whose parts get different answers.

1. **Does Django, DRF, Celery or another library call it?** It is a [thin layer](code-organization/thin-layers.md).
2. **Does it change state?** That means writing rows (other than a model saving its own row), writing files to storage, calling an external system, sending a message or dispatching a task. It goes in `services`, with the [checks](code-organization/services.md#checks) that guard the change. Reading and filling a cache is part of a read.
3. **Does it decide whether a user may see or do something?** `permissions`.
4. **Does it turn data into a file people download?** `exports`. It returns the content; the caller stores it.
5. **Does it read the database?** That means calling a manager, a queryset method or a related manager, or iterating a queryset. It goes in `queries`, or is a [`QuerySet` method](code-organization/queries.md).
6. **Is it a domain value worked out from one instance's own fields** (a status, a derived date, an allowed transition)? A model method or property.
7. **Otherwise it is a utility.** It stays private in its one caller, unless that caller is a thin layer. When a second module needs it, or its caller is a thin layer, it goes in the app's `utils/` if it names a Connect concept, otherwise in `commcare_connect/utils/<topic>.py`.

Services may call permissions, queries, exports and utils. Permissions and exports may call queries and utils. Queries may call `QuerySet` methods and utils. Only thin layers and other services call services.

Start each role as one module (`queries.py`). Past about 300 lines, make it a package with one module per topic (`services/billing.py`). `opportunity`'s roles start as packages.

Don't add `helpers.py` files, and move code out of the existing ones. Split a module that mixes roles, such as `opportunity/visit_import.py`, when you change it: decide each public function's role, and keep each private helper with the public function it serves.

Clients for external systems are their own module in the app that owns the integration: `commcarehq/`, `ocs_provider/`, `connect_id_client/`.

## Placement

Services, queries, permissions and exports go in their role module even with one caller, so readers can find them by what they do. For example, `rollback_invoice_line_items` is called from one view but reverses billing, so it goes in `opportunity/services/`. Two exceptions stay private in their module:

- a single-object lookup used in one role module, such as a `.get()` that raises a domain error
- a helper that serves only one role module, even when it writes

A module of classes that each calculate a value by querying, such as `audit/chc_indicators.py`, is a `queries` module.

- A class is placed like a function, by what its public methods do. Split it if they get different answers. Plain data types go in `dataclasses.py`.
- An exception caught outside the module that raises it goes in the app's `exceptions.py`. Otherwise it stays in that module.
- Constants and enums used by several modules go in the app's `const.py`. Those used by one module stay at its top. `TextChoices` for model fields, and constants a model needs, stay in `models.py`, because `const.py` may import models.
