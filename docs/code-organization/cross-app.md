# Code shared between apps

Read [`../code-organization.md`](../code-organization.md) first.

- Code goes in the app whose feature it serves. When it uses models from several apps, it goes in the app whose models import the others' (`opportunity`, not `organization`), so imports don't loop.
- Models and role modules don't import an app whose models import theirs, because that causes circular imports. Thin layers may import any app. String foreign keys and imports inside a function get around this (`Opportunity.program`), but don't add new ones without a reason.
- `commcare_connect/utils/` holds code that names no Connect concept. It may use the database and import Django and third-party packages, but no app from this repo: five apps' models import `utils/db.py`, so an app import there causes circular imports. The exception is a constants-only module such as `flags/switch_names.py`.
- An app writes another app's models only through that app's services, from any layer. Reading them is fine. An operation that spans apps, such as merging organizations, is a service in the app that starts it, and it calls the other apps' services.
- A filter on another app's models goes in the `queries` of the app whose models import that app's, e.g. users who hold a work area go in `microplanning/queries.py`.
- Middleware goes in the app whose feature it serves, by the first rule above. Generic middleware goes in `commcare_connect/utils/middleware.py`.
