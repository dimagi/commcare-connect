# AGENTS.md

This file provides guidance to code agents when working with code in this repository.

## Commands

### Running Tests

```bash
pytest                                          # all tests
pytest commcare_connect/opportunity/            # single app
pytest commcare_connect/opportunity/tests/test_views.py  # single file
pytest commcare_connect/opportunity/tests/test_views.py::test_name  # single test
pytest --reuse-db                               # reuse DB between runs (default via pyproject.toml)
```

Settings used for tests: `config.settings.test` (set via `pyproject.toml` `addopts`).

### Linting & Formatting

Pre-commit runs black, isort, flake8, prettier (for non-template files), pyupgrade, and django-upgrade.

```bash
pre-commit run -a                               # run all hooks on all files
black commcare_connect/                         # format Python (line length 119)
isort commcare_connect/                         # sort imports
flake8 commcare_connect/                        # lint
```

### JS/CSS Build

```bash
inv build-js                                    # one-time dev build
inv build-js -w                                 # watch mode
inv build-js --prod                             # production build
```

Tailwind v4 + Webpack. Source CSS lives in `tailwind/tailwind.css`; compiled output goes to `commcare_connect/static/bundles/`. When adding new Tailwind utility classes that are only generated dynamically (e.g. from Python), add them to `tailwind/safelists.txt`.

### Django Management

```bash
./manage.py migrate
./manage.py makemigrations
./manage.py createsuperuser
./manage.py promote_user_to_superuser <email>
./manage.py makemessages --all --ignore node_modules --ignore venv
./manage.py compilemessages
```

### Docker / Services

```bash
inv up      # start PostgreSQL, Redis, etc. via docker compose
inv down    # stop services
```

### Celery

```bash
celery -A config.celery_app worker -l info
celery -A config.celery_app beat               # periodic tasks
```

## Architecture

### Project Structure

Built with Cookiecutter Django. Settings are in `config/settings/` (`base.py`, `local.py`, `test.py`, `production.py`). URLs are in `config/urls.py`. All application code lives under `commcare_connect/`.

### Django Apps

| App                                  | Purpose                                                                                                                                                                   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `users`                              | Custom `User` model, auth helpers, internal feature access, OTP/invite logic                                                                                              |
| `organization`                       | Workspaces (Organizations), membership roles (Admin/Member/Viewer)                                                                                                        |
| `opportunity`                        | Core domain — learning/delivery opportunities, worker access, visits, payments, invoicing                                                                                 |
| `program`                            | Program Manager layer grouping ManagedOpportunities across organizations                                                                                                  |
| `flags`                              | Custom Waffle `Flag` model extended with Organization/Opportunity/Program M2M relations; switch names and flag names are constants in `switch_names.py` / `flag_names.py` |
| `reports`                            | Admin-facing KPI and invoice reporting                                                                                                                                    |
| `connect_id_client`                  | HTTP client for the external ConnectID service (OTPs, messaging, user lookup)                                                                                             |
| `commcarehq` / `commcarehq_provider` | OAuth2 integration with CommCare HQ                                                                                                                                       |
| `form_receiver`                      | Receives XForm submissions from CommCare                                                                                                                                  |
| `microplanning`                      | Microplanning features gated behind the `microplanning` feature flag                                                                                                      |
| `data_export`                        | Data export utilities                                                                                                                                                     |
| `multidb`                            | Logical replication to a secondary DB (for Superset analytics); models included are defined in `multidb/constants.py`                                                     |
| `deid`                               | De-identification utilities                                                                                                                                               |

### Request Context — Middleware-Injected Attributes

`OrganizationMiddleware` (`users/middleware.py`) attaches lazy attributes to every request:

- `request.org` — current `Organization` (resolved from `org_slug` URL kwarg)
- `request.org_membership` — `UserOrganizationMembership` for the current user/org
- `request.memberships` — all memberships for the current user
- `request.is_opportunity_pm` — whether user is a program manager for the URL's opportunity

Views in org-scoped URLs (`/a/<org_slug>/...`) rely on these being set.

### Access Control Patterns

Three layers work together:

1. **Django permissions** — defined in `User.Meta.permissions`; constants in `utils/permission_const.py`. Used for internal/staff features (e.g. `ALL_ORG_ACCESS`, `PRODUCT_FEATURES_ACCESS`).

2. **Organization decorators** — `org_member_required`, `org_admin_required`, `org_viewer_required`, `org_program_manager_required` (all in `organization/decorators.py`). CBVs use corresponding mixins (`OrganizationUserMixin`, etc.). `ALL_ORG_ACCESS` permission bypasses org membership checks.

3. **Feature flags/switches** — Waffle-based. The custom `Flag` model (`flags/models.py`) adds M2M relations to Organization/Opportunity/Program on top of Waffle's user-based flags. Switches are global on/off. Check: `waffle.switch_is_active(SWITCH_NAME)` or `Flag.is_flag_active_for_request(request, FLAG_NAME)`. The `require_flag_for_opp` decorator gates views by opportunity-level flag activation.

### Internal Features

Staff-only pages are listed on `/users/internal_features/`. To add a new internal page:

1. Add a permission constant to `utils/permission_const.py` and `User.Meta.permissions`.
2. Add it to the `show_internal_features` property in `users/models.py`.
3. Add an entry to the `features` list in `users/views.py::internal_features`.

### URL Patterns

- Public/auth: `/`, `/accounts/`, `/users/`
- Org-scoped: `/a/<org_slug>/` — opportunity, program, microplanning, organization sub-apps
- Internal/admin: `/users/internal_features/`, `/admin_reports/`, `/flags/`
- API: `/api/` (DRF, documented via drf-spectacular at `/api/docs/`)

### Frontend Stack

- **Templates**: Django templates extending `base.html`. Use `{% block css %}`, `{% block content %}`, `{% block javascript %}`, `{% block inline_javascript %}`.
- **Tailwind CSS v4**: utility-first; component classes (`.button`, `.button-icon`, `.card_bg`, etc.) defined in `tailwind/tailwind.css`.
- **Alpine.js**: reactivity and component state in templates (`x-data`, `x-show`, `x-tooltip.raw`, etc.).
- **HTMX**: partial page updates (`hx-get`, `hx-post`, `hx-swap`).
- **TomSelect**: enhanced multi-select dropdowns. Enable on any `<select>` with `data-tomselect="1"`. Requires the CSS bundle (`bundles/css/tomselect.css`) and JS bundle (`bundles/js/tomselect-bundle.js`) to be loaded in that template's `{% block css/javascript %}`.
- **Crispy Forms** (tailwind pack): use `FormHelper` + `Layout` for form rendering.

### Feature Flags Usage Pattern

- Flag names: constants in `flags/flag_names.py`
- Switch names: constants in `flags/switch_names.py`
- Check a switch: `waffle.switch_is_active(SWITCH_NAME)`
- Check a flag for the current request org/opp/program: `flag.is_active_for(obj)` or `Flag.is_flag_active_for_request(request, flag_name)`
- Gate a view on a flag for its opportunity: `@require_flag_for_opp(FLAG_NAME)` decorator
- `WAFFLE_FLAG_MODEL = "flags.Flag"` and `WAFFLE_CREATE_MISSING_FLAGS/SWITCHES = True` are set, so flags/switches are auto-created on first access.

### Caching

`commcare_connect/cache.py` exports a `quickcache` decorator (60s default, 0 memoize). Used for expensive lookups (e.g. ConnectID API calls, opportunity PM status). Waffle flag relations use their own cache keys configurable via Django settings.

### Testing Conventions

- Uses `pytest` with `pytest-django`; fixtures in `commcare_connect/conftest.py` (global) and per-app `conftest.py`.
- Factories in `<app>/tests/factories.py` using `factory_boy`.
- `--reuse-db` is on by default; run `pytest --create-db` to force recreation.
- Waffle: use `waffle.testutils.override_switch` / `override_flag` context managers in tests.
