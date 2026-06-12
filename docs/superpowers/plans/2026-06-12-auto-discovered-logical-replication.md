# Auto-discovered Logical Replication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-maintained `REPLICATION_ALLOWED_MODELS` list with CI-enforced twin lists, and split the interactive `setup_logical_replication` command into a one-time **bootstrap** plus a non-interactive **refresh** that runs on every deploy using only the connections Django already has.

**Architecture:** Implements the three decisions in `docs/replication-redesign-recommendation.md`: **D1.B** (twin `REPLICATION_INCLUDED_MODELS` / `REPLICATION_EXCLUDED_MODELS` lists, enforced by a Django system check), **D2** (a `refresh_replication` management command wired into the deploy pipeline *after* `migrate_multi` returns — the only race-safe trigger), and **D3.B** (bootstrap transfers subscription ownership to the app's secondary role so the recurring refresh needs no superuser). Pure logic lives in a new testable `commcare_connect/multidb/replication.py`; the management commands stay thin orchestrators.

**Tech Stack:** Django 4.2, PostgreSQL/PostGIS logical replication, psycopg2, pytest + pytest-django.

**Scope note — local models only:** The system check enforces classification over **local app models** (`settings.LOCAL_APPS`) only, auto-excluding pghistory `Event` models and auto-created m2m through tables. Third-party tables (sessions, auth, oauth2 tokens, celery-beat, waffle, pghistory event tables) are never replicated — the safe default — without being enumerated.

**Out of scope (do NOT do in this plan):**
- The blue/green Postgres 15 → 16 upgrade of the production secondary. D3.B's "no superuser in the refresh path" is only *legal* on PG16+ (a non-superuser may own a subscription). This plan writes code that is correct on PG16+; rolling `REPLICATION_ENABLED=True` to production must wait for that upgrade. Until then the recurring refresh on the production secondary still needs a superuser-owned subscription (it will no-op safely while `REPLICATION_ENABLED=False`).
- Column-level sensitivity (a sensitive *column* added to an included model still flows through).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `commcare_connect/multidb/constants.py` | Holds the two model lists + publication/subscription names | Modify (rename list, add second list) |
| `commcare_connect/multidb/replication.py` | Pure, testable replication logic: model discovery, the classification check, in-sync detection, gating, and the publication/subscription refresh primitives | **Create** |
| `commcare_connect/multidb/checks.py` | Django system check wrapping the pure classification function | **Create** |
| `commcare_connect/multidb/apps.py` | Registers the system check in `AppConfig.ready()` | Modify |
| `commcare_connect/multidb/management/commands/refresh_replication.py` | Non-interactive deploy-pipeline refresh command | **Create** |
| `commcare_connect/multidb/management/commands/setup_logical_replication.py` | Bootstrap: now also transfers subscription ownership; uses new discovery | Modify |
| `commcare_connect/multidb/management/commands/logical_replication_status.py` | Status command; switch to new discovery name | Modify |
| `config/settings/base.py` | `REPLICATION_ENABLED`, `REPLICATION_PRIMARY_REPL_USER`, `REPLICATION_SUPERSET_USER` settings | Modify |
| `docker/start_migrate` | Deploy wiring: run `refresh_replication` after `migrate_multi` | Modify |
| `commcare_connect/multidb/tests/` | Tests for all of the above | **Create** |

**Object-ownership target after bootstrap (PG16+):**

| Object | Owner | Established by |
|--------|-------|---------------|
| `tables_for_superset_pub` (primary) | app primary role | already — `CREATE PUBLICATION` runs as the app role |
| `tables_for_superset_sub` (secondary) | app secondary role | **new** `ALTER SUBSCRIPTION ... OWNER TO` in bootstrap |

---

## Task 1: Twin lists + discovery functions

Rename `REPLICATION_ALLOWED_MODELS` → `REPLICATION_INCLUDED_MODELS`, add `REPLICATION_EXCLUDED_MODELS`, and create `replication.py` with the discovery functions. Both existing commands import the old name, so they are updated here to keep imports valid.

**Files:**
- Modify: `commcare_connect/multidb/constants.py`
- Create: `commcare_connect/multidb/replication.py`
- Modify: `commcare_connect/multidb/management/commands/setup_logical_replication.py:8,19`
- Modify: `commcare_connect/multidb/management/commands/logical_replication_status.py:8,126`
- Create: `commcare_connect/multidb/tests/__init__.py`
- Create: `commcare_connect/multidb/tests/test_replication.py`

- [ ] **Step 1: Write the failing test**

Create `commcare_connect/multidb/tests/__init__.py` (empty file), then create `commcare_connect/multidb/tests/test_replication.py`:

```python
from commcare_connect.multidb import replication
from commcare_connect.multidb.constants import (
    REPLICATION_EXCLUDED_MODELS,
    REPLICATION_INCLUDED_MODELS,
)


def test_get_replicated_models_returns_included_list():
    assert replication.get_replicated_models() == REPLICATION_INCLUDED_MODELS


def test_get_replicated_tables_returns_db_table_names():
    tables = replication.get_replicated_tables()
    assert "opportunity_opportunity" in tables
    assert tables == {m._meta.db_table for m in REPLICATION_INCLUDED_MODELS}


def test_included_and_excluded_are_disjoint():
    overlap = set(REPLICATION_INCLUDED_MODELS) & set(REPLICATION_EXCLUDED_MODELS)
    assert overlap == set(), f"models in both lists: {overlap}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/multidb/tests/test_replication.py -v`
Expected: FAIL — `ImportError` (`REPLICATION_INCLUDED_MODELS` / `replication` do not exist yet).

- [ ] **Step 3: Update `constants.py`**

Replace the entire contents of `commcare_connect/multidb/constants.py` with:

```python
from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.commcarehq.models import HQServer
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.opportunity.models import (
    Assessment,
    AssignedTask,
    BlobMeta,
    CatchmentArea,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    Country,
    CredentialConfiguration,
    Currency,
    DeliverUnit,
    DeliverUnitFlagRules,
    DeliveryType,
    ExchangeRate,
    FormJsonValidationRules,
    HQApiKey,
    LabsRecord,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    TaskType,
    UserInvite,
    UserVisit,
)
from commcare_connect.organization.models import (
    LLOEntity,
    Organization,
    UserOrganizationMembership,
)
from commcare_connect.program.models import (
    ManagedOpportunity,
    Program,
    ProgramApplication,
)
from commcare_connect.reports.models import UserAnalyticsData
from commcare_connect.users.models import ConnectIDUserLink, User, UserCredential

PUBLICATION_NAME = "tables_for_superset_pub"
SUBSCRIPTION_NAME = "tables_for_superset_sub"

# Models whose tables are replicated to the secondary (Superset) database.
# Every local app model must appear in exactly ONE of the two lists below;
# a Django system check (see checks.py) fails otherwise. Adding a sensitive
# model? Put it in REPLICATION_EXCLUDED_MODELS.
REPLICATION_INCLUDED_MODELS = [
    Assessment,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    ConnectIDUserLink,
    Country,
    Currency,
    DeliverUnit,
    DeliverUnitFlagRules,
    DeliveryType,
    LearnModule,
    LLOEntity,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    Organization,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    Program,
    User,
    UserAnalyticsData,
    UserCredential,
    UserVisit,
]

# Local app models deliberately NOT replicated (PII, secrets, operational
# noise, or simply not needed in Superset).
REPLICATION_EXCLUDED_MODELS = [
    AuditReport,
    AuditReportEntry,
    HQServer,
    Flag,
    WorkArea,
    WorkAreaGroup,
    AssignedTask,
    BlobMeta,
    CatchmentArea,
    CredentialConfiguration,
    ExchangeRate,
    FormJsonValidationRules,
    HQApiKey,
    LabsRecord,
    TaskType,
    UserInvite,
    UserOrganizationMembership,
    ManagedOpportunity,
    ProgramApplication,
]
```

- [ ] **Step 4: Create `replication.py`**

Create `commcare_connect/multidb/replication.py`:

```python
from commcare_connect.multidb.constants import REPLICATION_INCLUDED_MODELS


def get_replicated_models():
    """Models whose tables should be present in the publication."""
    return REPLICATION_INCLUDED_MODELS


def get_replicated_tables() -> set[str]:
    """The set of db_table names that should be replicated."""
    return {model._meta.db_table for model in get_replicated_models()}
```

- [ ] **Step 5: Update the two commands' imports so nothing breaks**

In `commcare_connect/multidb/management/commands/setup_logical_replication.py`, change line 8 and line 19:

```python
# line 8 — was: from commcare_connect.multidb.constants import REPLICATION_ALLOWED_MODELS
from commcare_connect.multidb.replication import get_replicated_models
```

```python
# in get_table_list(), line 19 — was: for model in REPLICATION_ALLOWED_MODELS:
        for model in get_replicated_models():
```

In `commcare_connect/multidb/management/commands/logical_replication_status.py`, change line 8 and line 126:

```python
# line 8 — was: from commcare_connect.multidb.constants import REPLICATION_ALLOWED_MODELS
from commcare_connect.multidb.replication import get_replicated_models
```

```python
# line 126 — was: for model in REPLICATION_ALLOWED_MODELS:
        for model in get_replicated_models():
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest commcare_connect/multidb/tests/test_replication.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Verify no other references to the old name remain**

Run: `grep -rn "REPLICATION_ALLOWED_MODELS" commcare_connect/ config/`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add commcare_connect/multidb/constants.py commcare_connect/multidb/replication.py \
  commcare_connect/multidb/tests/__init__.py commcare_connect/multidb/tests/test_replication.py \
  commcare_connect/multidb/management/commands/setup_logical_replication.py \
  commcare_connect/multidb/management/commands/logical_replication_status.py
git commit -m "Replace replication allowlist with CI-enforceable twin lists"
```

---

## Task 2: CI-enforced classification (Django system check)

Every local app model must be in exactly one list. A Django system check (runs on `manage.py check`, `runserver`, and CI) fails on any unclassified or double-classified model. pghistory `Event` models and auto-created m2m tables are auto-excluded from the requirement.

**Files:**
- Create: `commcare_connect/multidb/checks.py`
- Modify: `commcare_connect/multidb/apps.py`
- Modify: `commcare_connect/multidb/replication.py`
- Modify: `commcare_connect/multidb/tests/test_replication.py`

- [ ] **Step 1: Write the failing test**

Append to `commcare_connect/multidb/tests/test_replication.py`:

```python
def test_no_unclassified_local_models():
    # Every local model is in exactly one list; this set must be empty.
    assert replication.get_unclassified_models() == set()


def test_unclassified_excludes_pghistory_events_and_m2m():
    # The "must classify" set never includes pghistory Event models or
    # auto-created through tables.
    import pghistory.models

    must_classify = replication.get_models_requiring_classification()
    for model in must_classify:
        assert not model._meta.auto_created
        assert not issubclass(model, pghistory.models.Event)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/multidb/tests/test_replication.py -k unclassified -v`
Expected: FAIL — `AttributeError: module 'replication' has no attribute 'get_unclassified_models'`.

- [ ] **Step 3: Add the discovery + classification functions to `replication.py`**

Append to `commcare_connect/multidb/replication.py`:

```python
import pghistory.models
from django.apps import apps
from django.conf import settings

from commcare_connect.multidb.constants import (
    REPLICATION_EXCLUDED_MODELS,
    REPLICATION_INCLUDED_MODELS,
)


def _local_app_labels() -> set[str]:
    return {
        app_config.label
        for app_config in apps.get_app_configs()
        if app_config.name in settings.LOCAL_APPS
    }


def get_models_requiring_classification() -> set:
    """Local, concrete, non-history models that must appear in one list.

    Excludes pghistory Event models and auto-created m2m through tables —
    these are never replicated and never need classifying.
    """
    local_labels = _local_app_labels()
    return {
        model
        for model in apps.get_models()
        if model._meta.app_label in local_labels
        and not model._meta.auto_created
        and not issubclass(model, pghistory.models.Event)
    }


def get_unclassified_models() -> set:
    classified = set(REPLICATION_INCLUDED_MODELS) | set(REPLICATION_EXCLUDED_MODELS)
    return get_models_requiring_classification() - classified


def get_doubly_classified_models() -> set:
    return set(REPLICATION_INCLUDED_MODELS) & set(REPLICATION_EXCLUDED_MODELS)
```

Note: keep the existing top-of-file import of `REPLICATION_INCLUDED_MODELS` (from Task 1); the new block re-imports both list names — consolidate into the single existing import line so there is only one import of the constants module.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest commcare_connect/multidb/tests/test_replication.py -k unclassified -v`
Expected: PASS (2 passed). If `test_no_unclassified_local_models` fails, the failure message lists models missing from both lists — add each to `REPLICATION_INCLUDED_MODELS` or `REPLICATION_EXCLUDED_MODELS` in `constants.py`.

- [ ] **Step 5: Create the system check**

Create `commcare_connect/multidb/checks.py`:

```python
from django.core.checks import Error, register

from commcare_connect.multidb import replication


@register()
def check_all_models_classified_for_replication(app_configs, **kwargs):
    errors = []

    unclassified = replication.get_unclassified_models()
    if unclassified:
        names = ", ".join(sorted(m._meta.label for m in unclassified))
        errors.append(
            Error(
                "Models are not classified for logical replication: " + names,
                hint=(
                    "Add each model to REPLICATION_INCLUDED_MODELS or "
                    "REPLICATION_EXCLUDED_MODELS in "
                    "commcare_connect/multidb/constants.py."
                ),
                id="multidb.E001",
            )
        )

    doubly = replication.get_doubly_classified_models()
    if doubly:
        names = ", ".join(sorted(m._meta.label for m in doubly))
        errors.append(
            Error(
                "Models appear in BOTH replication lists: " + names,
                hint="Each model must be in exactly one list.",
                id="multidb.E002",
            )
        )

    return errors
```

- [ ] **Step 6: Register the check in `apps.py`**

In `commcare_connect/multidb/apps.py`, add a `ready()` method to `MultidbConfig` (the class at the bottom of the file):

```python
class MultidbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commcare_connect.multidb"

    def ready(self):
        from commcare_connect.multidb import checks  # noqa: F401  (registers system checks)
```

- [ ] **Step 7: Verify the check passes against the real registry**

Run: `python manage.py check`
Expected: `System check identified no issues` (no `multidb.E001` / `multidb.E002`).

- [ ] **Step 8: Run the full multidb test module**

Run: `pytest commcare_connect/multidb/tests/test_replication.py -v`
Expected: PASS (all tests).

- [ ] **Step 9: Commit**

```bash
git add commcare_connect/multidb/checks.py commcare_connect/multidb/apps.py \
  commcare_connect/multidb/replication.py commcare_connect/multidb/tests/test_replication.py
git commit -m "Enforce replication model classification via system check"
```

---

## Task 3: Gating settings + in-sync / bootstrap-state detection

Add the settings that gate whether the refresh runs, plus the pure helpers the refresh command uses to decide whether to act: `replication_is_active()`, `publication_exists()`, and `is_publication_in_sync()`.

**Files:**
- Modify: `config/settings/base.py:50-55` (the secondary-DB block)
- Modify: `commcare_connect/multidb/replication.py`
- Create: `commcare_connect/multidb/tests/test_gating.py`

- [ ] **Step 1: Write the failing test**

Create `commcare_connect/multidb/tests/test_gating.py`:

```python
from contextlib import contextmanager

from django.test import override_settings

from commcare_connect.multidb import replication


class FakeCursor:
    """Captures executed SQL and returns canned fetch results."""

    def __init__(self, fetchall_result=None):
        self.executed = []
        self._fetchall_result = fetchall_result or []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchall_result[0] if self._fetchall_result else None


class FakeConnection:
    def __init__(self, fetchall_result=None):
        self.cursor_obj = FakeCursor(fetchall_result)

    @contextmanager
    def cursor(self):
        yield self.cursor_obj


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS="secondary")
def test_replication_is_active_when_enabled_and_secondary_configured():
    assert replication.replication_is_active() is True


@override_settings(REPLICATION_ENABLED=False, SECONDARY_DB_ALIAS="secondary")
def test_replication_inactive_when_disabled():
    assert replication.replication_is_active() is False


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS=None)
def test_replication_inactive_without_secondary():
    assert replication.replication_is_active() is False


def test_publication_exists_true_when_row_returned():
    conn = FakeConnection(fetchall_result=[("tables_for_superset_pub",)])
    assert replication.publication_exists(conn) is True


def test_publication_exists_false_when_no_row():
    conn = FakeConnection(fetchall_result=[])
    assert replication.publication_exists(conn) is False


def test_is_publication_in_sync_true_when_tables_match(monkeypatch):
    desired = {"opportunity_opportunity", "users_user"}
    monkeypatch.setattr(replication, "get_replicated_tables", lambda: desired)
    rows = [(t,) for t in desired]
    conn = FakeConnection(fetchall_result=rows)
    assert replication.is_publication_in_sync(conn) is True


def test_is_publication_in_sync_false_when_table_missing(monkeypatch):
    monkeypatch.setattr(
        replication, "get_replicated_tables", lambda: {"a", "b"}
    )
    conn = FakeConnection(fetchall_result=[("a",)])  # missing "b"
    assert replication.is_publication_in_sync(conn) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/multidb/tests/test_gating.py -v`
Expected: FAIL — `AttributeError` (`replication_is_active` / `publication_exists` / `is_publication_in_sync` not defined).

- [ ] **Step 3: Add the settings**

In `config/settings/base.py`, the secondary-DB block currently reads (lines ~50-55):

```python
SECONDARY_DB_ALIAS = None
if env("SECONDARY_DATABASE_URL", default=None):
    SECONDARY_DB_ALIAS = "secondary"
    DATABASES[SECONDARY_DB_ALIAS] = env.db("SECONDARY_DATABASE_URL")
    DATABASES[SECONDARY_DB_ALIAS]["ENGINE"] = "django.contrib.gis.db.backends.postgis"
    DATABASE_ROUTERS = ["commcare_connect.multidb.db_router.ConnectDatabaseRouter"]
```

Immediately after that block, add:

```python
# Logical replication to the secondary (Superset) database.
# Gates whether `refresh_replication` does anything on deploy. Keep False
# until the secondary is bootstrapped AND running Postgres 16+.
REPLICATION_ENABLED = env.bool("REPLICATION_ENABLED", default=False)
# Roles the refresh grants SELECT to (must already exist; created at bootstrap).
REPLICATION_PRIMARY_REPL_USER = env("REPLICATION_PRIMARY_REPL_USER", default="postgres_repl")
REPLICATION_SUPERSET_USER = env("REPLICATION_SUPERSET_USER", default="superset_readonly")
```

- [ ] **Step 4: Add the helpers to `replication.py`**

Append to `commcare_connect/multidb/replication.py`:

```python
from commcare_connect.multidb.constants import PUBLICATION_NAME


def replication_is_active() -> bool:
    """Whether the deploy-time refresh should run in this environment."""
    return bool(settings.REPLICATION_ENABLED and settings.SECONDARY_DB_ALIAS)


def publication_exists(connection) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pubname FROM pg_publication WHERE pubname = %s",
            [PUBLICATION_NAME],
        )
        return cursor.fetchone() is not None


def _current_publication_tables(connection) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_publication_tables WHERE pubname = %s",
            [PUBLICATION_NAME],
        )
        return {row[0] for row in cursor.fetchall()}


def is_publication_in_sync(connection) -> bool:
    return get_replicated_tables() == _current_publication_tables(connection)
```

Note: `PUBLICATION_NAME` may already be importable; ensure it is imported once at the top of the module alongside the other `constants` imports rather than mid-file.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest commcare_connect/multidb/tests/test_gating.py -v`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add config/settings/base.py commcare_connect/multidb/replication.py \
  commcare_connect/multidb/tests/test_gating.py
git commit -m "Add replication gating settings and in-sync detection helpers"
```

---

## Task 4: `refresh_replication` command (D2 + D3.B recurring path)

The non-interactive deploy command. It gates on `replication_is_active()`, no-ops if bootstrap hasn't run (no publication) or the publication is already in sync, and otherwise reconciles the publication (primary) and refreshes the subscription (secondary) using only Django's existing connections. The SQL primitives are extracted as functions so they are unit-testable with a fake connection.

**Files:**
- Modify: `commcare_connect/multidb/replication.py`
- Create: `commcare_connect/multidb/management/commands/refresh_replication.py`
- Create: `commcare_connect/multidb/tests/test_refresh.py`

- [ ] **Step 1: Write the failing test**

Create `commcare_connect/multidb/tests/test_refresh.py`:

```python
from commcare_connect.multidb import replication
from commcare_connect.multidb.tests.test_gating import FakeConnection


def _executed_sql(conn):
    return " | ".join(sql for sql, _ in conn.cursor_obj.executed)


def test_refresh_publication_sets_tables_and_grants():
    conn = FakeConnection()
    replication.refresh_publication(
        conn, desired_tables={"users_user", "opportunity_opportunity"}, repl_user="postgres_repl"
    )
    sql = _executed_sql(conn)
    assert "ALTER PUBLICATION" in sql
    assert "SET TABLE" in sql
    assert '"opportunity_opportunity"' in sql
    assert '"users_user"' in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public" in sql
    assert '"postgres_repl"' in sql


def test_refresh_subscription_refreshes_and_grants():
    conn = FakeConnection()
    replication.refresh_subscription(conn, superset_user="superset_readonly")
    sql = _executed_sql(conn)
    assert "ALTER SUBSCRIPTION" in sql
    assert "REFRESH PUBLICATION" in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public" in sql
    assert '"superset_readonly"' in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/multidb/tests/test_refresh.py -v`
Expected: FAIL — `AttributeError` (`refresh_publication` / `refresh_subscription` not defined).

- [ ] **Step 3: Add the SQL primitives to `replication.py`**

Append to `commcare_connect/multidb/replication.py` (psycopg2 `sql` composition matches the style already used in `setup_logical_replication.py`):

```python
from psycopg2 import sql

from commcare_connect.multidb.constants import SUBSCRIPTION_NAME


def _grant_select_all(cursor, role: str):
    cursor.execute(
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}").format(
            role=sql.Identifier(role)
        )
    )


def refresh_publication(connection, *, desired_tables: set[str], repl_user: str):
    """On the primary: make the publication's table set match `desired_tables`
    and let the replication role read the (possibly new) tables."""
    tables = sql.SQL(", ").join(sql.Identifier(t) for t in sorted(desired_tables))
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("ALTER PUBLICATION {pub} SET TABLE {tables}").format(
                pub=sql.Identifier(PUBLICATION_NAME), tables=tables
            )
        )
        _grant_select_all(cursor, repl_user)


def refresh_subscription(connection, *, superset_user: str):
    """On the secondary: re-read the publication (COPY new tables, drop
    removed) and let the Superset reader see the newly arrived tables."""
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("ALTER SUBSCRIPTION {sub} REFRESH PUBLICATION").format(
                sub=sql.Identifier(SUBSCRIPTION_NAME)
            )
        )
        _grant_select_all(cursor, superset_user)
```

Note: consolidate the `SUBSCRIPTION_NAME` / `PUBLICATION_NAME` imports into the single `constants` import at the top of the module.

- [ ] **Step 4: Run primitive tests to verify they pass**

Run: `pytest commcare_connect/multidb/tests/test_refresh.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Write the failing command-orchestration test**

Append to `commcare_connect/multidb/tests/test_refresh.py`:

```python
from io import StringIO

from django.core.management import call_command
from django.test import override_settings


@override_settings(REPLICATION_ENABLED=False, SECONDARY_DB_ALIAS=None)
def test_command_noops_when_replication_inactive():
    out = StringIO()
    call_command("refresh_replication", stdout=out)
    assert "skipping" in out.getvalue().lower()


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS="secondary")
def test_command_noops_when_bootstrap_not_run(monkeypatch):
    monkeypatch.setattr(replication, "publication_exists", lambda conn: False)
    out = StringIO()
    call_command("refresh_replication", stdout=out)
    assert "bootstrap" in out.getvalue().lower()


@override_settings(REPLICATION_ENABLED=True, SECONDARY_DB_ALIAS="secondary")
def test_command_noops_when_already_in_sync(monkeypatch):
    monkeypatch.setattr(replication, "publication_exists", lambda conn: True)
    monkeypatch.setattr(replication, "is_publication_in_sync", lambda conn: True)
    out = StringIO()
    call_command("refresh_replication", stdout=out)
    assert "in sync" in out.getvalue().lower()
```

- [ ] **Step 6: Run to verify it fails**

Run: `pytest commcare_connect/multidb/tests/test_refresh.py -k command -v`
Expected: FAIL — `CommandError: Unknown command: 'refresh_replication'`.

- [ ] **Step 7: Create the command**

Create `commcare_connect/multidb/management/commands/refresh_replication.py`:

```python
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections

from commcare_connect.multidb import replication


class Command(BaseCommand):
    help = (
        "Reconcile the logical-replication publication/subscription with the "
        "models in REPLICATION_INCLUDED_MODELS. Non-interactive; safe to run on "
        "every deploy after migrate_multi. Requires a one-time bootstrap "
        "(setup_logical_replication) to have run for this environment."
    )

    def handle(self, *args, **options):
        if not replication.replication_is_active():
            self.stdout.write("Replication not enabled for this environment; skipping.")
            return

        primary = connections[DEFAULT_DB_ALIAS]

        if not replication.publication_exists(primary):
            self.stdout.write(
                self.style.WARNING(
                    "Publication does not exist — run the one-time bootstrap "
                    "(setup_logical_replication) first. Skipping refresh."
                )
            )
            return

        if replication.is_publication_in_sync(primary):
            self.stdout.write("Publication already in sync; nothing to do.")
            return

        desired = replication.get_replicated_tables()
        self.stdout.write("Publication out of sync; reconciling...")
        replication.refresh_publication(
            primary,
            desired_tables=desired,
            repl_user=settings.REPLICATION_PRIMARY_REPL_USER,
        )
        self.stdout.write(self.style.SUCCESS("Primary publication updated."))

        secondary = connections[settings.SECONDARY_DB_ALIAS]
        replication.refresh_subscription(
            secondary,
            superset_user=settings.REPLICATION_SUPERSET_USER,
        )
        self.stdout.write(self.style.SUCCESS("Secondary subscription refreshed."))
```

- [ ] **Step 8: Run the command tests to verify they pass**

Run: `pytest commcare_connect/multidb/tests/test_refresh.py -v`
Expected: PASS (all tests).

- [ ] **Step 9: Commit**

```bash
git add commcare_connect/multidb/replication.py \
  commcare_connect/multidb/management/commands/refresh_replication.py \
  commcare_connect/multidb/tests/test_refresh.py
git commit -m "Add non-interactive refresh_replication command"
```

---

## Task 5: Extend bootstrap to transfer subscription ownership (D3.B)

`setup_logical_replication` stays the interactive, operator-run bootstrap, but after creating/refreshing the subscription it transfers subscription ownership to the app's secondary role (the role from `SECONDARY_DATABASE_URL`), so the recurring `refresh_replication` can `REFRESH PUBLICATION` without a superuser. This `ALTER SUBSCRIPTION ... OWNER TO` runs on the superuser connection that bootstrap already opened.

**Files:**
- Modify: `commcare_connect/multidb/management/commands/setup_logical_replication.py`
- Create: `commcare_connect/multidb/tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing test**

Create `commcare_connect/multidb/tests/test_bootstrap.py`:

```python
from commcare_connect.multidb.management.commands import setup_logical_replication
from commcare_connect.multidb.tests.test_gating import FakeCursor


def test_transfer_subscription_ownership_issues_alter_owner():
    cursor = FakeCursor()
    setup_logical_replication.transfer_subscription_ownership(cursor, "app_secondary_role")
    sql = " ".join(s for s, _ in cursor.executed)
    assert "ALTER SUBSCRIPTION" in sql
    assert "OWNER TO" in sql
    assert '"app_secondary_role"' in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/multidb/tests/test_bootstrap.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'transfer_subscription_ownership'`.

- [ ] **Step 3: Add the ownership-transfer helper**

In `commcare_connect/multidb/management/commands/setup_logical_replication.py`, add a module-level function (near the top, after the imports and name constants):

```python
def transfer_subscription_ownership(cursor, secondary_role: str):
    """Hand subscription ownership to the app's secondary role so the
    recurring refresh can REFRESH PUBLICATION without a superuser (PG16+)."""
    cursor.execute(
        psycopg2.sql.SQL("ALTER SUBSCRIPTION {sub} OWNER TO {role}").format(
            sub=psycopg2.sql.Identifier(SUBSCRIPTION_NAME),
            role=psycopg2.sql.Identifier(secondary_role),
        )
    )
```

- [ ] **Step 4: Call it after the subscription is created/refreshed**

In `setup_logical_replication.py`, the `with secondary_conn.cursor() as cursor:` block (currently lines ~97-133) creates or refreshes the subscription and then grants SELECT to the superset user. After the `_grant` / superset GRANT statement and before closing the connection, add the ownership transfer. Insert immediately after the superset GRANT `cursor.execute(...)` (around line 133):

```python
            self.stdout.write("Transferring subscription ownership to the app secondary role...")
            secondary_role = secondary_db_settings["USER"]
            transfer_subscription_ownership(cursor, secondary_role)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Subscription '{SUBSCRIPTION_NAME}' now owned by '{secondary_role}'."
                )
            )
```

(`secondary_db_settings` is already defined earlier in `handle()` at line ~80.)

- [ ] **Step 5: Run the bootstrap test to verify it passes**

Run: `pytest commcare_connect/multidb/tests/test_bootstrap.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Sanity-check the command still imports/parses**

Run: `python manage.py help setup_logical_replication`
Expected: prints the command help with no import errors.

- [ ] **Step 7: Commit**

```bash
git add commcare_connect/multidb/management/commands/setup_logical_replication.py \
  commcare_connect/multidb/tests/test_bootstrap.py
git commit -m "Transfer subscription ownership to app secondary role at bootstrap"
```

---

## Task 6: Wire `refresh_replication` into the deploy pipeline (D2 trigger)

`docker/start_migrate` runs `migrate_multi --noinput` then starts gunicorn. The only race-safe place to refresh replication is *after* `migrate_multi` returns (both DBs are at the new schema). Add the refresh between them. It is a no-op unless `REPLICATION_ENABLED=True`, so this is safe to merge before the PG16 upgrade.

**Files:**
- Modify: `docker/start_migrate`

- [ ] **Step 1: Read the current script**

Run: `cat docker/start_migrate`
Expected current contents:

```bash
#!/bin/bash

set -o errexit
set -o pipefail
set -o nounset


echo "Django migrate"
python manage.py migrate_multi --noinput
echo "Run Gunicorn"
gunicorn config.wsgi --bind 0.0.0.0:8000 --chdir=/app --timeout 450 -w 10
```

- [ ] **Step 2: Insert the refresh step**

Edit `docker/start_migrate` so the lines between `migrate_multi` and `Run Gunicorn` become:

```bash
echo "Django migrate"
python manage.py migrate_multi --noinput
echo "Refresh logical replication"
python manage.py refresh_replication
echo "Run Gunicorn"
gunicorn config.wsgi --bind 0.0.0.0:8000 --chdir=/app --timeout 450 -w 10
```

(The script uses `set -o errexit`; `refresh_replication` exits 0 on the no-op paths, so deploys are unaffected when replication is disabled.)

- [ ] **Step 3: Verify the script is still valid bash**

Run: `bash -n docker/start_migrate`
Expected: no output (syntax OK).

- [ ] **Step 4: Commit**

```bash
git add docker/start_migrate
git commit -m "Run refresh_replication after migrate_multi on deploy"
```

---

## Task 7: Update `logical_replication_status` to drop the superuser prompt

Per the recommendation's "Out of scope → `logical_replication_status.py`" note: under D3.B its read-only status checks can run on the connections Django already has, so the secondary-superuser prompt is no longer needed. This task makes the status command consistent with the new model (its discovery import was already fixed in Task 1). This is a refactor of an interactive command; the meaningful unit is the table-count helper.

**Files:**
- Modify: `commcare_connect/multidb/management/commands/logical_replication_status.py`
- Modify: `commcare_connect/multidb/tests/test_replication.py`

- [ ] **Step 1: Write the failing test**

Append to `commcare_connect/multidb/tests/test_replication.py`:

```python
def test_table_count_rows_uses_replicated_models(monkeypatch):
    from commcare_connect.multidb.management.commands import logical_replication_status

    class C:
        def __init__(self, n):
            self.n = n
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self.executed.append(sql)

        def fetchone(self):
            return (self.n,)

    class Conn:
        def __init__(self, n):
            self.n = n

        def cursor(self):
            return C(self.n)

    rows = logical_replication_status.table_count_rows(Conn(5), Conn(3))
    tables = {r[0] for r in rows}
    assert "opportunity_opportunity" in tables
    assert all(r[1] == 5 and r[2] == 3 for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/multidb/tests/test_replication.py -k table_count -v`
Expected: FAIL — `AttributeError: ... has no attribute 'table_count_rows'`.

- [ ] **Step 3: Extract the table-count helper and use Django's secondary connection**

In `logical_replication_status.py`:

(a) Add a module-level helper (after imports):

```python
def table_count_rows(primary_conn, secondary_conn):
    """Return [(table_name, primary_count, secondary_count), ...] for all
    replicated models."""
    rows = []
    for model in get_replicated_models():
        table_name = model._meta.db_table
        with primary_conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
            primary_count = cursor.fetchone()[0]
        with secondary_conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
            secondary_count = cursor.fetchone()[0]
        rows.append((table_name, primary_count, secondary_count))
    return rows
```

(b) Replace the interactive superuser prompt + raw `psycopg2.connect(...)` for the secondary (lines ~33-50) with Django's existing secondary connection:

```python
        default_conn = connections[DEFAULT_DB_ALIAS]
        secondary_conn = connections[secondary_db_alias]
```

Remove the now-unused `getpass` import, the `secondary_user`/`secondary_password` prompts, and the `secondary_conn.close()` at the end (Django manages its own connections).

(c) Replace the inline table-count loop (lines ~126-136) with:

```python
        for table_name, primary_count, secondary_count in table_count_rows(default_conn, secondary_conn):
            self.stdout.write(f"{table_name:<30}{primary_count:<20}{secondary_count}")
```

Note: subscription/replication-slot queries that previously ran on the raw superuser connection now run on `connections[secondary_db_alias]` (the app secondary role). Reading `pg_subscription` / `pg_stat_replication` is fine for the app role on PG16+; if a particular catalog read is restricted in an environment, it surfaces as a normal query error rather than requiring a superuser prompt.

- [ ] **Step 4: Run the helper test to verify it passes**

Run: `pytest commcare_connect/multidb/tests/test_replication.py -k table_count -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Sanity-check the command parses**

Run: `python manage.py help logical_replication_status`
Expected: prints help with no import errors.

- [ ] **Step 6: Commit**

```bash
git add commcare_connect/multidb/management/commands/logical_replication_status.py \
  commcare_connect/multidb/tests/test_replication.py
git commit -m "Run replication status checks on app connections, no superuser prompt"
```

---

## Task 8: Full verification + documentation pointer

**Files:**
- Modify: `docs/replication-redesign-recommendation.md` (mark implementation status)

- [ ] **Step 1: Run the whole multidb test suite**

Run: `pytest commcare_connect/multidb/ -v`
Expected: PASS (all tests).

- [ ] **Step 2: Run Django system checks**

Run: `python manage.py check`
Expected: `System check identified no issues`.

- [ ] **Step 3: Confirm the old constant name is fully gone**

Run: `grep -rn "REPLICATION_ALLOWED_MODELS" .`
Expected: no output (ignore matches inside `.git/`).

- [ ] **Step 4: Run linters**

Run: `prek run -a`
Expected: all hooks pass (black, isort, flake8 line-length 119, etc.). Fix any formatting the hooks rewrite, then re-run.

- [ ] **Step 5: Add an implementation-status note to the recommendation doc**

At the top of `docs/replication-redesign-recommendation.md`, under the title, add:

```markdown
> **Implementation status (2026-06):** Code landed — twin lists + system
> check, `refresh_replication` command, bootstrap ownership transfer, and
> deploy wiring. **Not yet rolled out to production:** gated behind
> `REPLICATION_ENABLED=False` pending the secondary's Postgres 15 → 16
> blue/green upgrade (see "Postgres version prerequisite").
```

- [ ] **Step 6: Commit**

```bash
git add docs/replication-redesign-recommendation.md
git commit -m "Note replication redesign implementation status"
```

---

## Self-Review

**Spec coverage:**
- D1.B twin lists → Task 1; CI enforcement via system check → Task 2. ✓
- D2 deploy-pipeline trigger after `migrate_multi` → Task 6; in-sync short-circuit → Tasks 3-4. ✓
- D3.B bootstrap ownership transfer → Task 5; non-superuser recurring refresh → Task 4. ✓
- `REPLICATION_ENABLED` gating → Task 3. ✓
- Out-of-scope `logical_replication_status` rename + superuser removal → Tasks 1 & 7. ✓
- PG16 prerequisite handled by keeping `REPLICATION_ENABLED=False` (Tasks 3, 6, 8) — code merges safely; rollout waits. ✓
- Example "adding a new model" flow: system check fails locally/CI (Task 2) → deploy runs refresh after migrate (Tasks 4, 6). ✓

**Type/name consistency:** `get_replicated_models`, `get_replicated_tables`, `replication_is_active`, `publication_exists`, `is_publication_in_sync`, `refresh_publication`, `refresh_subscription`, `transfer_subscription_ownership`, `table_count_rows` — each defined once and referenced with the same signature throughout. `FakeConnection`/`FakeCursor` defined in `test_gating.py` and reused. `REPLICATION_INCLUDED_MODELS`/`REPLICATION_EXCLUDED_MODELS`/`PUBLICATION_NAME`/`SUBSCRIPTION_NAME` consistent across constants, replication, and commands.

**Known limitation (documented, intentional):** the in-sync short-circuit checks only the primary publication membership; if a prior deploy updated the publication but the subscriber `REFRESH` failed, the next deploy's short-circuit would skip the subscriber. Acceptable per YAGNI — a failed refresh fails the deploy step (`set -o errexit`), so the publication and subscriber move together in practice.
