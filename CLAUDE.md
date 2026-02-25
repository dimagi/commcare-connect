# CommCare Connect

Django + GeoDjango backend with Webpack/Tailwind/Alpine.js/HTMX frontend. Connects mobile health workers (via CommCare HQ) to opportunities and payments.

## Commands

### Setup

```bash
inv up                          # Start Docker services (Postgres/PostGIS + Redis)
pip install -r requirements/dev.txt
npm ci
npm run dev                     # Build JS/CSS once
./manage.py migrate
./manage.py createsuperuser
./manage.py runserver
```

### Daily dev

```bash
npm run dev-watch               # Watch and rebuild assets
celery -A config.celery_app worker -l info  # Async tasks (usually not needed locally)
```

### Tests

```bash
pytest                          # All tests (--reuse-db by default)
pytest commcare_connect/opportunity/tests/test_models.py::TestClass::test_method
coverage run -m pytest && coverage html
```

### Lint / format

```bash
pre-commit run -a               # Black, isort, flake8, prettier (run before committing)
```

### Dependencies

```bash
inv requirements                # Recompile requirements without upgrading
inv requirements --upgrade-package celery  # Upgrade specific package
```

### Translations

```bash
inv translations
```

## Architecture

```
commcare_connect/
├── opportunity/        # Core domain: opportunities, claims, payments
├── organization/       # Orgs, memberships, roles (admin/member/viewer)
├── program/            # Program management
├── users/              # Custom User model (AUTH_USER_MODEL = "users.User")
├── reports/            # Analytics and reporting
├── commcarehq/         # CommCare HQ integration
├── connect_id_client/  # ConnectID OAuth client
├── form_receiver/      # Incoming form submissions
├── flags/              # Feature flags (django-waffle)
├── multidb/            # Multi-database router (read replicas)
├── web/                # Web templates and views
└── static/js/          # Webpack entry points → static/bundles/
config/
├── settings/base.py    # Core settings
├── settings/local.py   # Dev overrides (DEBUG=True, eager Celery)
├── settings/test.py    # Test overrides (fast password hasher)
└── celery_app.py
```

## Environment Variables

Copy `.env_template` → `.env`. Key vars:

```
DATABASE_URL=postgres://postgres:postgres@localhost:5432/commcare_connect
CELERY_BROKER_URL=redis://localhost:6379/0
REDIS_URL=redis://localhost:6379/0
COMMCARE_HQ_URL=https://www.commcarehq.org
MAPBOX_TOKEN=
```

## Testing Patterns

- **Framework**: pytest with `--ds=config.settings.test --reuse-db`
- **Factories**: factory_boy — `UserFactory`, `OrgWithUsersFactory`, `OpportunityFactory`, etc. in each app's `tests/factories.py`
- **Global fixtures**: `commcare_connect/conftest.py` — `organization`, `opportunity`, `mobile_user`, `api_client`, `api_rf`
- **External HTTP**: mock with `pytest-httpx`

## Gotchas

**GeoDjango**: Requires system libraries (GDAL, GEOS, Proj). On macOS set explicit paths in `.env`:

```bash
GDAL_LIBRARY_PATH=$(brew --prefix gdal)/lib/libgdal.dylib
GEOS_LIBRARY_PATH=$(brew --prefix geos)/lib/libgeos_c.dylib
```

**Celery in dev**: Tasks run eagerly (synchronously) by default — `CELERY_TASK_ALWAYS_EAGER = True` in `local.py`. No worker needed unless testing async behavior.

**Multi-database**: `SECONDARY_DATABASE_URL` enables read replicas via logical replication. Use `./manage.py migrate_multi` when this is configured.

**Static files**: Run `npm run build` or `npm run dev-watch` before testing UI. Output goes to `commcare_connect/static/bundles/`. Templates load bundles via `{% load render_bundle %}`.

**Organization middleware**: Sets `request.organization` from session. Querysets in views are automatically scoped to the current org — always check this when writing new views.

**API versioning**: Accept header versioning — `Accept: application/json; version=2.0`. Current versions: 1.0 (legacy), 2.0 (current).

**Feature flags**: `django-waffle` — check via `waffle.flag_is_active(request, "flag_name")` or `{% flag "flag_name" %}` in templates.

**PDF generation**: WeasyPrint — requires extra system deps. Mac: `brew install weasyprint`.

**pre-commit**: Install once with `pre-commit install`. Hooks cover Black, isort, flake8, prettier, django-upgrade. Excludes `migrations/` and `docs/`.

## Key Files

| File                        | Purpose                                           |
| --------------------------- | ------------------------------------------------- |
| `tasks.py`                  | Invoke tasks (`inv up`, `inv requirements`, etc.) |
| `config/api_router.py`      | DRF API URL routing                               |
| `commcare_connect/cache.py` | `quickcache` utility                              |
| `webpack/base.config.js`    | Webpack entry points and bundle config            |
| `.pre-commit-config.yaml`   | All code quality hooks                            |
| `deploy/README.md`          | Deployment with Kamal + Ansible                   |

## CI/CD

- **CI** (`.github/workflows/ci.yml`): runs pre-commit + pytest on PRs to `main`
- **Deploy** (`.github/workflows/deploy.yml`): manual workflow dispatch → Kamal to staging or production
- Docker multi-stage build: Python wheels → Node build → final runtime image
