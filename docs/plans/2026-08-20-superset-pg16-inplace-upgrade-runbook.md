# Superset PG16 In-Place Upgrade — Runbook

Checklist for upgrading the Superset RDS instance (`connect-superset-prod`) from **15.17 to 16.13**.
Work top to bottom and tick each box. For the reasoning behind any step, see the
[plan](2026-08-20-superset-pg16-inplace-upgrade-plan.md); task numbers match.

**Outage:** connect-superset.dimagi.com only. connect.dimagi.com stays up; Connect deploys are frozen.

## Hard rules

- ⛔ Do Task 1's `GRANT`s **before** the upgrade. They cannot be applied afterwards.
- ⛔ Never `ALTER SUBSCRIPTION ... ENABLE` on the upgraded instance before the Task 8 probe.
- ⛔ Never pass `--recreate` to `setup_logical_replication`.
- ⛔ Never `DROP SUBSCRIPTION` without first `SET (slot_name = NONE)`.
- ⛔ Never touch the primary's parameter group (`logicalreplicatorpublisher15`).
- ⛔ Every `aws` command carries `AWS_PROFILE=commcare-connect` and `--region eu-west-1`. The old
  us-east-1 instances have the **same identifiers**.

## Prerequisites

| Need | Detail |
|---|---|
| Superset RDS | `connect-superset-prod`, parameter group `default.postgres15`, master user `postgres` (1Password: *RDS Replica Subscriber DB Root credentials*) |
| Connect primary | `connect-production`, parameter group `logicalreplicatorpublisher15` (leave alone) |
| `postgres_repl` password | Replication user on the primary (1Password: *Prod Superset RDS replication user*). Used for the subscription and for slot drops |
| AWS | Console, plus a local AWS CLI with the `commcare-connect` profile and RDS permissions |
| Connect shell | `./manage.py shell` on an EU Connect host, as used for the earlier checks |
| Superset host | Browser SSH with sudo. `pg_dump`/`pg_restore` 16.15 live here |
| Superset UI | Admin role (for the R4 fallback) |

## Record as you go

| Item | Value |
|---|---|
| `N` = `len(REPLICATION_ALLOWED_MODELS)` (Session setup) | |
| Baseline row counts and table owners (Task 2) | |
| Slot `restart_lsn` / `confirmed_flush_lsn` / time (Task 6) | |
| `APPLIED` — subscriber's applied position (Task 6) | |
| Snapshot identifier (Task 6) | |
| Upgrade start / end (Task 7) | |
| Probe result → branch taken (Task 8) | |
| Rollback owner / cutoff time (R0) | |

## Session setup

In the Connect shell, paste **one block at a time**, pressing Enter on a blank line after each `def`:

```python
import psycopg2, getpass
from django.conf import settings
from django.db import connections
from commcare_connect.multidb.constants import REPLICATION_ALLOWED_MODELS

PW = getpass.getpass("Superset RDS postgres password: ")
REPL_PW = getpass.getpass("postgres_repl password: ")
d = connections["default"].settings_dict
CONN = f"host={d['HOST']} port={d['PORT']} dbname={d['NAME']} user=postgres_repl password={REPL_PW} sslmode=require"
N = len(REPLICATION_ALLOWED_MODELS)
REPLICATED = ", ".join(f'"{m._meta.db_table}"' for m in REPLICATION_ALLOWED_MODELS)
TRUNCATE = f"SET ROLE connect_superset; TRUNCATE {REPLICATED} CASCADE;"   # always follow with RESET ROLE
```

```python
def secondary(dbname=None):
    s = connections[settings.SECONDARY_DB_ALIAS].settings_dict
    c = psycopg2.connect(host=s["HOST"], port=s["PORT"], dbname=dbname or s["NAME"],
                         user="postgres", password=PW, sslmode="require")
    c.autocommit = True
    return c
```

```python
def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        try: return cur.fetchall()
        except psycopg2.ProgrammingError: return None
```

```python
def primary(sql):
    with connections["default"].cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()
```

```python
def repl(sql):
    c = psycopg2.connect(host=d["HOST"], port=d["PORT"], dbname=d["NAME"], user="postgres_repl",
                         password=REPL_PW, sslmode="require")
    c.autocommit = True
    try: return q(c, sql)
    finally: c.close()
```

```python
def cascade_nonempty(c):
    del c.notices[:]
    try: q(c, f"BEGIN; SET LOCAL ROLE connect_superset; SET LOCAL lock_timeout = '5s'; TRUNCATE {REPLICATED} CASCADE;")
    finally: q(c, "ROLLBACK;")
    tables = [n.split('"')[1] for n in c.notices if "cascades to table" in n]
    counts = {t: q(c, f'SELECT count(*) FROM "{t}"')[0][0] for t in tables}
    return {t: n for t, n in counts.items() if n}
```

```python
def wait_disabled(timeout=120):
    import time
    for _ in range(timeout):
        workers = q(conn, "SELECT count(*) FROM pg_stat_subscription WHERE pid IS NOT NULL;")[0][0]
        slot = primary("SELECT active FROM pg_replication_slots WHERE slot_name='tables_for_superset_sub';")
        if workers == 0 and slot in ([(False,)], []):
            return True
        time.sleep(1)
    return False
```

```python
conn = secondary()
```

- `wait_disabled()` returns `True` once the subscription's workers have exited **and** the primary's
  slot is inactive. `DISABLE` is asynchronous: until then, positions can still move.
- `primary()` runs as Connect's `connect` user: reads only. Slot drops go through `repl()`, because
  `connect` has no replication rights.
- `cascade_nonempty()` is a rolled-back dry run of the truncate. It lists non-replicated tables that
  `CASCADE` would empty **and** that hold rows. Expect `{}` (all 38 were empty on 2026-10-26). It
  waits at most 5s for locks; a `lock timeout` error outside the window just means Superset was busy,
  so retry.
- Record `N`.

---

## Before the window

### Task 1 — One-way GRANT

- [ ] Precondition. Expect `[]`:
  ```python
  conn = secondary()
  q(conn, """SELECT grantee.rolname FROM pg_auth_members m
             JOIN pg_roles t ON t.oid = m.roleid JOIN pg_roles grantee ON grantee.oid = m.member
             WHERE t.rolname = 'connect_superset';""")
  ```
- [ ] Apply: `q(conn, 'GRANT "connect_superset" TO "postgres";')`. **If it errors, stop and escalate.**
- [ ] Re-run the precondition. Expect `postgres` listed.
- [ ] Also grant the **Superset metadata owner** (owns the 50 tables in `superset_meta`). Without it, a
      metadata restore on PG16 recreates Superset's tables owned by `postgres`, and Superset loses access
      to them:
  ```python
  q(conn, 'GRANT "superset_meta" TO "postgres";')
  q(conn, "SELECT pg_has_role('postgres','superset_meta','MEMBER');")   # [(True,)]
  ```

### Task 2 — Baselines and checks

- [ ] `./manage.py logical_replication_status` — record the counts. Log in as `postgres`. It prints
      the `postgres_repl` password in the subscription row: redact it before sharing the output.
- [ ] `primary("SELECT count(*) FROM pg_publication_tables WHERE pubname='tables_for_superset_pub';")` → `N`
- [ ] `q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")` → `[('r', N)]`
- [ ] Record table owners (Task 12 compares against this):
      `q(conn, "SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1;")`
- [ ] `cascade_nonempty(conn)` → `{}`
- [ ] `repl("SELECT pg_has_role(current_user, 'rds_replication', 'MEMBER');")` → `True`
      (proves `REPL_PW` works and `postgres_repl` can drop slots).
- [ ] `q(conn, "SELECT has_function_privilege('postgres','pg_replication_origin_advance(text,pg_lsn)','EXECUTE'), has_function_privilege('postgres','pg_replication_origin_progress(text,boolean)','EXECUTE');")`
      → `[(True, True)]` (needed for Tasks 6 and 9).
- [ ] `CONN` actually reaches the primary. This connects and reads the publication, but creates no
      slot and replicates nothing:
  ```python
  q(conn, f"CREATE SUBSCRIPTION probe_sub CONNECTION '{CONN}' PUBLICATION tables_for_superset_pub WITH (create_slot = false, enabled = false, copy_data = false);")
  q(conn, "ALTER SUBSCRIPTION probe_sub SET (slot_name = NONE); DROP SUBSCRIPTION probe_sub;")
  ```
  If the first line fails, fix `CONN` (or the network) before the window. If the second fails, run
  it again: `probe_sub` must not be left behind.

### Task 3 — Backups

- [ ] Backup retention > 0:
  ```bash
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 describe-db-instances --db-instance-identifier connect-superset-prod \
    --query 'DBInstances[].[BackupRetentionPeriod,DBInstanceStatus]' --output table
  ```

### Task 4 — Agreements

- [ ] Outage window agreed.
- [ ] Deploy freeze agreed for the window.
- [ ] Window has ~1 hour of headroom for the slow path: if Task 9b or 10 is needed, Superset
      stays down until `opportunity_uservisit` (14 GB of the 16 GB) finishes copying — ~12 min for
      12 GB in the rehearsal, so ~15 min here; allow up to an hour. Prefer low Connect traffic, and say in the downtime notice it
      may run long.

### R0 — Rollback pre-stage

- [ ] Record the restore settings:
  ```bash
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 describe-db-instances --db-instance-identifier connect-superset-prod \
    --query 'DBInstances[0].{Class:DBInstanceClass,SubnetGroup:DBSubnetGroup.DBSubnetGroupName,
             SecurityGroups:VpcSecurityGroups[].VpcSecurityGroupId,ParamGroups:DBParameterGroups[].DBParameterGroupName,
             OptionGroups:OptionGroupMemberships[].OptionGroupName,MultiAZ:MultiAZ,Public:PubliclyAccessible,
             DeletionProtection:DeletionProtection,BackupRetention:BackupRetentionPeriod,
             MonitoringInterval:MonitoringInterval,ReadReplicas:ReadReplicaDBInstanceIdentifiers,
             StorageType:StorageType,Iops:Iops,Endpoint:Endpoint.Address}'
  ```
  `ReadReplicas` should be empty. If not, stop: the upgrade plan does not cover replicas.
- [ ] Name the **rollback owner** and the **cutoff**: the latest time a full rollback can start and
      still finish in the window. Rehearsal timings: restore ~7 min, rename swap ~4 min, re-copy
      ~15 min; allow up to ~1 hour in total. Record both.
- [ ] Superset endpoint locations (for the R4 fallback):
  - Metadata URI: `SQLALCHEMY_DATABASE_URI` in `/home/ccc_superset/www/superset/superset_config.py`.
    Edit by hand; the ansible_superset repo is out of date, so don't run the playbook.
  - Restart: `sudo supervisorctl restart superset`.
  - Stored connection: only **Connect New** points at this instance.

---

## The window

### Task 5 — Announce and freeze

- [ ] Post the downtime notice (mention that Superset users will be logged out).
- [ ] Activate the deploy freeze.
- [ ] **Stop Superset**: `sudo supervisorctl status`, then stop **every** Superset program listed
      (web, and any celery worker/beat for reports or alerts), e.g. `sudo supervisorctl stop superset`.
      Confirm all show `STOPPED`. They stay down until Task 14.
- [ ] On the **Superset host**, dump the metadata, and confirm the file is non-empty:
  ```bash
  pg_dump -h <connect-superset-prod endpoint> -U postgres -d superset_meta -Fc -f ~/superset_meta_window_start.dump
  ls -la ~/superset_meta_window_start.dump
  ```

### Task 6 — Disable, record and snapshot

- [ ] Disable, and wait for the workers to exit. Expect `subenabled = False`, then `True`:
  ```python
  conn = secondary()
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
  q(conn, "SELECT subname, subenabled FROM pg_subscription;")
  wait_disabled()
  ```
  Record nothing until `wait_disabled()` is `True`.
- [ ] Record the slot position:
  ```python
  primary("""SELECT slot_name, active, restart_lsn, confirmed_flush_lsn,
             pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn))
             FROM pg_replication_slots;""")
  ```
- [ ] Record **`APPLIED`**, how far the subscriber had actually applied. The upgrade erases this, and
      Task 9 needs it:
  ```python
  OLDOID = q(conn, "SELECT oid FROM pg_subscription WHERE subname='tables_for_superset_sub';")[0][0]
  APPLIED = q(conn, f"SELECT pg_replication_origin_progress('pg_{OLDOID}', true);")[0][0]
  primary(f"SELECT pg_wal_lsn_diff('{APPLIED}', confirmed_flush_lsn) FROM pg_replication_slots WHERE slot_name='tables_for_superset_sub';")
  ```
  The last line shows how many bytes the subscriber is ahead of the slot. A negative value is also
  fine: it means no replicated writes since the subscriber's last commit, and streaming then resumes
  from the slot.
- [ ] Snapshot, and **wait for it to finish**:
  ```bash
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 create-db-snapshot --db-instance-identifier connect-superset-prod \
    --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 wait db-snapshot-completed --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1
  ```
- [ ] From now on, re-run the slot query periodically and **watch primary free space**.

### Task 7 — Upgrade

- [ ] Start the upgrade (or the console equivalent: Modify → 16.13, not `16.13-R2` → parameter group
      `default.postgres16` → apply immediately):
  ```bash
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 modify-db-instance --db-instance-identifier connect-superset-prod --engine-version 16.13 \
    --allow-major-version-upgrade --db-parameter-group-name default.postgres16 --apply-immediately
  ```
- [ ] Watch the status. Expect `upgrading` (~5 min), then `backing-up` on 16.13, then `available`
      (~14 min in total in the rehearsal). The database accepts connections from `backing-up` onwards,
      so carry on with the next steps then.
  - Precheck failed (`upgrading`, then back to `available` on 15.17) → **Abort before upgrade** (below).
  - Upgrade failed, or not on 16.13 by the cutoff → **Full rollback**.
- [ ] Reconnect (the reboot killed `conn`): `conn = secondary(); q(conn, "SELECT version();")` → 16.13
- [ ] PostGIS. Expect `[('3.4.3', '3.4.3')]` for both databases:
  ```python
  for db in ("superset", "superset_meta"):
      print(db, q(secondary(db), """SELECT e.extversion, a.default_version FROM pg_extension e
            JOIN pg_available_extensions a ON a.name = e.extname WHERE e.extname = 'postgis';"""))
  ```
  If it shows 3.4.6 available, note it as a follow-up. **This is not a rollback trigger.**
- [ ] Geometry queries. Expect one `POINT(...)`; `[]` proves nothing unless the table is empty:
  `q(conn, "SELECT ST_AsText(ST_Centroid(boundary)) FROM microplanning_workarea WHERE boundary IS NOT NULL LIMIT 1;")`

### Task 8 — Probe

- [ ] Expect `subenabled = False` and a count of `0`:
  ```python
  q(conn, "SELECT subname, subenabled FROM pg_subscription;")
  q(conn, "SELECT count(*) FROM pg_subscription_rel;")
  ```
  If the count is `N`, not `0`, **stop and escalate**: the upgrade didn't wipe the subscriber state as
  expected, and Task 10's refresh would copy nothing.
- [ ] Task 9 needs to set the replay position: expect `[(True,)]`:
      `q(conn, "SELECT has_function_privilege('postgres','pg_replication_origin_advance(text,pg_lsn)','EXECUTE');")`
- [ ] Test-create a throwaway subscription, as in Task 2. This is the real test of PG16's
      `CREATE SUBSCRIPTION` privileges and of `CONN`. It connects to the primary and reads the
      publication, but creates no slot and replicates nothing:
  ```python
  q(conn, f"CREATE SUBSCRIPTION probe_sub CONNECTION '{CONN}' PUBLICATION tables_for_superset_pub WITH (create_slot = false, enabled = false, copy_data = false);")
  q(conn, "ALTER SUBSCRIPTION probe_sub SET (slot_name = NONE); DROP SUBSCRIPTION probe_sub;")
  ```
  If the second line fails, run it again before going on: `probe_sub` must not be left behind.
- [ ] Record the branch:
  - **Privilege `True` and the test-create worked** → Task 9.
  - **Anything else** → Task 10. Do not attempt Task 9.

### Task 9 — Replay from the slot

- [ ] Detach and drop:
  ```python
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
  q(conn, "DROP SUBSCRIPTION tables_for_superset_sub;")
  ```
- [ ] Re-create against the existing slot:
  ```python
  q(conn, f"""CREATE SUBSCRIPTION tables_for_superset_sub CONNECTION '{CONN}'
              PUBLICATION tables_for_superset_pub
              WITH (copy_data = false, slot_name = 'tables_for_superset_sub',
                    create_slot = false, enabled = false);""")
  ```
  - **If refused** → Task 9b.
- [ ] Start from `APPLIED`, so transactions the subscriber already has aren't re-sent (they would
      fail on duplicate keys). If the shell was restarted, set `APPLIED = '<recorded value>'` first:
  ```python
  NEWOID = q(conn, "SELECT oid FROM pg_subscription WHERE subname='tables_for_superset_sub';")[0][0]
  q(conn, f"SELECT pg_replication_origin_advance('pg_{NEWOID}', '{APPLIED}');")
  q(conn, f"SELECT pg_replication_origin_progress('pg_{NEWOID}', false);")   # → APPLIED
  ```
- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")`
- [ ] Watch for 3 minutes. `apply_error_count` must stay `0`:
      `q(conn, "SELECT apply_error_count, sync_error_count FROM pg_stat_subscription_stats;")`
  - Rising → duplicate keys. Go to Task 9b.
  - Stays `0` → **Verification**.

**Task 9b — fallback: fresh slot and full re-copy**

- [ ] If a subscription exists, detach and drop it:
      `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE; ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE); DROP SUBSCRIPTION tables_for_superset_sub;")`
- [ ] On the primary, confirm the slot is inactive, then drop it **as `postgres_repl`**:
  ```python
  wait_disabled()   # True
  repl("SELECT pg_drop_replication_slot('tables_for_superset_sub');")
  ```
- [ ] `cascade_nonempty(conn)` → `{}`. Then truncate: `q(conn, TRUNCATE); q(conn, "RESET ROLE;")`
- [ ] Re-create **without** `slot_name` / `create_slot`, so a fresh slot is created:
  ```python
  q(conn, f"""CREATE SUBSCRIPTION tables_for_superset_sub CONNECTION '{CONN}'
              PUBLICATION tables_for_superset_pub WITH (copy_data = true, enabled = false);""")
  ```
- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")` → watch the copy, as in Task 10.

### Task 10 — Rebuild (probe failed)

- [ ] `cascade_nonempty(conn)` → `{}` (before enabling: a "Stop" here must not leave an enabled
      subscription behind).
- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")`
- [ ] Truncate: `q(conn, TRUNCATE); q(conn, "RESET ROLE;")`
- [ ] `./manage.py setup_logical_replication` (no flags).
- [ ] Watch the copy until everything is at `r`. Ignore the command's own output:
      `q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")`
  - A table stuck at `d` shouldn't happen (everything is truncated in one statement). If it does,
    **don't** re-truncate with `CASCADE`, which would empty tables already at `r` that never re-copy:
    go to the full rollback.
  - Not all at `r` by the cutoff → **Full rollback**.

---

## Verification

### Task 12 — Structure

- [ ] `q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")` → `[('r', N)]`
- [ ] `q(conn, "SELECT * FROM pg_stat_subscription_stats;")` → error counts `0 | 0`
- [ ] Table owners match the Task 2 baseline:
  `q(conn, "SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1;")`
- [ ] Superset can read every table. Expect `[]`:
  ```python
  q(conn, """SELECT c.relname FROM pg_subscription_rel sr JOIN pg_class c ON c.oid = sr.srrelid
             WHERE NOT has_table_privilege('superset_readonly', c.oid, 'SELECT');""")
  ```
  If any tables are listed, re-grant:
  `q(conn, "SET ROLE connect_superset; GRANT SELECT ON ALL TABLES IN SCHEMA public TO superset_readonly;"); q(conn, "RESET ROLE;")`

### Task 13 — Data moves

- [ ] `./manage.py logical_replication_status` — compare to the baseline (redact the password line).
- [ ] Latest visit on each side. Wait a few minutes: the secondary must reach the primary's earlier value:
  `primary("SELECT max(id) FROM opportunity_uservisit;"), q(conn, "SELECT max(id) FROM opportunity_uservisit;")`
- [ ] The slot on the primary is `active`, and its retained WAL is small and falling.

### Task 14 — Superset

- [ ] Refresh planner statistics (the upgrade doesn't carry them over):
      `q(secondary(), "ANALYZE;")` and `q(secondary("superset_meta"), "ANALYZE;")`.
      Any "skipping" warnings for `superset_meta` tables are fine to ignore; the metadata is small.
- [ ] Start Superset (`sudo supervisorctl start superset`).
- [ ] Logins work, dashboards load, a saved chart queries, and maps render.

### Task 11 — Lift the freeze

- [ ] Deploy Connect and confirm `migrate_multi` succeeds.
- [ ] Post the all-clear.

### Task 15 — Close out

- [ ] Record the upgrade duration, the probe result and the branch taken in the investigation doc.

---

## If something goes wrong

| Problem | Do |
|---|---|
| Precheck fails | Abort before upgrade (below). |
| PostGIS 3.4.6 available after the upgrade | Not a problem. Follow up later. |
| Probe or test-create fails (Task 8) | Task 10. |
| Duplicate-key errors after Task 9's `ENABLE` | Task 9b. |
| `CREATE SUBSCRIPTION` refused in Task 9b | Full rollback. |
| `cascade_nonempty()` not `{}` | Stop. Find out what the listed tables hold before truncating. |
| Tables stuck at `d` | Full rollback (see Task 10). |
| Task 8 shows `pg_subscription_rel` not `0` | Stop and escalate. |
| Primary disk filling | **Don't** re-enable early. Press on to Task 8. If you must abandon, do the full rollback. |
| `superset_readonly` missing grants | Re-grant (Task 12). |
| Superset broken, data fine | Metadata restore (below). |
| Upgrade failed / replication not reconnected / verification failing by the cutoff | Full rollback (below). |

### Abort before upgrade

Only while the instance is still on **15.17** (precheck failed, or the window is abandoned before
Task 7 starts). Re-enabling is safe here: the subscriber's state is intact.

- [ ] `conn = secondary(); q(conn, "SELECT version();")` → 15.17
- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")`
- [ ] After a minute, `apply_error_count` is `0` and the slot is `active` with retained WAL falling.
- [ ] Start Superset, lift the freeze, and post an update.

### Metadata restore

- [ ] Stop Superset (if running).
- [ ] On the Superset host, restore. Expect exit code `0`. If there are errors, read them: PostGIS owner
      and comment errors are harmless.
  ```bash
  pg_restore -h <endpoint> -U postgres -d superset_meta --clean --if-exists ~/superset_meta_window_start.dump
  ```
  Do **not** pass `--no-owner` or `--role`.
- [ ] Tables in `superset_meta` are still owned by `superset_meta`. PostGIS comes back at PG16's
      default (3.4.6), which is fine.
- [ ] Start Superset, then check logins and one saved chart.

### Full rollback

**R1 — Detach the upgraded instance**

- [ ] Make sure Superset is stopped.
- [ ] If the upgraded instance is reachable and has a subscription, reconnect and **detach, do not drop**:
  ```python
  conn = secondary()
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
  wait_disabled()   # True
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
  ```

**R2 — Check the slot; this picks R5a or R5b**

- [ ] `primary("SELECT active, confirmed_flush_lsn FROM pg_replication_slots WHERE slot_name='tables_for_superset_sub';")`

  | Result | Go to |
  |---|---|
  | Inactive, LSN **equals** the Task 6 record | R5a |
  | LSN **ahead** of the Task 6 record | R5b |
  | No slot | R5b, skipping the slot drop |
  | `active = true` | The failed instance (or something else) still holds it. End that connection as `postgres_repl`, then re-check: `repl("SELECT pg_terminate_backend(active_pid) FROM pg_replication_slots WHERE slot_name='tables_for_superset_sub' AND active;")`. If it comes back, stop the failed instance (`aws rds stop-db-instance`, with the profile and region). |

**R3 — Restore the snapshot**

- [ ] Restore with the R0 settings:
  ```bash
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 restore-db-instance-from-db-snapshot \
    --db-instance-identifier connect-superset-prod-rollback \
    --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1 \
    --db-instance-class <R0> --db-subnet-group-name <R0> --vpc-security-group-ids <R0> \
    --db-parameter-group-name default.postgres15 --option-group-name <R0> \
    --storage-type <R0> [--iops <R0>] <--multi-az | --no-multi-az> <--publicly-accessible | --no-publicly-accessible>
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 wait db-instance-available --db-instance-identifier connect-superset-prod-rollback
  ```
  Its state is checked in R5, once there is a connection to it.

**R4 — Move the endpoint**

- [ ] Preferred: swap the instance identifiers, so no config changes are needed:
  ```bash
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 modify-db-instance --db-instance-identifier connect-superset-prod \
    --new-db-instance-identifier connect-superset-prod-failed-pg16 --apply-immediately
  # wait until available
  AWS_PROFILE=commcare-connect aws rds --region eu-west-1 modify-db-instance --db-instance-identifier connect-superset-prod-rollback \
    --new-db-instance-identifier connect-superset-prod --apply-immediately
  # wait until available and the endpoint matches the R0 record
  ```
  Then restart the Connect containers (`sudo docker restart`) to drop pooled connections. If the
  failed instance can't be modified (stuck in `upgrading`), use the fallback below instead.
- [ ] *Or, as the fallback:* update `secondary_db_url` (1Password *Ansible Secrets - Production*) →
      `inv django-settings`. Running containers keep the old value until the R7 deploy, so R5 needs a
      **new** container (below). Then edit `SQLALCHEMY_DATABASE_URI` in
      `/home/ccc_superset/www/superset/superset_config.py`, and once Superset is started, update the
      host on the **Connect New** connection before users get access. Keep `SECRET_KEY` unchanged.
- [ ] Stop the failed instance, so it can never reclaim the slot:
      `AWS_PROFILE=commcare-connect aws rds --region eu-west-1 stop-db-instance --db-instance-identifier <its current identifier>`. Don't delete it.
- [ ] Re-apply the R0 settings a restore doesn't carry over (deletion protection, backup retention,
      monitoring), via `aws rds modify-db-instance`.

**R5 — Reconnect first.** The old `conn` still points at the failed instance.

- [ ] Start a **new** container on a web host (`docker restart` does not reload the env file), and use
      it for everything from here, including R5b's `setup_logical_replication`:
      `sudo docker run --rm -it --env-file /home/connect/www/commcare-connect/docker.env <image> bash`
      Inside it, run `python manage.py shell` and redo the Session setup.
- [ ] Confirm it is the restored instance. Expect 15.17, `subenabled = False`, and `[('r', N)]`:
  ```python
  conn = secondary()
  q(conn, "SELECT version();")
  q(conn, "SELECT subname, subenabled FROM pg_subscription;")
  q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")
  ```
  If it shows 16.13, you are still on the failed instance. Stop.

**R5a — Resume (slot untouched)**

- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")`

**R5b — Re-copy (slot advanced or gone).** Do **not** use Task 10's refresh here; it copies nothing.

- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")`
- [ ] `q(conn, "DROP SUBSCRIPTION tables_for_superset_sub;")`
- [ ] If the slot exists: `repl("SELECT pg_drop_replication_slot('tables_for_superset_sub');")`
- [ ] `cascade_nonempty(conn)` → `{}`. Then truncate: `q(conn, TRUNCATE); q(conn, "RESET ROLE;")`
- [ ] Exit the shell and, **in the same container**, run `python manage.py setup_logical_replication`
      (no flags). Then reopen the shell, redo the Session setup, and watch until all tables are at `r`.

**R6–R8 — Finish**

- [ ] Run Verification (Tasks 12–14) against the restored instance (engine 15.17).
- [ ] Lift the freeze and deploy. Confirm `migrate_multi` reaches the restored instance.
- [ ] The failed instance (stopped in R4) auto-restarts after 7 days: delete it or stop it again before
      then, once the cause is understood.
- [ ] Keep the snapshot and the dump. Record the trigger, the branch taken and the durations in the
      investigation doc.
