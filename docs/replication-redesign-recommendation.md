# Auto-discovered logical replication — recommended design (detailed)

This document expands on the recommendation reached in
[`replication-redesign.md`](./replication-redesign.md).

> **Implementation status (2026-06):** Code landed — twin lists
> (`REPLICATION_INCLUDED_MODELS` / `REPLICATION_EXCLUDED_MODELS`) with a
> test-enforced classification guard over local app models, the
> non-interactive `refresh_replication` command, the bootstrap subscription
> ownership transfer, and the deploy wiring (`docker/start_migrate` runs
> `refresh_replication` after `migrate_multi`). Classification enforcement is
> a pytest (fails in CI), not a Django system check. **Not yet rolled out to
> production:** gated behind `REPLICATION_ENABLED=False` pending the
> secondary's Postgres 15 → 16 blue/green upgrade (see
> [Postgres version prerequisite](#postgres-version-prerequisite)).

## Recommended options (recap)

- **D1.B — maintain 2 lists defining replicated and non-replicated tables.** `REPLICATION_INCLUDED_MODELS` and
  `REPLICATION_EXCLUDED_MODELS` in `commcare_connect/multidb/constants.py`;
  a Django system check or pytest fails if any model is in neither list.
- **D2 — deploy-pipeline refresh.** A non-interactive `refresh_replication`
  command runs in the deploy pipeline, sequenced *after* `migrate_multi`
  returns (the only race-safe trigger — see D2 in the main doc).
- **D3.B — split lifecycle.** A one-time, privileged, operator-run
  **bootstrap** establishes the publication, subscription, roles, and
  *object ownership*; a recurring, non-interactive **refresh** reconciles
  the table set using only the ordinary app roles — no superuser in the
  recurring path.

> ⚠️ **Prerequisite:** A no-superuser refresh requires the secondary
> to run **Postgres 16+**. Production currently runs Postgres 15. See
> [Postgres version prerequisite](#postgres-version-prerequisite) below.

## The roles involved

The following table outlines *which role owns what and where*.

| Role | Where | Configured as | Purpose |
|------|-------|---------------|---------|
| **App primary role** | primary | `DATABASES["default"]["USER"]` (`RDS_USERNAME`) | The role Django/`migrate_multi` uses on the primary. Creates and therefore **owns** the app tables on the primary. After bootstrap, **owns the publication**. |
| **App secondary role** | secondary | `DATABASES["secondary"]["USER"]` (from `SECONDARY_DATABASE_URL`) | The role `migrate_multi` uses on the secondary. Creates and **owns** the app tables on the secondary. After bootstrap (PG16+), **owns the subscription**. |
| **`postgres_repl`** | primary | replication login role | The role the secondary's Postgres server connects *as* to pull changes. Needs `REPLICATION` attribute and `SELECT` on every published table. Its credentials are embedded in the subscription's `CONNECTION` string at bootstrap. |
| **`superset_readonly`** | secondary | read-only login role | The role Superset queries the secondary with. Needs `SELECT` on every replicated table. |
| **Secondary superuser** | secondary | operator-supplied at bootstrap | Used **only** at bootstrap to `CREATE SUBSCRIPTION` and transfer subscription ownership. Never persisted; never used by the refresh. |


## Split-lifecycle overview

| | **Bootstrap** | **Refresh** |
|---|---|---|
| Frequency | Once per environment | Every deploy |
| Invocation | Interactive, operator-run | Non-interactive, deploy pipeline |
| Privilege | Secondary superuser (primary half runs as the app role) | Ordinary app roles only |
| Creates objects? | Yes (`CREATE PUBLICATION`/`SUBSCRIPTION`, grants, subscription ownership transfer) | No — only `ALTER`s membership and grants `SELECT` on new tables |
| Credentials needed | Secondary superuser + `postgres_repl` creds for the subscription connection string | None beyond what Django already has |
| Command | Extend the existing `setup_logical_replication` | `refresh_replication` (new, extracted from it) |

---

## Phase 1 — Bootstrap (one-time, interactive, operator-run)

Runs once when an environment first turns on replication. It is allowed to
be privileged and interactive because it happens rarely and is performed by
an operator. Its job is to leave the system in a state where the **refresh
can run unprivileged forever after**.

### Reuse the existing command

Bootstrap is essentially **the existing
[`setup_logical_replication`](../commcare_connect/multidb/management/commands/setup_logical_replication.py)
command, extended with one new statement**: the transfer of subscription
ownership to the app secondary role. The existing command already

1. Creates the publication (using the same role as `migrate_multi`)
2. Creates the subscription (using superuser role)
3. Creates the subscription connection


The [`setup_logical_replication`](../commcare_connect/multidb/management/commands/setup_logical_replication.py) command will need to be updated to transfer ownership of the subscription object to the app secondary role (i.e. the role used for running `migrate_multi` on secondary DB).

```sql
ALTER SUBSCRIPTION tables_for_superset_sub OWNER TO <app_secondary_role>;
```

**Ownership outcome after bootstrap**

| Object | Owner | How |
|--------|-------|-----|
| `tables_for_superset_pub` (primary) | app primary role | already owned — `CREATE PUBLICATION` ran as the app role |
| `tables_for_superset_sub` (secondary) | app secondary role *(PG16+)* | **new** `ALTER SUBSCRIPTION ... OWNER TO` |
| app tables (primary) | app primary role | created by `migrate_multi` |
| app tables (secondary) | app secondary role | created by `migrate_multi` |

---

## Phase 2 — Refresh (recurring, non-interactive, deploy pipeline)

Runs on **every deploy**, after `migrate_multi` returns. It reconciles the
live publication/subscription with the desired table set computed from
`REPLICATION_INCLUDED_MODELS`. It uses **only the connections Django
already has**.

**Preconditions**

- `migrate_multi` has returned, so the new tables exist on **both** DBs.
- Bootstrap has been run once for this environment.

**Step 0 — in-sync short-circuit**

```python
if not settings.SECONDARY_DATABASE_URL:
  # no-op if no secondary database
  return

desired = {m._meta.db_table for m in get_replicated_models()}
# SELECT tablename FROM pg_publication_tables WHERE pubname = 'tables_for_superset_pub'
if desired == actual_publication_tables:
    return  # nothing changed this deploy
```

Most deploys don't touch models, so this exits immediately.

Note: the `setup_logical_replication` command is already able to decide whether a setup or a refresh should happen, so we could simply reuse this mechanism as well.


**Step 1 — publisher (primary, as the app primary role)**

```sql
-- Reconcile membership. SET TABLE is a full replace: adds new, drops removed.
ALTER PUBLICATION tables_for_superset_pub SET TABLE "t1", "t2", "t_new", ... ;

-- Let postgres_repl read the newly added tables (needed for the initial COPY).
GRANT SELECT ON ALL TABLES IN SCHEMA public TO postgres_repl;
```

**Step 2 — subscriber (secondary, as the app secondary role)**

```sql
-- Re-read the publication's table list: COPY+stream new tables, drop removed.
ALTER SUBSCRIPTION tables_for_superset_sub REFRESH PUBLICATION;

-- Let Superset read the newly arrived tables.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO superset_readonly;
```

## Example flow for adding a new model

1. Developer adds a new model in code.
2. The Django system check / test fails locally and in CI unless the model
   is listed in `REPLICATION_INCLUDED_MODELS` or `REPLICATION_EXCLUDED_MODELS`.
3. On deploy, after `migrate_multi` returns, `refresh_replication` runs:
   - no-op if `REPLICATION_ENABLED` is `False`;
   - no-op if the publication is already in sync;
   - otherwise: `ALTER PUBLICATION ... SET TABLE` + grant on the primary,
     then `ALTER SUBSCRIPTION ... REFRESH PUBLICATION` + grant on the
     secondary — all as the ordinary app roles.



## Postgres version prerequisite

The solution above, i.e. "no superuser in the recurring path", depends on the
subscription being **owned by the non-superuser app secondary role**. That
ownership is only legal on **Postgres 16+**:

- **PG ≤15:** a subscription's owner *must* be a superuser, so
  `ALTER SUBSCRIPTION ... OWNER TO <app_role>` cannot transfer ownership to
  an ordinary role. The PG15
  [`ALTER SUBSCRIPTION`](https://www.postgresql.org/docs/15/sql-altersubscription.html)
  reference states it directly in its Description:

  > You must own the subscription to use `ALTER SUBSCRIPTION`. To alter the
  > owner, you must also be a direct or indirect member of the new owning
  > role. **The new owner has to be a superuser.** (Currently, all
  > subscription owners must be superusers, so the owner checks will be
  > bypassed in practice. But this might change in the future.)

- **PG 16+:** the "this might change in the future" above is exactly what
  PG16 delivered — it introduced the
  [`pg_create_subscription`](https://www.postgresql.org/docs/16/predefined-roles.html)
  predefined role and non-superuser subscription ownership, so an ordinary
  app role can own the subscription and drive `REFRESH`.


**Current state:** the production secondary runs **Postgres 15** and is therefore
**not implementable as-is today**.

**Plan to unblock:** upgrade the secondary to Postgres 16+ via a
**blue/green** approach — stand up the PG16 instance in parallel, validate
replication against it, cut over once confirmed, and decommission the PG15
instance. D3.B can only be rolled out *after* that cutover; until then the
subscriber refresh still requires a superuser (D3.A behaviour).

## Breakdown of implementation

1. **Add parallel secondary postgres instance (16+)** - Stand up a new instance of Postgres (16+) in parallel as a new replication DB. Once replication is working correctly we can remove the older replication DB.
2. **Update existing command** - Update the `setup_logical_replication` command:
    - Transfer the secondary database ownership to the `SECONDARY_DATABASE_URL` role so steady-state refreshes can be made using this role.
    - The `setup_logical_replication` already checks if there's replication set up and if so refreshes, otherwise create the replication. We should make sure the refresh path uses the secondary DB role to perform the refresh action.