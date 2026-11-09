# Code shared between apps

Read [`../code-organization.md`](../code-organization.md) first.

Apps are ordered by their models' imports. Each `models.py` imports only apps to its left:

`utils` → `commcarehq` → `users` → `organization` → `opportunity` → `program`, `microplanning`, `audit` → `solicitation`, `flags`

Client apps that import no app (`connect_id_client`, `jira_service_desk`, `commcarehq_provider`, `ocs_provider`) sit at the left with `utils`. Entry-point apps (`form_receiver`, `data_export`, `reports`, `multidb`, `web`, `prelogin`, `deid`) sit at the right.

- Code goes in the app whose feature it serves, unless it uses models from an app further right. Then it goes in the first app at or past all of them.
- Models and role modules import only apps to their left. Thin layers may import any app. String foreign keys and imports inside a function may point right (`Opportunity.program`), but don't add new ones without a reason.
- `commcare_connect/utils/` holds code that names no Connect concept. It may use the database and import Django and third-party packages, but no app from this repo: five apps' models import `utils/db.py`, so an app import there causes circular imports. The exception is a constants-only module such as `flags/switch_names.py`.
- An app writes another app's models only through that app's services, from any layer. Reading them is fine. An operation that spans apps, such as merging organizations, is a service in the app that starts it, and it calls the other apps' services.
- A filter on another app's models goes in the `queries` of the app the order picks, e.g. users who hold a work area go in `microplanning/queries.py`.
- Middleware goes in the app the order picks. Generic middleware goes in `commcare_connect/utils/middleware.py`.
