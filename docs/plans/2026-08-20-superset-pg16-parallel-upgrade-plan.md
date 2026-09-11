# Superset PG16 Parallel Upgrade — Implementation Plan

**Goal:** Stand up a new PostgreSQL 16 RDS instance, populate it from the Connect primary by
logical replication, validate it while 15.17 keeps serving, then cut Superset and Connect over.

---

## Before Starting

**Two rules**, both about things the steps cannot enforce for you:

1. **The primary is read-only** except where flagged: the `GRANT SELECT` and
   `ALTER PUBLICATION ... SET TABLE` that `setup_logical_replication` issues (Task 4), the canary row
   (Task 7), the slot cleanup (Task 11), and the publication rewrite Task 13's `REFRESH` exercise
   triggers. **Never invoke `migrate_multi` by hand** — it migrates every configured database, the
   primary included. Deploys run it normally; that is fine, and expected at Task 10.
2. **Both subscribers must receive every migration during the overlap.** `migrate_multi` migrates
   `default` plus *the one* configured secondary, so whichever instance the config does not name
   drifts. For the whole overlap, either hold migrations that touch `REPLICATION_ALLOWED_MODELS`, or
   run `./manage.py migrate --database=<alias>` against **both** instances after each deploy that
   carries one. Check Task 7's counters after any such deploy.

**Connection helper.** Subscription DDL needs autocommit as a specific role. `new` is the PG16
instance, `old` is 15.17, and Django's `connections["default"]` is the primary:

```python
import psycopg2, getpass
def conn(host, dbname, user, password=None, autocommit=True):
    c = psycopg2.connect(host=host, port=5432, dbname=dbname, user=user,
                         password=password or getpass.getpass(f"{user}@{host}: "),
                         sslmode="require")
    c.autocommit = autocommit
    return c

def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        try: return cur.fetchall()
        except psycopg2.ProgrammingError: return None
```

**Verified production facts:**

| Fact | Value |
|---|---|
| Superset version | **5.0.0**, supports PG16 |
| Metadata database | **`superset_meta`**, colocated with analytics on the old instance |
| PostGIS | **3.4.3, owner `rdsadmin`, in BOTH `superset` and `superset_meta`.** The new instance must offer ≥ 3.4.3, and **`superset_meta` needs the extension created before the restore** (Task 9). No `address_standardizer*` anywhere. |
| Publication coverage | **29/29, no drift** |
| Replica identity | **All 29 tables clean** — primary key on each, `relreplident = 'd'` |
| Subscription owner | **`postgres`**, the RDS master user — member of `rds_superuser` |
| Replication role | **`postgres_repl`**, credential held and verified. `rolreplication = false`; RDS grants it via `rds_replication` membership. |

---

## Phase 1 — Build the new instance

*Nothing here affects anything live.*

### Task 1: Provision

One console form. Engine **16.15** (ships PostGIS 3.4.6). Same VPC and security group as the current
secondary, so the primary is reachable. Instance class at least as large as the current one — the
initial `COPY` is the heaviest work it will ever do. Automated backups on.

Then confirm connectivity and PostGIS from the app host:

```python
new = conn("<new-endpoint>", "postgres", "postgres")
print(q(new, "SELECT version();"))
print(q(new, "SELECT name, default_version FROM pg_available_extensions WHERE name = 'postgis';"))
```

And from the Superset host, which reaches the database by a different route and is the one that
breaks silently at cutover if the security group is wrong:

```bash
psql "postgresql://postgres@<new-endpoint>:5432/postgres?sslmode=require" -c "SELECT 1;"
```

### Task 2: Create the roles

Create them **as `postgres`**: PG16 auto-grants the creator `ADMIN OPTION`, which is what the apply
worker's `SET ROLE` needs. On the old instance nobody could `SET ROLE` to `connect_superset` and it
took a one-way grant to fix; here it comes free.

Create the analytics database with the **same name as on the old instance (`superset`)**, so only
the host changes at cutover — Superset's stored connection and `SECONDARY_DATABASE_URL` then differ
in one component rather than two.

```python
new = conn("<new-endpoint>", "superset", "postgres")
q(new, "CREATE ROLE connect_superset LOGIN PASSWORD '<pw>';")
q(new, "CREATE ROLE superset_readonly LOGIN PASSWORD '<pw>';")
q(new, "GRANT CREATE ON DATABASE superset TO connect_superset;")
q(new, "GRANT USAGE, CREATE ON SCHEMA public TO connect_superset;")
q(new, 'GRANT "connect_superset" TO "postgres";')

print(q(new, "SELECT has_schema_privilege('connect_superset','public','CREATE');"))   # expect True
```

**The schema grant is not optional.** `GRANT CREATE ON DATABASE` confers the right to create
*schemas*, not tables in `public`. Since PG15 the `public` schema is owned by `pg_database_owner`
and no longer carries `CREATE` for `PUBLIC`, so without that grant Task 4's migration fails with
`permission denied for schema public`.

---

## Phase 2 — Start replication

### Task 3: Add a `--subscription-name` option to `setup_logical_replication` `[CODE]`

The command creates a subscription with no explicit `slot_name`, so Postgres names the slot after
the subscription. The two subscribers are on different instances and their *subscription* names
would not clash — but they share one primary, and the old subscriber's **slot** there is already
called `tables_for_superset_sub`. Run as it stands, the command fails with *replication slot already
exists*.

**Take the name as an argument rather than renaming the constant.** A rename would leave the code
referring to a subscription that does not exist on the live instance — `logical_replication_status`
would report it missing, and anyone running `setup_logical_replication` against the old instance
would create a *second* subscription alongside the live one. Arguments avoid all of that: the
constants keep describing what is actually deployed, and the overlap is expressed at the call site.

**Files:** `setup_logical_replication.py`, `logical_replication_status.py`, `constants.py`

1. **Add `--subscription-name` to `setup_logical_replication`**, defaulting to
   `SUBSCRIPTION_NAME`.

2. **Add the same option to `logical_replication_status`** so it can inspect either subscriber
   during the overlap.

3. **Dedupe the constants.** `PUBLICATION_NAME` and `SUBSCRIPTION_NAME` are defined **twice** —
   `constants.py:28-29` (dead; nothing imports it) and `setup_logical_replication.py:10-11` (live,
   and what `logical_replication_status` imports). Delete the second pair and import from
   `constants`.

4. **While in that file:** `setup_logical_replication` calls `psycopg2.sql.SQL(...)` after only
   `import psycopg2`. It works because Django's stack imports the submodule first. Add
   `from psycopg2 import sql`.

Lint, then `pytest commcare_connect/multidb/ -q`.

**This change is safe to deploy at any time.** Defaults preserve current behaviour exactly, and
nothing runs the command unattended — `docker/start_migrate` runs only `migrate_multi`.

**After decommission (Task 12), update `SUBSCRIPTION_NAME` to `tables_for_superset_sub_v2`** so the
constant again describes the only subscriber that exists. The `_v2` name is permanent — slots cannot
be renamed, so reverting would require another rebuild.

### Task 4: Create the subscription `[GATE]`

This is where the one open privilege question gets answered. If `CREATE SUBSCRIPTION` is refused,
stop — the old subscriber is untouched and in-place is the remaining option.

**Pre-flight:**

```python
print(q(new, "SELECT pg_has_role('postgres','pg_create_subscription','USAGE');"))   # expect True
```

Confirm Task 3 is deployed where you are running from — `./manage.py setup_logical_replication
--help` should list `--subscription-name`.

**Point `SECONDARY_DATABASE_URL` at the new instance**, using the **`connect_superset`**
credentials, and build the schema. The tables must exist before subscribing — `copy_data = true`
fails during tablesync against a missing table — and creating them as `connect_superset` gives the
uniform ownership PG16's apply worker requires.

```bash
./manage.py migrate --database=<SECONDARY_DB_ALIAS>
```

```python
print(q(new, "SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1;"))
```

Expect `connect_superset` only, plus `rdsadmin` for `spatial_ref_sys`, roughly 91 tables.

**Record the primary's row counts** with a timestamp, so the copy can be verified later:

```python
from django.db import connections
from commcare_connect.multidb.constants import REPLICATION_ALLOWED_MODELS
cur = connections["default"].cursor()
for m in REPLICATION_ALLOWED_MODELS:
    cur.execute(f"SELECT count(*) FROM {m._meta.db_table};")
    print(m._meta.db_table, cur.fetchone()[0])
cur.close()
```

**Run it in a low-traffic window** — the copy starts immediately.

```bash
./manage.py setup_logical_replication --subscription-name tables_for_superset_sub_v2
```

Interactive: answer `yes`; accept `postgres_repl` on the primary; supply the new instance's
superuser credentials; supply the primary replication credentials; accept `superset_readonly`.

It grants `SELECT` to the replication user on the **primary**, issues `ALTER PUBLICATION ... SET
TABLE` there with `main`'s 29 models, creates the subscription with `copy_data = true`, and grants
`SELECT` to `superset_readonly`.

**That publication write rewrites what the old subscriber reads from.** It is a no-op only because
the published set already matches `main`'s 29 models. If it has drifted, this silently removes
tables and *both* subscribers stop syncing them — re-confirm the count first.

Then confirm two slots exist on the primary, both active:

```python
from django.db import connections
cur = connections["default"].cursor()
cur.execute("""SELECT slot_name, active, pg_size_pretty(
                        pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
               FROM pg_replication_slots;""")
print(cur.fetchall()); cur.close()
```

**Finally, revert `SECONDARY_DATABASE_URL` to the old instance** — the subscription is created and
the config has no further part to play until cutover. Leaving it pointed at the new instance cuts
Connect over silently, before any of Phase 3's validation has run, and the next deploy would
`migrate_multi` against it. The old instance stays the configured secondary, and the rollback stays
real, until Task 10.

### Task 5: Watch the initial copy `[RISK]`

`opportunity_uservisit` is large and the sync holds a slot open on the primary while reading it. A
stalled copy accumulates WAL there with real disk-fill risk. Watch both sides, and **record the
duration** — it is the number nobody has.

```python
print(q(new, "SELECT srrelid::regclass, srsubstate FROM pg_subscription_rel ORDER BY 2, 1;"))
```

States go `i` → `d` → `r`. Retained WAL on the primary should rise during the copy and fall after.

**Bail-out if retained WAL threatens the primary's disk:**

```python
q(new, "ALTER SUBSCRIPTION tables_for_superset_sub_v2 DISABLE;")
q(new, "ALTER SUBSCRIPTION tables_for_superset_sub_v2 SET (slot_name = NONE);")
q(new, "DROP SUBSCRIPTION tables_for_superset_sub_v2;")

cur = connections["default"].cursor()
cur.execute("SELECT slot_name, active FROM pg_replication_slots WHERE slot_name = 'tables_for_superset_sub_v2';")
print(cur.fetchall())            # proceed only when active is False
cur.execute("SELECT pg_drop_replication_slot('tables_for_superset_sub_v2');")
cur.close()
```

**Every name there is the `_v2` one.** `tables_for_superset_sub` is the *old* subscriber; naming it
here is how this bail-out fails at the one moment it is needed. `pg_drop_replication_slot` also
refuses while the slot is `active`. The old subscriber is unaffected — retry in a quieter window.

### Task 6: Superset's read access

The command granted `SELECT ON ALL TABLES` but not `USAGE ON SCHEMA`, without which
`superset_readonly` cannot read anything:

```python
q(new, "GRANT USAGE ON SCHEMA public TO superset_readonly;")
print(q(new, "SELECT has_table_privilege('superset_readonly','opportunity_uservisit','SELECT');"))
```

---

## Phase 3 — Validate, with nothing at stake

*15.17 is still serving. No time pressure — this phase is what makes the whole approach worthwhile.*

### Task 7: Verify replication is genuinely live

```python
print(q(new, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;"))   # ('r', 29)
print(q(new, "SELECT * FROM pg_stat_subscription_stats;"))                          # 0 | 0
```

Any table at `d` means its copy is stuck; at `i`, it never started; an empty result means the
subscription has no tables at all. **Zero errors alone is not health** — that counter reads zero on
a subscription applying nothing, which is exactly the failure this phase exists to catch.

Then, in order of increasing value:

1. **Row counts** against the Task 4 baseline, then **sample again minutes later and confirm they
   moved**. A single sample cannot tell a frozen table from a current one.
2. **Canary:** insert a synthetic row into one replicated table, confirm it arrives, then delete it
   and confirm the delete arrives too. Log both statements. The most informative check here, and
   this phase's one write to the primary. `DATA` *(primary, one row, reversed)*
3. **Real dashboards:** point a Superset instance at the new analytics database read-only and run
   the dashboards people actually use. Counts agreeing is necessary; the real queries returning the
   real numbers is what matters.

---

## Phase 4 — Cutover

*The only window. Minutes.*

### Task 8: Announce and freeze deploys

Deploys run `migrate_multi` against whichever instance the config names, so one landing mid-cutover
is a genuine hazard. Name who enforces the freeze and who lifts it.

### Task 9: Move Superset's metadata

**Stop Superset before the dump and leave it down until Task 10 repoints it.** The Task 8 freeze
covers deploys, not users: anything edited in between lands in the *old* `superset_meta` and is lost,
and anything created after cutover is missing from 15.17 if you roll back.

```bash
pg_dump -h <old-endpoint> -U postgres -d superset_meta -Fc -f superset_meta.dump
```

**Read the ownership off the old instance rather than guessing it** — the dump carries
`ALTER ... OWNER` for every object, and any owning role missing on the new instance leaves that
object owned by `postgres`. Run as `postgres`; `connect_superset` is denied `CONNECT` here:

```python
old_meta = conn("<old-endpoint>", "superset_meta", "postgres")
print(q(old_meta, "SELECT datdba::regrole FROM pg_database WHERE datname='superset_meta';"))
print(q(old_meta, "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname='public';"))
```

Create any of those roles the new instance lacks, then the database and the extension —
`superset_meta` carries `postgis` 3.4.3, without which the restore fails on PostGIS-dependent
objects:

```python
maint = conn("<new-endpoint>", "postgres", "postgres")
q(maint, "CREATE DATABASE superset_meta OWNER <datdba from above>;")   # not inside a transaction
maint.close()

meta = conn("<new-endpoint>", "superset_meta", "postgres")
q(meta, "CREATE EXTENSION IF NOT EXISTS postgis;")
```

```bash
pg_restore -h <new-endpoint> -U postgres -d superset_meta superset_meta.dump
```

**Do not use `--no-owner`** to paper over a missing role — it makes `postgres` own everything, which
holds until Superset's next Alembic migration. `pg_restore` continues past errors and exits `1` if it
ignored any, so read the error list rather than the exit code: the `postgis` owner and comment
statements fail benignly, since `rdsadmin` owns it on both instances. Then compare table counts and
owners against the old database.

**Keep Superset's `SECRET_KEY` unchanged**, or the encrypted stored database passwords will not
decrypt. Do not combine this with a Superset redeploy.

### Task 10: Repoint and verify

Superset: update `SQLALCHEMY_DATABASE_URI` and its stored analytics connection, restart, confirm
logins and dashboards.

Connect: update `SECONDARY_DATABASE_URL`, lift the freeze, deploy, confirm `migrate_multi` succeeds
against the new secondary. Re-run Task 7 post-cutover, and open a saved Superset chart that queries
the analytics database — that is what proves the `SECRET_KEY` and metadata move were clean.

---

## Phase 5 — Decommission

*Promptly, but not immediately: two subscriptions roughly double WAL retention and decode work on
the primary, while the old instance is still the rollback. Agree a date in advance.*

### Task 11: Release the old subscriber and its slot

```python
old = conn("<old-endpoint>", "superset", "postgres")
q(old, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
q(old, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
q(old, "DROP SUBSCRIPTION tables_for_superset_sub;")
```

`SET (slot_name = NONE)` deliberately orphans the slot on the primary, and it retains WAL forever
until dropped:

```python
cur = connections["default"].cursor()
cur.execute("SELECT active FROM pg_replication_slots WHERE slot_name = 'tables_for_superset_sub';")
print(cur.fetchall())                       # drop only once this reads False
cur.execute("SELECT pg_drop_replication_slot('tables_for_superset_sub');")
cur.execute("""SELECT slot_name, active, pg_size_pretty(
                        pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
               FROM pg_replication_slots;""")
print(cur.fetchall()); cur.close()          # expect exactly one slot, the _v2 one
```

The walsender can still be shutting down seconds after `DROP SUBSCRIPTION`, and
`pg_drop_replication_slot` refuses on an active slot. Nothing is at risk if it does — wait and
re-run.

**After this step the rollback no longer exists.**

### Task 12: Retire the old instance

Confirm subscription and slot still share a name — the invariant `setup_logical_replication` assumes:

```python
print(q(new, "SELECT subname, subslotname FROM pg_subscription;"))   # both tables_for_superset_sub_v2
```

Final snapshot, then delete. Record the snapshot identifier and retention.

**For any future rebuild:** pass a new `--subscription-name`. A rebuild cannot reuse a name while a
subscriber still holds that slot, and slots cannot be renamed.

### Task 13: Close out

Record the copy duration, the cutover duration, and whether RDS granted `pg_create_subscription` —
all previously unknown. Fold them into the investigation doc.

**One untested path remains:** the REFRESH branch of `setup_logical_replication` has never run
against a real subscriber under the `_v2` name. Exercise it once deliberately — add a model to
`REPLICATION_ALLOWED_MODELS` and re-run the command — rather than discovering it during an urgent
change. Confirm the slot is still `tables_for_superset_sub_v2` afterwards.

---

## Rollback

**Before Task 11:** repoint `SECONDARY_DATABASE_URL` and Superset's config back at 15.17, which has
been running untouched with its subscription intact. This is the reason for choosing this shape.

**After Task 11:** there is no rollback. The old subscription is dropped and its data is stale from
that moment.

## Abort criteria

| Trigger | Action |
|---|---|
| Task 4 — `CREATE SUBSCRIPTION` refused | Stop. Nothing has changed anywhere. Fall back to in-place. |
| PostGIS unavailable at ≥ 3.4.3 on the new instance | Stop at Task 1, before any further investment. |
| Publication has fewer than 29 tables | Fix the drift first, or the new instance inherits the gap. |
| Initial `COPY` stalls and WAL builds on the primary | Task 5 bail-out. The old subscriber is unaffected. |
| Any table outside `r`, or non-zero error counters | Do not cut over. 15.17 keeps serving during diagnosis. |
| Apply errors after a deploy carrying a migration | The subscriber missed it (rule 2). Migrate that instance, then confirm apply resumes. |
| Row counts disagree after the copy | Do not cut over. The copy can be redone. |
| Superset charts broken after the metadata restore | Repoint Superset back to 15.17; its metadata there is untouched. |
