# Code organization

Most existing code predates these rules. All code your branch adds or changes follows them, even when nearby code doesn't.

## Terms

- **Connect concept:** opportunities, visits, payment units, invoices, work areas, programs and the like. Code without one would make sense in any Django project.
- **Topic:** what the code is about: a business noun (`invoices`, `work_areas`), a page (`dashboard`) or a kind of framework code (`forms`, `tables`). Not a catch-all (`helpers`, `misc`, `common`).
- **Service:** an app's public way to change its data, in the app's `services`. Other apps call it to write that app's models.
- **Utility:** shared code used inside one app, in the app's `utils/`, or generic code in `commcare_connect/utils/`. It may write its own app's models, but other apps don't call it to do so.

## Writing another app's models

An app writes another app's models only through that app's services, from any layer. Reading them is fine. An operation that spans apps, such as merging organizations, is a service in the app that starts it, and it calls the other apps' services.

## Utilities

Utilities may read or write the database and call external systems.

- A utility with one caller stays in that module and is private: its name starts with `_`, e.g. `_ratio`.
- When a second module needs it, drop the `_` and move it: it goes in the app's `utils/` if it names a Connect concept, e.g. `generate_invoice_number`. Otherwise it goes in `commcare_connect/utils/<topic>.py`.
- `commcare_connect/utils/` may use the database and import Django and third-party packages, but no app from this repo: several apps' models import `utils/db.py`, so an app import there causes circular imports. The exception is a constants-only module such as `flags/switch_names.py`.
- Don't add `helpers.py` files, and move code out of the existing ones into `utils` when you change it.
- Test code follows the same rules. A builder or assertion used by one test module is private there. When a second test module in the app needs it, drop the `_` and move it to the app's `tests/utils.py`. Fixtures still go in `conftest.py`.

## Large modules

A views module that grows too large, such as `opportunity/views.py`, can become a package with one module per topic, e.g. `opportunity/views/invoices.py`.

## Migrations

Data migrations use `apps.get_model` and don't import production code, except model-free code in `commcare_connect/utils/` such as `batched`.
