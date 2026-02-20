# AGENTS.md

This file provides guidance to code agents when working with code in this repository.

## Commands

```bash
# Tests
pytest                                                                           # all tests
pytest commcare_connect/opportunity/tests/test_views.py::test_name              # single test
pytest --reuse-db                                                                # reuse DB (default)

# Lint / format
pre-commit run -a                      # black, isort, flake8, prettier, pyupgrade, django-upgrade
black commcare_connect/                # line length 119
isort commcare_connect/

# JS/CSS
inv build-js                           # dev build
inv build-js -w                        # watch mode
inv build-js --prod

# Services
inv up / inv down                      # PostgreSQL, Redis via docker compose
celery -A config.celery_app worker -l info
celery -A config.celery_app beat

# Django
./manage.py migrate / makemigrations / createsuperuser
./manage.py promote_user_to_superuser <email>
./manage.py makemessages --all --ignore node_modules --ignore venv && compilemessages
```

Test settings: `config.settings.test` (via `pyproject.toml` `addopts`).
Tailwind source: `tailwind/tailwind.css` → compiled to `commcare_connect/static/bundles/`. Dynamically generated utility classes must be added to `tailwind/safelists.txt`.

## Architecture

### Project Structure

Cookiecutter Django. Settings in `config/settings/` (`base.py`, `local.py`, `test.py`, `production.py`). URLs in `config/urls.py`. All app code under `commcare_connect/`.

### Django Apps

| App                                  | Purpose                                                                                                                   |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| `users`                              | Custom `User` model, auth helpers, OTP/invite logic, internal feature access                                              |
| `organization`                       | Workspaces (Organizations), membership roles (Admin/Member/Viewer)                                                        |
| `opportunity`                        | Core domain — opportunities, worker access, visits, payments, invoicing                                                   |
| `program`                            | Program Manager layer grouping ManagedOpportunities across organizations                                                  |
| `flags`                              | Custom Waffle `Flag` extended with Organization/Opportunity/Program M2M; constants in `flag_names.py` / `switch_names.py` |
| `reports`                            | Admin-facing KPI and invoice reporting                                                                                    |
| `connect_id_client`                  | HTTP client for ConnectID (OTPs, messaging, user lookup)                                                                  |
| `commcarehq` / `commcarehq_provider` | OAuth2 integration with CommCare HQ                                                                                       |
| `form_receiver`                      | Receives XForm submissions from CommCare                                                                                  |
| `microplanning`                      | Microplanning features (gated behind feature flag)                                                                        |
| `multidb`                            | Logical replication to secondary DB (Superset analytics); models in `multidb/constants.py`                                |
| `data_export` / `deid`               | Data export and de-identification utilities                                                                               |

### URL Patterns

- Public/auth: `/`, `/accounts/`, `/users/`
- Org-scoped: `/a/<org_slug>/` — opportunity, program, microplanning, organization
- Internal/admin: `/users/internal_features/`, `/admin_reports/`, `/flags/`
- API: `/api/` (DRF; docs at `/api/docs/`)

### Middleware & Request Context

`OrganizationMiddleware` (`users/middleware.py`) attaches lazy attributes to every request: `request.org`, `request.org_membership`, `request.memberships`, `request.is_opportunity_pm`. All org-scoped views rely on these.

### Access Control

Three layers:

1. **Django permissions** — constants in `utils/permission_const.py` (e.g. `ALL_ORG_ACCESS`, `PRODUCT_FEATURES_ACCESS`). `ALL_ORG_ACCESS` bypasses org membership checks.
2. **Organization decorators** — `org_viewer_required`, `org_member_required`, `org_admin_required`, `org_program_manager_required` (`organization/decorators.py`); CBV equivalents are `OrganizationUserMixin` etc.
3. **Feature flags/switches** — Waffle-based. Custom `Flag` model (`flags/models.py`) adds M2M to Org/Opportunity/Program. Check: `waffle.switch_is_active(NAME)` or `Flag.is_flag_active_for_request(request, NAME)`. Gate a view: `@require_flag_for_opp(FLAG_NAME)`. Flags/switches are auto-created on first access.

### Internal Features

Staff-only pages live at `/users/internal_features/`. To add one: add a permission constant to `utils/permission_const.py` and `User.Meta.permissions`, add it to `show_internal_features` in `users/models.py`, and add an entry to `features` in `users/views.py::internal_features`.

### Frontend Stack

- **Templates**: Django templates extending `base.html` (`{% block css/content/javascript/inline_javascript %}`).
- **Tailwind CSS v4** + **Alpine.js** (`x-data`, `x-show`, `x-tooltip.raw`) + **HTMX** (`hx-get`, `hx-post`, `hx-swap`).
- **TomSelect**: enhanced `<select>` — add `data-tomselect="1"` and load `tomselect.css` + `tomselect-bundle.js` bundles.
- **Crispy Forms** (tailwind pack): use `FormHelper` + `Layout`.

### Caching

`cache.py` exports `quickcache` (60s TTL, 0 memoize). Used for ConnectID calls, exchange rate lookups, and opportunity PM status. Redis locks used for concurrent visit processing.

### Testing

- `pytest-django`; global fixtures in `commcare_connect/conftest.py`, per-app fixtures alongside tests.
- Factories via `factory_boy` in `<app>/tests/factories.py`.
- `--reuse-db` on by default; use `pytest --create-db` to force recreation.
- Waffle: use `waffle.testutils.override_switch` / `override_flag` in tests.

## Core concepts

CommCare Connect connects **organizations** to **workers** via **opportunities** — programs where workers complete learning and delivery tasks using CommCare mobile apps and receive payments for verified work.

### Models

**Organization** — primary workspace/tenant (`slug` for URL routing). `UserOrganizationMembership` assigns roles: Admin, Member (write), Viewer (read-only). Orgs with `program_manager=True` can own Programs.

**Opportunity** (`opportunity/models.py`) — central domain entity. Links `learn_app` (training) and `deliver_app` (fieldwork). Key flags: `active`, `auto_approve_visits`, `auto_approve_payments`, `managed`, `is_test`. Budget set via `total_budget`; per-visit rates via **PaymentUnit** children.

**PaymentUnit** — defines `amount` (local currency), `org_amount` (org portion, managed only), `max_total` and `max_daily` per user. Multiple per Opportunity; supports `parent_payment_unit` hierarchy.

**OpportunityAccess** — joins a user to an opportunity. Tracks `accepted`, `suspended`, `completed_learn_date`, `payment_accrued` (cached). **OpportunityClaim** (one-to-one) holds claimed budget; **OpportunityClaimLimit** enforces per-PaymentUnit `max_visits`.

**Program & ManagedOpportunity** (`program/models.py`) — Program groups opportunities across orgs. ManagedOpportunity is a proxy of Opportunity with `managed=True` and a `program` FK. Managed opps require PM review before payments finalize and track `org_amount`.

**UserVisit** — one record per form submission: `xform_id`, `entity_id` (beneficiary), `visit_date`, `form_json`, `location` (GPS). `flagged` + `flag_reason` (JSON array) record failed checks.

- `VisitValidationStatus`: `pending` → `approved` | `rejected` | `over_limit` | `duplicate` | `trial`
- `VisitReviewStatus` (managed only): `pending` → `agree` | `disagree`

**CompletedWork** — billable unit aggregating UserVisits for `(opportunity_access, entity_id, payment_unit)`.

- `CompletedWorkStatus`: `pending` → `approved` | `rejected` | `over_limit` | `incomplete`
- Denormalized `saved_*` fields (`saved_approved_count`, `saved_payment_accrued`, `saved_payment_accrued_usd`, `saved_org_payment_accrued*`) updated by batch tasks.
- `payment_accrued = approved_count × payment_unit.amount`. For managed opps, also tracks `org_payment_accrued`.
- Stays `pending` on managed opps until all visits have `review_status=agree`.

### Form Processing (`form_receiver/processor.py`)

XForms arrive at `/api/receiver/form/`. `process_xform()` routes by `app_id`:

- **Learn path**: creates `CompletedModule` + `Assessment` records; sets `OpportunityAccess.completed_learn_date` at 100%.
- **Deliver path**: creates/updates `UserVisit`; runs validation (duplicate, GPS, proximity, catchment, time-window, attachment, duration, custom JSONPath rules); auto-approves if `auto_approve_visits=True` and no flags; creates/updates `CompletedWork`. Redis locks (`visit_processor:{access_id}:{deliver_unit_id}:{entity_id}`) prevent race conditions.

### Payment & Invoicing

1. `CompletedWork` reaches `approved` status.
2. Payments created via batch (`bulk_update_payments` in `opportunity/visit_import.py`).
3. `PaymentInvoice` groups payments through a state machine:
   `pending_nm_review` → `pending_pm_review` → `ready_to_pay` → `paid` / `archived`
   NM can cancel; PM can reject.
4. Exchange rates cached by date; USD-converted amounts stored on `CompletedWork`.

### Key Computed Properties

| Model             | Property            | Notes                                   |
| ----------------- | ------------------- | --------------------------------------- |
| Opportunity       | `remaining_budget`  | `total_budget` − sum of claimed budgets |
| Opportunity       | `is_setup_complete` | Has PaymentUnits, budget, dates, limits |
| OpportunityAccess | `learn_progress`    | `(completed / total modules) × 100`     |
| OpportunityAccess | `visit_count`       | Excludes `over_limit` visits            |
| CompletedWork     | `payment_accrued`   | `approved_count × payment_unit.amount`  |

### External Integrations

- **ConnectID** (`connect_id_client/`): mobile user identity, OTPs, messaging. Calls cached via `quickcache`.
- **CommCare HQ** (`commcarehq/`): OAuth2 for app management and form routing. `ConnectIDUserLink` maps Connect users to HQ usernames.

## Do's when making changes

- Always lint, test, and typecheck updated files.
- When adding new features: write or update unit tests first, then code to green.
- For regressions: add a failing test that reproduces the bug, then fix to green.
- Always use `.github/pull_request_template.md` for pull request descriptions.
