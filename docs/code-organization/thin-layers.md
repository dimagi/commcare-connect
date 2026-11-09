# Thin layers

Read [`../code-organization.md`](../code-organization.md) first.

Thin layers are views, forms, tables, filter sets, serializers, admin classes, decorators, Celery tasks, management commands, middleware, signal receivers, and anything else a framework calls. They take input, call the role modules and return output. They keep only code that works with the request, the form, the task's arguments or the response. Everything else moves out, even with one caller, so it can be tested without a request, a worker or a command line.

- Thin layers may run simple reads: `get_object_or_404`, or `.get()`, `.filter()`, `.exists()` or `.count()` on one model with values from the request, or a related manager. Reads that use `annotate`, `aggregate`, `Subquery`, `Prefetch` or raw SQL, or that repeat a filter written elsewhere, go in `queries`.
- Differing copies of the same logic still move out: into one service if the difference isn't intended, otherwise into two services that share private helpers, without behaviour flags.
- When a view and its service would share a name, the service gets the full name (`accept_invite` and `accept_organization_invite`).
- Tasks handle only Celery concerns: retries, progress reporting, file storage and failures.
- Management commands handle only arguments and output. Logic shared between commands goes in role modules, plumbing (base classes, prompts, output helpers) in `<app>/management/base.py`, and connections to other databases or systems in a client module.
- Forms and filter sets may build choice querysets with `QuerySet` methods. Validation is in [`services.md`](services.md#checks).
- Access guards (view decorators, DRF permission classes) live in the app's `decorators.py` and call `permissions`.
- Signal receivers live in `<app>/signals.py`, connected in `AppConfig.ready()`. For our own models, call a service instead of sending a signal.
- Middleware placement is in [`cross-app.md`](cross-app.md).
- The entry point (middleware, view, task or command) sets `pghistory.context(...)`, not services.

## Template context and page JSON

- Used by one view: stays in the view.
- Used by a template rendered from several places: goes with that template's rendering, e.g. `exports/invoices.py` for the invoice PDF.
- Built the same way by many views, such as breadcrumbs: the `utils` of the app that owns the objects it names, e.g. `opportunity/utils/breadcrumbs.py`.
- A display value from one object: a model property or a template filter.
- Needed on every page: `web/context_processors.py`.
- JSON for our own pages (GeoJSON, htmx) follows the same rules.

Context builders and exports get their data from `queries` instead of using the ORM themselves. Usually the caller passes the data in. When it can't, such as for per-PDF context in a batch, the export calls `queries`.

## Shared base classes, mixins and fields

Base classes, mixins, columns and field factories go in the module for their kind (`forms.py`, `tables.py`). They stay in the module that uses them, go in the owning app when they depend on its models (e.g. a currency field), and go in `commcare_connect/utils/` (`forms.py`, `tables.py`) when generic. Check those first, e.g. for `select_column`.
