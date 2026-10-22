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
it before proceeding. The manual snapshot is taken in the window, at Task 6 Step 3.

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

**Step 4:** Record timestamps.

### Task 6: Disable the subscription and snapshot

**Step 1: Disable**

```python
conn = secondary(user="postgres")
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
print(q(conn, "SELECT subname, subenabled FROM pg_subscription;"))   # expect False
```

**Step 2: Record the slot position on the primary** — that is where the backlog replays from:

```python
from django.db import connections
cur = connections["default"].cursor()
cur.execute("""SELECT slot_name, active, restart_lsn, confirmed_flush_lsn,
                      pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained
               FROM pg_replication_slots;""")
print(cur.fetchall())
cur.close()
```

**Record the `restart_lsn`, the `confirmed_flush_lsn` and the wall-clock time.** Phase 5 R2
compares against `confirmed_flush_lsn`. Re-run this query periodically through the window:
`retained` is the WAL the primary is holding on the slot's behalf, and it only stops growing once
the subscription is re-enabled. `max_slot_wal_keep_size = -1` means the slot will **not** be dropped
to protect the disk, so **watch primary free space** rather than assuming it self-limits.

**Step 3: Take the manual snapshot, and wait for it.** It is taken *after* the disable on purpose:
the snapshot's replication position then matches the slot exactly, so a restore made before anything
re-enables replication can resume with no gap and no re-copy (Phase 5, R5a). A snapshot taken while
the subscription was still running would fall behind the slot and could never resume cleanly.

```bash
aws rds create-db-snapshot --db-instance-identifier <instance-id> \
  --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1

aws rds wait db-snapshot-completed \
  --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1
```

**Do not start the upgrade until the wait returns** — a snapshot still in `creating` is not a
rollback. **Record the identifier**; Phase 5 refers to it. Bump the `-1` on a same-day retry, since
RDS refuses a duplicate.

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

**Superset can read every replicated table** — expect `[]`:

```python
print(q(conn, """SELECT c.relname FROM pg_subscription_rel sr
                 JOIN pg_class c ON c.oid = sr.srrelid
                 WHERE NOT has_table_privilege('superset_readonly', c.oid, 'SELECT');"""))
```

If any are listed, re-grant (not a rollback trigger, and don't re-run `setup_logical_replication` for it):

```python
q(conn, "SET ROLE connect_superset; GRANT SELECT ON ALL TABLES IN SCHEMA public TO superset_readonly;")
q(conn, "RESET ROLE;")
```

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

**There is no in-place rollback.** You cannot downgrade an RDS engine. Two levels of recovery exist:

- **Metadata restore** — Superset is broken but the instance and replication are healthy. Restore
  the Task 5 dump into `superset_meta` (procedure below). The instance stays on 16.13.
- **Full rollback** — abandon the upgraded instance. Restore the Task 6 snapshot onto a new 15.17
  instance, move the endpoint, and reconnect replication (R0–R8 below).

| Trigger | Action |
|---|---|
| Precheck fails when the upgrade is initiated | Safe — it aborts and nothing changes. Retrieve `pg_upgrade_precheck.log` from the RDS console, fix the flagged extension, retry. Cost is a wasted window, not damage. |
| Task 7 — PostGIS not at 3.4.3 after the upgrade | **Not a rollback trigger.** Wrong minor applied; 3.4.3 is supported on PG16. Verify geometry (Step 5) and carry the update as a follow-up. |
| Task 8 probe returns `False` | Proceed to Task 10 — pre-authorised in Task 4 Step 3. No in-window approval needed. |
| Task 10 — tables stuck at `d` | Truncate them again; the sync self-heals. Verified. |
| Primary free space falling during the window | **Do not re-enable the subscription to relieve it** — the `ENABLE` prohibition holds regardless of storage pressure, and enabling before the Task 8 probe is what produces the silent permanent freeze. **Prefer pressing on to Task 8** and taking the re-copy branch, which releases the slot and keeps the subscription. If the window itself has to be abandoned, that is the full rollback — R1 detaches the subscription safely. Do not drop it outside that procedure (see below). |
| Superset broken after the upgrade | Restore the Task 5 window-start metadata dump (procedure below); if that fails, full rollback. |
| Upgrade fails after the precheck passed, or the instance is not `available` on 16.13 by the R0 cutoff | Full rollback. If RDS reports the instance back on 15.17 and `available`, it was the precheck case above instead. |
| Neither Task 9 nor Task 10 gets all 29 tables to `r` by the R0 cutoff | Full rollback. The restored instance's replication can be rebuilt by the PG15 route in R5b, which does not depend on the PG16 permission changes. |
| Phase 4 checks fail and cannot be fixed by the R0 cutoff | Full rollback. |

**Dropping the subscription forecloses Task 10**, which enables the existing one and needs the
`REFRESH` branch. The only route back is then Task 9 Step 4 standalone — drop the slot, truncate,
`CREATE SUBSCRIPTION` with `copy_data = true`, `ENABLE` — untested end to end. Hence pressing on to
Task 8 instead. The full rollback below stays available either way.

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

### Full rollback

Restores Superset and analytics to their state at Task 6, on 15.17. Connect itself is unaffected
throughout — only Superset is down, and Connect deploys stay frozen until R7.

**Unverified as a whole.** Each piece is standard RDS or Postgres behaviour, but the sequence has not
been rehearsed. The R0 dry run is the rehearsal.

#### R0: Pre-stage (before the window)

**Step 1: Record the settings a restore needs.** A snapshot restore does *not* reproduce the
instance's networking or parameter group — anything not passed explicitly falls back to defaults.

```bash
aws rds describe-db-instances --db-instance-identifier <instance-id> \
  --query 'DBInstances[0].{Class:DBInstanceClass,SubnetGroup:DBSubnetGroup.DBSubnetGroupName,
           SecurityGroups:VpcSecurityGroups[].VpcSecurityGroupId,ParamGroups:DBParameterGroups[].DBParameterGroupName,
           OptionGroups:OptionGroupMemberships[].OptionGroupName,MultiAZ:MultiAZ,Public:PubliclyAccessible,
           StorageType:StorageType,Iops:Iops,Endpoint:Endpoint.Address}'
```

**The parameter group matters most.** The upgrade moves the instance onto a `postgres16` group, and
a restored 15.17 instance needs the current `postgres15` one, which carries the logical-replication
settings. Record its name and do not delete or modify it until the upgrade is closed out.

**Step 2: Record where the endpoint is configured**, in case R4's rename route is not used:

- **Connect** — `secondary_db_url` in the 1Password item *Ansible Secrets - Production* (vault
  *Connect Tech*), rendered into `SECONDARY_DATABASE_URL` by
  [connect.docker.env.j2](../../deploy/roles/connect/templates/connect.docker.env.j2) via
  `inv django-settings`, and picked up on the next deploy.
- **Superset** — `SQLALCHEMY_DATABASE_URI` (points at `superset_meta`) in Superset's own deployment
  config, which lives outside this repo. Record where it lives and who can change it. Also the stored
  analytics connection to `superset` (*Settings → Database Connections*); because that is stored
  in `superset_meta`, the restored copy still holds the old host.

**Step 3: Name the rollback owner and the cutoff** — the latest time a full rollback can start and
still finish inside the window. Restore time plus a possible full re-copy (R5b) is unmeasured; Step 4
measures it.

**Step 4 (recommended): Dry-run the restore.** Restore the latest automated snapshot to a scratch
identifier with the Step 1 settings, except for the parameter group. **Use a copy of the
`postgres15` group with `max_logical_replication_workers = 0`.** Automated snapshots are taken with
the subscription *enabled*, so a scratch instance booted with the real group starts an apply worker
straight away. Whenever the production subscriber reconnects, that worker can take the production
slot and consume changes production then never receives. With zero workers it cannot. On the
scratch instance, time how long it takes to become `available`, confirm `postgres` logins and
`pg_subscription`, and try the R4 rename on it. Then delete it, and the scratch parameter group.
This is the only rehearsal of R3 and R4.

#### R1: Stop Superset and detach the upgraded instance from the slot

Stop Superset so nobody uses a half-working instance. Then, if the upgraded instance is reachable:

```python
conn = secondary(user="postgres")
print(q(conn, "SELECT subname, subenabled, subslotname FROM pg_subscription;"))
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
```

**Detach, do not `DROP`.** Dropping a subscription that still has a slot name also drops the slot
on the primary, which removes R5a. If no subscription exists, or the instance is unreachable,
skip this step. R2 confirms the outcome either way.

#### R2: Inspect the slot on the primary — this picks the R5 branch

```python
cur = connections["default"].cursor()
cur.execute("""SELECT slot_name, active, confirmed_flush_lsn FROM pg_replication_slots
               WHERE slot_name = 'tables_for_superset_sub';""")
print(cur.fetchall())
cur.close()
```

| Result | Meaning | R5 branch |
|---|---|---|
| Present, `active = false`, `confirmed_flush_lsn` **equal** to the Task 6 record | Nothing consumed the slot since the snapshot | **R5a** — resume, no re-copy |
| Present, `confirmed_flush_lsn` **ahead** of the Task 6 record | Task 9 or 10 replayed changes the snapshot does not have | **R5b** — re-copy |
| Absent | Task 9 Step 4 dropped it | **R5b** — re-copy, skip its slot drop |
| `active = true` | Something still holds the slot | Stop. Redo R1, and find the holder via `pg_stat_replication`. |

Write the branch into the log before continuing.

#### R3: Restore the snapshot

```bash
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier <instance-id>-rollback \
  --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1 \
  --db-instance-class <R0> --db-subnet-group-name <R0> \
  --vpc-security-group-ids <R0> --db-parameter-group-name <R0 postgres15 group> \
  --option-group-name <R0> [--multi-az] [--no-publicly-accessible]

aws rds wait db-instance-available --db-instance-identifier <instance-id>-rollback
```

The instance comes back on 15.17, with its subscription **disabled** (Task 6 ran before the
snapshot), so it does not touch the slot until R5. If the manual snapshot is missing, use RDS's
automatic pre-upgrade snapshot. It was also taken after Task 6, so R2's comparison still holds.

Confirm the state before going further, connecting as `postgres` to the new endpoint:

```sql
SELECT subname, subenabled FROM pg_subscription;                 -- disabled
SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1; -- ('r', 29): PG15 keeps it
SELECT extversion FROM pg_extension WHERE extname = 'postgis';   -- 3.4.3
```

#### R4: Move the endpoint

**Preferred — rename, so no config changes.** An RDS endpoint is derived from the instance
identifier. Renaming the failed instance away and giving the restored instance the original
identifier means `SECONDARY_DATABASE_URL`, Superset's URI and its stored connection all stay
correct (unverified here; the R0 dry run can confirm it on the scratch instance):

```bash
aws rds modify-db-instance --db-instance-identifier <instance-id> \
  --new-db-instance-identifier <instance-id>-failed-pg16 --apply-immediately
# poll describe-db-instances until <instance-id>-failed-pg16 is 'available'
aws rds modify-db-instance --db-instance-identifier <instance-id>-rollback \
  --new-db-instance-identifier <instance-id> --apply-immediately
# poll until <instance-id> is 'available' and its Endpoint.Address matches the R0 record
```

DNS can take a few minutes to follow. Restart Superset and the Connect containers afterwards, so
no pooled connection still points at the old address.

**Fallback — repoint the config.** Use the R0 Step 2 locations. Update `secondary_db_url` and run
`inv django-settings`; it takes effect at the R7 deploy. Update Superset's `SQLALCHEMY_DATABASE_URI`
and its stored analytics connection. **Keep Superset's `SECRET_KEY` unchanged**, or the stored
connection passwords will not decrypt.

#### R5: Reconnect replication on the restored instance

**R5a — resume (slot untouched).** The restored subscriber's position matches the slot, so enabling
replays the outage backlog with no gap. This is safe here, unlike on the upgraded instance, because
PG15 kept the subscriber state intact (R3 showed 29 at `r`).

```python
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")
```

**R5b — re-copy (slot advanced or gone).** The restored tables are missing changes the primary will
not resend. **Do not use Task 10 here.** `REFRESH PUBLICATION` only copies tables the subscription
does not already know about. Task 10 works because the PG16 upgrade wipes that list; on the restored
instance all 29 are still listed, so the refresh would copy nothing and report success. Re-create
the subscription instead:

```python
# 1. Detach and drop the subscription (detaching keeps the drop from touching the primary)
q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
q(conn, "DROP SUBSCRIPTION tables_for_superset_sub;")
```

```sql
-- 2. On the primary, if R2 found the slot: confirm inactive, then drop
SELECT slot_name, active FROM pg_replication_slots WHERE slot_name = 'tables_for_superset_sub';
SELECT pg_drop_replication_slot('tables_for_superset_sub');
```

```python
# 3. Truncate the 29 tables -- the copy fails on duplicate keys otherwise
#    (list: commcare_connect/multidb/constants.py -> REPLICATION_ALLOWED_MODELS)
q(conn, "SET ROLE connect_superset; TRUNCATE <the 29 replicated tables>;")
q(conn, "RESET ROLE;")
```

```bash
# 4. Create a fresh subscription with a full copy. No flags; the subscription no longer exists,
#    so the command takes its CREATE branch, creates a new slot, and re-grants superset_readonly.
./manage.py setup_logical_replication
```

Superset serves empty tables until the copy finishes — the same cost agreed in Task 4 Step 3. Watch
the states move `i` → `d` → `r`, as in Task 10 Step 4.

#### R6: Verify

Run Phase 4 (Tasks 12–14) against the restored instance, with the same expectations, except
that the engine is 15.17. Task 13's movement check is what proves R5 really reconnected.

#### R7: Lift the deploy freeze

As in Task 11. If the fallback route in R4 was used, this deploy is also what picks up the new
`SECONDARY_DATABASE_URL`, so confirm `migrate_multi` reaches the restored instance.

#### R8: Clean up and record

- **Keep the failed PG16 instance until the cause is understood.** Stop it rather than delete it.
  RDS automatically restarts a stopped instance after 7 days, so delete it or stop it again before then.
- Keep the snapshot and the Task 5 dump until the next upgrade attempt.
- Record the trigger, the R2 branch taken, and the restore and copy durations in the investigation doc.

**What bounds the downside:** the primary is written to in three places only — Task 1's `GRANT`,
`setup_logical_replication`'s `ALTER PUBLICATION ... SET TABLE`, and Task 13's canary and its delete
— and the replicated tables are reproducible by definition. The worst realistic outcome is Superset
down for hours and analytics rebuilt from scratch, via the full rollback. Superset metadata changes
made during the window are lost. The window should not allow any.
