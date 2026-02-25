# CommCare Connect

Django 4.2 + PostGIS monolith for managing community health worker opportunities, payments, and workflows. Integrates with CommCare HQ and ConnectID services.

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

# Celery (local dev)
celery -A config.celery_app worker -B -l info

# Tests
pytest                              # run all tests
pytest path/to/test_file.py::test_name  # run single test

# Linting (runs black, isort, flake8, pyupgrade, django-upgrade, prettier)
pre-commit run -a

# Requirements (pip-tools)
inv requirements                    # recompile .txt from .in files
inv requirements --upgrade-package <pkg>

# Translations
inv translations
```

## Architecture

- **Monolith**: Django serves both HTML templates (Tailwind + Alpine.js + htmx) and a DRF REST API
- **URL pattern**: Most views scoped under `/a/<org_slug>/` via `OrganizationMiddleware`
- **API versioning**: `AcceptHeaderVersioning` with versions `1.0` and `2.0`
- **Background tasks**: Celery with Redis broker; beat scheduler uses DB
- **Feature flags**: django-waffle with custom `Flag` model; constants in `commcare_connect/flags/switch_names.py`
- **Audit trail**: django-pghistory stores `username` + `user_email` in context (survives user deletion)
- **Database**: PostgreSQL + PostGIS. `ATOMIC_REQUESTS = True` (all requests are transactions)
- **Deployment**: Kamal (Docker-based) + Ansible. NOT Elastic Beanstalk despite README mention

### Key directories

```
commcare_connect/
  opportunity/     # Core domain: opportunities, visits, payments (largest app)
  organization/    # Org management, membership roles
  program/         # Program management, linking orgs/opportunities
  users/           # Custom User model, ConnectID links
  commcarehq/      # CommCare HQ server integration
  connect_id_client/  # HTTP client for ConnectID service
  form_receiver/   # Receives xforms from CommCare HQ
  microplanning/   # Maps, catchment areas (Mapbox)
  reports/         # KPI and admin reports
  flags/           # Waffle feature flag/switch name constants
  multidb/         # Secondary DB support + logical replication
  utils/           # BaseModel, middleware, caching, permissions
config/
  settings/        # base.py, local.py, test.py, staging.py, production.py
  api_router.py    # DRF API URL routing
  celery_app.py    # Celery config
  urls.py          # Root URL config
```

## Code Style

- **Python**: black + isort (line length 119, target py311). flake8 for linting
- **JS/CSS**: prettier (tab-width 2, single-quote). Templates excluded from prettier
- **Pre-commit hooks enforce all of the above** plus pyupgrade (--py311-plus) and django-upgrade (--target-version 4.1)
- Models should extend `BaseModel` from `commcare_connect/utils/db.py` (provides `created_by`, `modified_by`, `date_created`, `date_modified`)
- Custom `User` model uses single `name` field instead of `first_name`/`last_name`

## Testing

- **Framework**: pytest + pytest-django + factory-boy
- **Config**: `--ds=config.settings.test --reuse-db` (in pyproject.toml)
- **Test location**: `commcare_connect/<app>/tests/` with `factories.py`, `test_*.py`
- **Global fixtures** in `commcare_connect/conftest.py`: `organization`, `user`, `opportunity`, `mobile_user`, `mobile_user_with_connect_link`, `org_user_member`, `org_user_admin`, `api_rf`, `api_client`
- **autouse fixtures**: `media_storage` (redirects to tmpdir), `ensure_currency_country_data` (repopulates Currency/Country flushed between tests)
- HTTP mocking: `pytest-httpx` for httpx calls

## Gotchas

- **PostGIS required everywhere** (including tests). Local dev needs `gdal`, `geos`, `proj` system libs. On macOS, set `GDAL_LIBRARY_PATH` and `GEOS_LIBRARY_PATH` in `.env`
- **`--reuse-db` + Currency/Country data**: These models get flushed between tests. The `ensure_currency_country_data` autouse fixture handles this — don't remove it
- **API UUID transition**: The `API_UUID` waffle switch controls whether API endpoints accept integer PKs or UUIDs. Use `get_object_or_list_by_uuid_or_int()` from `utils/db.py` for API lookups
- **CSRF via sessions**: `CSRF_USE_SESSIONS = True`. Templates use `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'` on `<body>` for htmx
- **Webpack bundle tracker**: Frontend builds write `webpack-stats.json`. Templates reference bundles from `staticfiles/bundles/`
- **CI uses**: `postgis/postgis:15-3.5` image, Python 3.11, requires `gdal-bin libproj-dev` apt packages
