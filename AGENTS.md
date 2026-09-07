# CommCare Connect

Django 5.2 + PostGIS monolith for managing community health worker opportunities, payments, and workflows. Integrates with CommCare HQ and ConnectID services.

## Commands

```bash
# Local services (PostgreSQL/PostGIS + Redis)
inv up                              # docker compose up
inv down                            # docker compose down

# Django
./manage.py migrate                 # run migrations (dev)
./manage.py migrate_multi           # run migrations on both primary + secondary DB (prod)
./manage.py runserver               # dev server

# JavaScript/CSS
npm ci                              # install deps
inv build-js                        # dev build
inv build-js -w                     # dev build with watch
inv build-js --prod                 # production build
npm run build                       # production build (what CI runs)

# Celery (local dev)
celery -A config.celery_app worker -B -l info

# Tests
pytest                              # run all tests
pytest path/to/test_file.py::test_name  # run single test

# Linting (ruff, ruff-format, pyupgrade, django-upgrade, prettier, djlint, eslint)
prek run -a

# Requirements (uv)
uv sync
uv add <pkg>

# Translations
inv translations
```

## Architecture

- **Monolith**: Django serves both HTML templates (Tailwind + Alpine.js + htmx) and a DRF REST API
- **URL pattern**: Most views scoped under `/a/<org_slug>/` via `OrganizationMiddleware`
- **API versioning**: `AcceptHeaderVersioning` with versions `1.0` and `2.0`
- **Background tasks**: Celery with Redis broker; beat scheduler uses DB
- **Feature flags**: django-waffle with a custom `Flag` model. Flag names in `flags/flag_names.py`, switch names in `flags/switch_names.py` — see Terminology below
- **Audit trail**: django-pghistory stores `username` + `user_email` in context (survives user deletion)
- **Database**: PostgreSQL + PostGIS. `ATOMIC_REQUESTS = True` (all requests are transactions)
- **Deployment**: Kamal (Docker-based) + Ansible on EC2. See `deploy/README.md`

### Key directories

```
commcare_connect/
  opportunity/     # Core domain: opportunities, visits, payments (largest app)
  organization/    # Org management, membership roles
  program/         # Program management, linking orgs/opportunities
  users/           # Custom User model, ConnectID links
  audit/           # Audit reports: CHC indicators, sampling, review workflow
  commcarehq/      # CommCare HQ server integration
  connect_id_client/  # HTTP client for ConnectID service
  form_receiver/   # Receives xforms from CommCare HQ
  commcarehq_provider/ # OAuth provider: HQ as an identity provider
  ocs_provider/    # Open Chat Studio integration
  data_export/     # CSV/streaming exports over OAuth token scopes
  microplanning/   # Maps, catchment areas (Mapbox)
  reports/         # KPI and admin reports
  flags/           # Waffle feature flag/switch name constants
  multidb/         # Secondary DB support + logical replication
  deid/            # SQL generator for de-identifying a DB copy; only in INSTALLED_APPS under local.py
  web/             # Context processors, shared template tags
  prelogin/        # Public marketing pages
  utils/           # BaseModel, middleware, caching, permissions
config/
  settings/        # base.py, local.py, test.py, staging.py, production.py
  api_router.py    # DRF API URL routing
  celery_app.py    # Celery config
  urls.py          # Root URL config
```

## Terminology

Some words mean several unrelated things here. Work out which one you are in before searching.

**"flag"** — four separate concepts:

- **Waffle flags**: per-request/per-user feature toggles. Names in `flags/flag_names.py`, checked with `flag_is_active(request, NAME)`, backed by the custom `Flag` model in `flags/models.py`.
- **Waffle switches**: global on/off toggles. Names in `flags/switch_names.py`, checked with `switch_is_active(NAME)`.
- **Visit validation flags**: why a `UserVisit` was flagged for review (`duplicate`, `gps`, `catchment`, …). Defined in `utils/flags.py` (`Flags`, `FlagDescription`, `FlagLabels`), stored on `UserVisit.flagged` and `UserVisit.flag_reason`.
- **Verification flag config**: which validation flags an opportunity applies — `OpportunityVerificationFlags` and `DeliverUnitFlagRules` in `opportunity/models.py`.

**"worker"** — there is no `Worker` model. A worker is a `User` reached through an `OpportunityAccess`, and `WorkerPageView`, the `Worker*Table` classes and the worker templates all resolve to that pair. Nothing on `User` marks someone as a worker: org staff have a `UserOrganizationMembership`, workers have an `OpportunityAccess`. Worker-scoped URLs pass `?user=<User.user_id>` — the UUID, not the PK.

## Code Style

- **Python**: ruff for linting, formatting, and import sorting (line length 119, target py311)
- **JS/CSS**: prettier (tab-width 2, single-quote)
- **Templates**: djlint owns them, not prettier — `djlint-reformat-django` will rewrite your formatting on commit. `templates/prelogin/home.html` is excluded from both djlint hooks
- **prek hooks enforce all of the above** (reading `.pre-commit-config.yaml`) plus pyupgrade (--py311-plus), django-upgrade (--target-version 4.1), djlint (templates) and eslint (`commcare_connect/static/**/*.js`)
- Django models should extend `BaseModel` from `commcare_connect/utils/db.py` (provides `created_by`, `modified_by`, `date_created`, `date_modified`)
- Custom `User` model uses single `name` field instead of `first_name`/`last_name`
- **Single Responsibility**: Functions should do one thing (or a few closely related things). If a function is doing too much, split it
- **Newspaper metaphor**: Order functions top-down — high-level/public functions at the top, helpers/details below
- **Prefer class-based views** for complex business logic, form handling, and views that switch on request method. Use function views only for simple cases. Note that most existing views are function views (`opportunity/views.py` is roughly 2:1 function to class) — they are legacy, not the pattern to copy
- **Use Django Forms** over raw HTML forms for validation and rendering
- **No inline HTML in Python**: Keep templates in `.html` files, not in Python strings
- **Keep JS in JS files**: Don't inline JavaScript in templates; use separate `.js` files
- **Alpine.js for in-page interactivity**, **htmx for dynamic data loading** from the server
- **Use predefined style classes** for elements instead of raw Tailwind utility classes. The vocabulary lives in `tailwind/tailwind.css` (`button`, `button-outline-rounded`, `badge`, `card_bg`, `title`, `status-active`, …); `grep -E '^\s*\.[a-z][a-zA-Z0-9_-]*\s*\{' tailwind/tailwind.css` lists all 76 of them. Plenty of templates still use raw utilities; that's legacy

## Robustness

- **Guard against missing environmental dependencies**: Code must not hard-fail because the environment isn't fully set up. This covers settings/env values (`settings.MAPBOX_TOKEN`, Twilio creds, API keys), DB records that act as configuration (`SocialApp`, `Site`, `Currency`/`Country`, waffle `Flag`s), and unreachable external services (HQ, ConnectID, OCS)
- **Required vs optional**: if only one feature needs it, degrade that feature alone — hide or disable the UI and say why (see `configured_provider` in `commcare_connect/users/templatetags/socialaccount_extras.py` and `commcare_connect/templates/ocs/_connect_prompt.html`). If the app genuinely can't work without it, fail loudly and early in the code path that needs it rather than half-working.
- **Never render broken UI**: don't emit a button, link or map that 500s or dead-ends when the config is absent
- **Set an explicit timeout on outbound calls**: all HTTP goes through **httpx**, which defaults to 5s — fine for quick calls, too short for bulk work, so pass a timeout sized to the operation (see `_make_request` in `commcare_connect/connect_id_client/main.py`: `timeout=10` default, 15–30s for bulk sends; `commcare_connect/ocs_provider/views.py`)
- **Dispatch Celery tasks after commit**: `ATOMIC_REQUESTS = True` means a bare `task.delay()` in a request can run before the transaction commits, so the worker may not see the rows it was told about. Wrap the dispatch: `transaction.on_commit(partial(task.delay, obj.pk))` (see `commcare_connect/form_receiver/processor.py`). Most views still call `.delay()` directly — don't copy that.
- **Celery tasks: skip vs retry**: missing configuration won't fix itself — log and return (`commcare_connect/audit/tasks.py`). A transient failure (unreachable service, lock contention) should retry with `autoretry_for` + `retry_backoff` (`commcare_connect/opportunity/tasks.py`)
- **Log, don't spam**: use the module `logger` (`logger.exception` / `logger.error`). Log at error level only when the config is _expected_ in that environment; things that are legitimately absent in dev belong at `info` (e.g. `audit/tasks.py`)
- **Test the unconfigured path**: add a test that runs with the setting unset or the record missing — an optional feature still renders/runs, a required one fails early with a clear error

## Testing

- **Framework**: pytest + pytest-django + factory-boy
- **Test location**: `commcare_connect/<app>/tests/` with `factories.py`, `test_*.py`
- **Global fixtures** in `commcare_connect/conftest.py`: `organization`, `user`, `opportunity`, `mobile_user`, `user_with_connectid_link`, `mobile_user_with_connect_link`, `paymentunit_options`, `org_user_member`, `org_user_admin`, `program_manager_org`, `managed_opportunity`, `program_manager_org_user_member`, `program_manager_org_user_admin`, `api_rf`, `api_client`
- **autouse fixtures**: `media_storage` (redirects to tmpdir), `ensure_currency_country_data` (repopulates Currency/Country flushed between tests)
- HTTP mocking: `pytest-httpx` for httpx calls
- **Prefer fixtures over factories** to avoid duplication. Check `conftest.py` files (global and per-app) for existing fixtures before creating new factory instances
- **Use `pytest.mark.parametrize`** instead of writing multiple near-identical tests
- **Test functions, not view responses**: When testing views, extract and test the underlying business logic functions rather than making HTTP requests. Extract helper functions from views if needed to make them testable

## Gotchas

- **PostGIS required everywhere** (including tests). Local dev needs `gdal`, `geos`, `proj` system libs. On macOS, set `GDAL_LIBRARY_PATH` and `GEOS_LIBRARY_PATH` in `.env`
- **`.env` leaks into test settings**: `base.py` calls `env.read_env(BASE_DIR / ".env")`, so local `.env` values also apply under `config.settings.test`. An _empty_ value overrides a code default with `""` rather than falling back — `CONNECTID_URL=` breaks the suite. CI has no `.env`, so these failures are local-only
- **`--reuse-db` + Currency/Country data**: These models get flushed between tests. The `ensure_currency_country_data` autouse fixture handles this — don't remove it
- **Secondary DB is opt-in**: `DATABASE_ROUTERS` is only installed when `SECONDARY_DATABASE_URL` is set (`config/settings/base.py:52-56`). Without it `SECONDARY_DB_ALIAS` is `None`, routing is off and `migrate_multi` just migrates `default` — so multidb routing behaviour can't be reproduced locally by default
- **API UUID transition**: The `API_UUID` waffle switch controls whether API endpoints accept integer PKs or UUIDs. Use `get_object_or_list_by_uuid_or_int()` from `utils/db.py` for API lookups. The migration is unfinished — `program/api/views.py`, `opportunity/api/views/automation.py` and `.../task_completion.py` still look up by `pk` directly, so don't take a neighbouring view as the pattern
- **CSRF via sessions**: `CSRF_USE_SESSIONS = True`. Templates use `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` on `<body>` for htmx
- **Webpack output**: Bundles are built to `commcare_connect/static/bundles/` and referenced with plain `{% static 'bundles/...' %}`, served via `STATICFILES_DIRS`. `webpack-stats.json` is written but unused — django-webpack-loader is not installed
- **CI uses**: `postgis/postgis:15-3.5` image, Python 3.11, requires `gdal-bin libproj-dev` apt packages

## Further reading

- `pr_guidelines.md` — PR size, description and review conventions
- `docs/dependency-management.md` — adding/upgrading dependencies
- `deploy/README.md` — Kamal + Ansible deployment
