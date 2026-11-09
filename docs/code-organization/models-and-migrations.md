# Models and migrations

Read [`../code-organization.md`](../code-organization.md) first.

## Models

A model method reads or changes only its own model's fields, and models don't import `services` or `queries`. When an operation also involves another app, an external system or a task, a service calls the model method and then does the rest, so saving a model in a test, migration or admin action has no hidden effects. Abstract models and mixins go in the app's `models.py`, or in `commcare_connect/utils/db.py` when several apps use them and they import no app.

## Migrations

Data migrations use `apps.get_model` and don't import production code, except model-free code in `commcare_connect/utils/` such as `batched`. Before you move or delete a function, grep `*/migrations/*.py` for imports of it and copy it into each of those migrations, in the same commit.
