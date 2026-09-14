# Superset PG16 In-Place Upgrade — Implementation Plan

**Goal:** Upgrade the Superset/analytics RDS instance from PostgreSQL **15.17 to 16.13** in place,
and reconnect logical replication from the Connect primary afterwards.

---

## Before Starting

**Three ways to break this permanently, all of them silent:**

1. **Task 1's `GRANT` must land before the upgrade.** It is legal on PG15, refused on PG16, and no
   role you can log in as can apply it afterwards. Skip it and all 29 tables freeze permanently.
2. **Never run `ALTER SUBSCRIPTION ... ENABLE` before Task 8's probe.** Enabling with wiped
   subscriber state gives a subscription that reports `apply_error_count = 0` and applies nothing
   forever, while advancing the slot past the backlog. Verified.
3. **Never pass `--recreate` to `setup_logical_replication`.** It drops the subscription and
   re-creates it without a slot name, colliding with the orphaned slot it just detached — failing
   *after* the subscription is gone.

**Connection helper.** Several statements need true autocommit as a specific role — `postgres`,
the subscriber's master user. Pass `dbname="superset_meta"` to reach the metadata database:

```python
import psycopg2, getpass
from django.conf import settings
from django.db import connections

def secondary(user="postgres", password=None, dbname=None, autocommit=True):
    s = connections[settings.SECONDARY_DB_ALIAS].settings_dict
    c = psycopg2.connect(host=s["HOST"], port=s["PORT"], dbname=dbname or s["NAME"],
                         user=user, password=password or getpass.getpass(f"{user}: "),
                         sslmode="require")
    c.autocommit = autocommit
    return c

def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        try: return cur.fetchall()
        except psycopg2.ProgrammingError: return None
```

**Verified production facts this plan is built on** (2026-08-12 and 2026-08-20):

| Fact | Value |
|---|---|
| Secondary version | 15.17. |
| **Target engine** | **16.13** — *not* `16.13-R2`. Chosen because it ships PostGIS **3.4.3**, the version already installed, so no extension update is needed and no precheck version mismatch arises. |
| Databases on the instance | `ccc_analytics`, `ccc_analytics_old`, `postgres`, `rdsadmin`, `superset`, `superset_meta` |
| **Analytics (replicated) database** | **`superset`** — from `SECONDARY_DATABASE_URL`. |
| **Superset metadata database** | **`superset_meta`** |
| PostGIS | **3.4.3, owner `rdsadmin`, in BOTH `superset` and `superset_meta`**. Already the newest version PG15 offers — nothing to update pre-upgrade. |
| Table ownership | all 91 tables `connect_superset`, except `spatial_ref_sys` (`rdsadmin`) |
| Superset version | **5.0.0**. Supports PG16; no compatibility test needed. |
| Superset sessions | filesystem (`flask_session/`) — **a restart logs everyone out**; say so in the downtime comms |
| Metadata colocation | **confirmed** — live backends on both `superset` (2) and `superset_meta` (5), so the upgrade takes Superset itself down, not just analytics freshness. `superset_meta` is the half worth dumping (Task 5). |
| Replication role (primary) | **`postgres_repl`** — verified 2026-08-27 as the role the live subscriber authenticates with (`pg_stat_replication.usename`, `application_name = tables_for_superset_sub`). `rolreplication = false`, because RDS grants replication through **`rds_replication` membership** rather than the attribute. |
| Subscription owner | `postgres` — `rolsuper = false`, `rolcreaterole = true`, **member of `rds_superuser`** |
| Members of `connect_superset` | **none** — the Task 1 grant is required |
| `max_slot_wal_keep_size` (primary) | **`-1`** — the slot is never invalidated |
| Slot state | one slot, active, 56 bytes retained, no backlog |
| Publication vs subscription | **no drift** — 29 published, 29 at `srsubstate = 'r'` |

---

## Phase 1 — Pre-flight on PG15

Nothing here requires a specific time window.

### Task 1: Apply the one-way GRANT

**Why:** PG16's apply worker `SET ROLE`s to each replicated table's owner. The subscription is owned
by `postgres`; the tables are owned by `connect_superset`; nothing is a member of `connect_superset`.
On PG15 `CREATEROLE` implies the right to grant membership in any non-superuser role, so `postgres`
can grant this today. PG16 removed that implication — a `CREATEROLE` role may only administer roles
it created or holds `ADMIN OPTION` on — and `rdsadmin`, though flagged `rolcanlogin`, is an
AWS-internal role whose credentials nobody outside AWS holds. **After the upgrade there is no path
to apply it.** Both halves verified in Docker.

**Who can run this:** The instance has exactly two loginable privileged
roles: `postgres` (the RDS master user) and `rdsadmin` (AWS-internal, unusable). Django connects as
`connect_superset`, which has neither `CREATEROLE` nor superuser. **`postgres` is the only role that
can apply this**.

**Step 1: Confirm the precondition**

```python
from django.db import connections
with connections["secondary"].cursor() as c:
    c.execute("""SELECT grantee.rolname, m.admin_option
                 FROM pg_auth_members m
                 JOIN pg_roles target  ON target.oid  = m.roleid
                 JOIN pg_roles grantee ON grantee.oid = m.member
                 WHERE target.rolname = 'connect_superset';""")
    print(c.fetchall())
```
The expected output here is `[]`.

**Step 2: Apply it as `postgres`.**

```python
conn = secondary(user="postgres")
q(conn, 'GRANT "connect_superset" TO "postgres";')
```

If this errors *on PG15*, stop and escalate — it only becomes harder on 16.

**Step 3: Verify.** Re-run Step 1; it must no longer be empty. `admin_option = False` is fine —
`INHERIT` membership is what the apply worker needs, and it survives `pg_upgrade` (verified).

---

### Task 2: Baselines and credentials

**Step 1: Row counts both sides**

```bash
./manage.py logical_replication_status
```

Interactive; prompts for secondary superuser credentials. Without this baseline there is no way to
prove afterwards that nothing froze — frozen tables keep plausible counts.

**Step 2: Confirm no publication drift persists** (verified clean 2026-08-20, re-check on the day):

```python
with connections["default"].cursor() as c:
    c.execute("SELECT count(*) FROM pg_publication_tables WHERE pubname='tables_for_superset_pub';")
    print(c.fetchone())          # expect 29
with connections["secondary"].cursor() as c:
    c.execute("SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")
    print(c.fetchall())          # expect [('r', 29)]
```

**Step 3: Record** the baseline counts and the drift check.

---

## Phase 2 — Scheduling gates

### Task 3: Backups

Confirm backup retention > 0, so RDS takes an automatic pre-upgrade snapshot:

```bash
aws rds describe-db-instances --db-instance-identifier <instance-id> \
  --query 'DBInstances[].[BackupRetentionPeriod,PreferredBackupWindow,DBInstanceStatus]' --output table
```

A retention of `0` means automated backups are **off** and no pre-upgrade snapshot is taken — enable
it before proceeding. The manual snapshot is taken in the window, at Task 5 Step 4.

### Task 4: Agree the window and the deploy freeze

**Step 1:** Agree the outage window.

**Step 2:** Ensure no deploys happen during outage window.

**Step 3: Agree on the re-copy fallback** so the upgrade path is not blocked mid-window. If the
probe (Task 8) sends you to Task 10, all 29 tables are truncated and re-copied from the primary.
Agree *now* that this is acceptable, and record it. What the approver is signing off:

- **Superset serves empty tables until the copy finishes** — blank dashboards, not stale ones.
- **Sustained read load on the Connect primary** for the duration; `opportunity_uservisit` is the
  largest of the 29.

Unlikely to be needed — `max_slot_wal_keep_size = -1` means the slot survives, so the fast path
stays available — but worth agreeing in advance.

---

## Phase 3 — The window

### Task 5: Announce and freeze

**Step 1:** Post the downtime notice.

**Step 2:** Activate the deploy freeze.

**Step 3:** Dump Superset's metadata — the only irreplaceable data on the instance, and the rollback
artifact if Superset breaks after the upgrade:

```bash
pg_dump -h <endpoint> -U postgres -d superset_meta -Fc -f superset_meta_window_start.dump
```

**Step 4:** Take the manual snapshot, at the true starting state, and wait for it:

```bash
aws rds create-db-snapshot --db-instance-identifier <instance-id> \
  --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1

aws rds wait db-snapshot-completed \
  --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1
```

**Do not start the upgrade until the wait returns** — a snapshot still in `creating` is not a
rollback. **Record the identifier**; Phase 5 refers to it. Bump the `-1` on a same-day retry, since
RDS refuses a duplicate.

**Step 5:** Record timestamps.

### Task 6: Disable the subscription

```python
conn = secondary(user="postgres")
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
print(q(conn, "SELECT subname, subenabled FROM pg_subscription;"))   # expect False
```

**Then record the slot position on the primary** — that is where the backlog replays from:

```python
from django.db import connections
cur = connections["default"].cursor()
cur.execute("""SELECT slot_name, active, restart_lsn, confirmed_flush_lsn,
                      pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
               FROM pg_replication_slots;""")
print(cur.fetchall())
cur.close()
```

**Record the `restart_lsn` and the wall-clock time.** Re-run this query periodically through the
window: `retained` is the WAL the primary is holding on the slot's behalf, and it only stops growing
once the subscription is re-enabled. `max_slot_wal_keep_size = -1` means the slot will **not** be
dropped to protect the disk, so **watch primary free space** rather than assuming it self-limits.

### Task 7: Run the engine upgrade

**Step 1:** RDS console → Modify → engine version **16.13** → apply immediately. Not `16.13-R2`.
**Step 2:** Wait. **Record start and end times**.
**Step 3:** Confirm the engine reports 16.13:

```python
conn = secondary(user="postgres")
print(q(conn, "SELECT version();"))
```

**Step 4: Verify PostGIS survived — no update, in either database.**

```python
for db in ("superset", "superset_meta"):
    c = secondary(user="postgres", dbname=db)
    print(db, q(c, """SELECT e.extversion, a.default_version
                     FROM pg_extension e
                     JOIN pg_available_extensions a ON a.name = e.extname
                     WHERE e.extname = 'postgis';"""))
    c.close()
```

Expect `[('3.4.3', '3.4.3')]` for both — installed and available matching, so there is nothing to
update.

If `default_version` unexpectedly reads 3.4.6, the wrong minor was applied (likely `16.13-R2`).
PostGIS still works at 3.4.3 on PG16 — do **not** treat it as a rollback trigger; note it and carry
the update as a follow-up.

**Step 5:** Confirm geometry still queries:

```python
print(q(conn, """SELECT ST_AsText(ST_Centroid(geometry))
                 FROM microplanning_workarea WHERE geometry IS NOT NULL LIMIT 1;"""))
```

### Task 8: The probe — before anything destructive

**Step 1: Confirm the expected post-upgrade state**

```python
conn = secondary(user="postgres")
print(q(conn, "SELECT subname, subenabled FROM pg_subscription;"))     # expect False
print(q(conn, "SELECT count(*) FROM pg_subscription_rel;"))            # expect 0 -- wiped
```

**Step 2: Probe**

```python
print(q(conn, "SELECT pg_has_role('postgres','pg_create_subscription','USAGE');"))
print(q(conn, "SELECT has_database_privilege(current_user, current_database(), 'CREATE');"))
```

**Both must be `True`.** PG16 gates `CREATE SUBSCRIPTION` on two things, not one:
`pg_create_subscription` membership *and* `CREATE` on the current database. Task 9 opens by
dropping the subscription, so attempting it on a `False` strands the instance with no way to
reconnect — snapshot restore only. Treat either `False` as a `False` overall.

**Step 3: Branch, and write the branch into the log before acting.**

- Both `True` → Task 9 (replay from the slot, no re-copy)
- Either `False` → Task 10 (rebuild). Do **not** attempt Task 9.

### Task 9: Replay from the retained slot (probe returned `True`)

The slot is intact — `max_slot_wal_keep_size = -1` guarantees it was never invalidated — so the whole
outage backlog replays with no re-copy.

**Step 1: Detach and drop**

```python
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
q(conn, "DROP SUBSCRIPTION tables_for_superset_sub;")
```

**Step 2: Re-create against the retained slot, not copying**

```python
q(conn, """CREATE SUBSCRIPTION tables_for_superset_sub
             CONNECTION 'host=<primary> port=5432 dbname=<db> user=postgres_repl password=<pw> sslmode=require'
             PUBLICATION tables_for_superset_pub
             WITH (copy_data = false,
                   slot_name = 'tables_for_superset_sub',
                   create_slot = false,
                   enabled = false);""")
```

**If `create_slot = false` is refused** (unverified on RDS), fall through to Step 4.

**Step 3: Enable and let it replay**

```python
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")
```

Expected: `pg_subscription_rel` populates at state `r` with no copying, and the backlog replays.
Go to Phase 4.

**Step 4: Only if Step 2 was refused.** Copy instead of replaying. Order matters — the slot goes
first.

**4a. Drop the retained slot on the primary.** Step 1 detached it deliberately, so it is still there
under the subscription's name. Re-creating without `slot_name` / `create_slot` makes Postgres try to
create a slot by that name and fail with `replication slot ... already exists` — the same collision
that makes `--recreate` unsafe, again after the subscription is gone.

```sql
-- on the primary: confirm inactive, then drop
SELECT slot_name, active FROM pg_replication_slots WHERE slot_name = 'tables_for_superset_sub';
SELECT pg_drop_replication_slot('tables_for_superset_sub');
```

**4b. Truncate the 29 tables** — copying into populated tables fails with `duplicate key value
violates unique constraint`, so this is mandatory.

```python
q(conn, "SET ROLE connect_superset; TRUNCATE <the 29 replicated tables>;")
q(conn, "RESET ROLE;")   # session-scoped; left set, the DDL below runs as connect_superset and fails
```

**4c. Re-create as in Step 2**, with `copy_data = true` and no `slot_name` / `create_slot`, then
enable it — that template carries `enabled = false`, and Step 3 is above this one, so a subscription
created here replicates nothing until you say so.

```python
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")
```

### Task 10: Rebuild via the management command (probe returned `False`)

Verified end to end in Docker under prod-shaped roles. Needs only subscription ownership.

**Step 1: Enable** — required, `REFRESH` is refused on a disabled subscription (verified):

```python
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")
```

**Step 2: Truncate** — mandatory, and skipping it fails *silently*: `REFRESH` with `copy_data = true`
into populated tables returns no error while every tablesync loops on duplicate keys.

```python
q(conn, "SET ROLE connect_superset; TRUNCATE <the 29 replicated tables>;")
q(conn, "RESET ROLE;")   # Step 4 sends you back here; leave it set and the later DDL fails
```

Table list: `commcare_connect/multidb/constants.py` → `REPLICATION_ALLOWED_MODELS`.

**Step 3: Run the command, no flags**

```bash
./manage.py setup_logical_replication
```

It takes the REFRESH branch (the subscription exists), sets autocommit itself, and re-grants
`superset_readonly` its SELECTs. Its `ALTER PUBLICATION ... SET TABLE` against the primary is a
no-op — Task 2 Step 2 confirmed the published set matches. **Never `--recreate`.**

**Step 4: Watch the copy.** States progress `i` → `d` → `r`; errors stay `0|0`. Superset serves
**empty** tables until this completes. **Do not trust the command's output** — it reports success
regardless. A table parked at `d` means the truncate missed it; truncate it now and it self-heals.

### Task 11: Lift the deploy freeze

Only after Phase 4 passes. Run a real deploy and confirm `migrate_multi` succeeds against the 16
secondary.

---

## Phase 4 — Verification

The failure mode reports success, so no single check suffices.

**Task 12: Structural**

```python
print(q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;"))  # ('r', 29)
print(q(conn, "SELECT * FROM pg_stat_subscription_stats;"))                          # 0 | 0
print(q(conn, """SELECT tableowner, count(*) FROM pg_tables
                 WHERE schemaname='public' GROUP BY 1;"""))    # connect_superset + rdsadmin
```

Expected: `connect_superset` for all 90, plus `rdsadmin` for `spatial_ref_sys` alone — the exception
recorded in the facts table above, and unchanged by the upgrade. Any *other* owner, or a
`connect_superset` count below 90, is the real signal.

An empty `pg_subscription_rel`, or anything at `d`, means replication is dead regardless of what
anything printed.

**Task 13: Data — movement, not just counts**

**Step 1:** `./manage.py logical_replication_status`, compare to the Task 2 baseline.
**Step 2:** Wait several minutes and sample again. **Step 3:** Confirm the counts moved — a single
sample cannot distinguish frozen from current, which is exactly how this failure hides.
**Step 4:** Insert a synthetic row into one replicated table, confirm it reaches the secondary, then
delete it and confirm the delete replicates too — the plan's one write to the primary, so log both
statements. **Step 5:** Slot on the primary is `active`, retained bytes small and falling.

**Task 14: Superset** — dashboards load, logins work, a saved chart still queries the analytics
database, and geometry-backed content renders.

**Task 15: Close out** — summarise the measured upgrade duration, the probe answer, and whether the
PostGIS extension upgrade worked as a non-owner. Fold all three back into the investigation doc;
they were the open unknowns.

---

## Phase 5 — Rollback and abort

**There is no in-place rollback.** Snapshot restore is the only path back and it yields a **new
endpoint**, so `SECONDARY_DATABASE_URL` and Superset's config change anyway — in-place's "no config
change" advantage holds only on the success path. Pre-stage where both are set.

| Trigger | Action |
|---|---|
| Precheck fails when the upgrade is initiated | Safe — it aborts and nothing changes. Retrieve `pg_upgrade_precheck.log` from the RDS console, fix the flagged extension, retry. Cost is a wasted window, not damage. |
| Task 7 — PostGIS not at 3.4.3 after the upgrade | **Not a rollback trigger.** Wrong minor applied; 3.4.3 is supported on PG16. Verify geometry (Step 5) and carry the update as a follow-up. |
| Task 8 probe returns `False` | Proceed to Task 10 — pre-authorised in Task 4 Step 3. No in-window approval needed. |
| Task 10 — tables stuck at `d` | Truncate them again; the sync self-heals. Verified. |
| Primary free space falling during the window | **Do not re-enable the subscription to relieve it** — the `ENABLE` prohibition holds regardless of storage pressure, and enabling before the Task 8 probe is what produces the silent permanent freeze. **Prefer pressing on to Task 8** and taking the re-copy branch, which releases the slot and keeps the subscription. Drop the subscription only if the window itself has to be abandoned — and note that doing so puts Task 10 out of reach (see below). |
| Superset broken after the upgrade | Restore the Task 5 window-start metadata dump (procedure below); if that fails, restore the snapshot. |

**Dropping the subscription forecloses Task 10**, which enables the existing one and needs the
`REFRESH` branch. The only route back is then Task 9 Step 4 standalone — drop the slot, truncate,
`CREATE SUBSCRIPTION` with `copy_data = true`, `ENABLE` — untested end to end. Hence pressing on to
Task 8 instead.

**Restoring the metadata dump.** `superset_meta` still exists at this point, so a plain
`pg_restore` will not replace what is in it — it errors on every existing object and leaves a
half-old, half-new database. Stop Superset first, then:

```bash
pg_restore -h <endpoint> -U postgres -d superset_meta \
  --clean --if-exists superset_meta_window_start.dump
```

`--clean --if-exists` drops each object before recreating it. **Do not pass `--no-owner` or
`--role`** — the dump carries the right `ALTER ... OWNER` statements and `postgres` can apply them,
so ownership returns as it was. `postgis` is dropped and recreated too, so confirm it is back at
3.4.3 before restarting Superset, then check logins and one saved chart.

`pg_restore` continues past errors and exits `1` if it ignored any, so read the error list rather
than the exit code — the `postgis` owner and comment statements fail benignly on RDS.

**What bounds the downside:** the primary is written to in three places only — Task 1's `GRANT`,
`setup_logical_replication`'s `ALTER PUBLICATION ... SET TABLE`, and Task 13's canary and its delete
— and the replicated tables are reproducible by definition. The worst realistic outcome is Superset
down for hours, analytics rebuilt from scratch, possibly a snapshot restore onto a new endpoint with
some Superset metadata lost.
