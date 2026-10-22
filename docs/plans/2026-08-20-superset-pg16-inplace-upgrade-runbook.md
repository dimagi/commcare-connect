# Superset PG16 In-Place Upgrade — Runbook

Checklist for upgrading the Superset RDS instance from **15.17 to 16.13**. Work top to bottom and
tick each box. For the reasoning behind any step, see the
[plan](2026-08-20-superset-pg16-inplace-upgrade-plan.md); task numbers match.

**Outage:** connect-superset.dimagi.com only. connect.dimagi.com stays up; Connect deploys are frozen.

## Hard rules

- ⛔ Do Task 1's `GRANT` **before** the upgrade. It cannot be applied afterwards.
- ⛔ Never `ALTER SUBSCRIPTION ... ENABLE` on the upgraded instance before the Task 8 probe.
- ⛔ Never pass `--recreate` to `setup_logical_replication`.
- ⛔ Never `DROP SUBSCRIPTION` without first `SET (slot_name = NONE)`.

## Record as you go

| Item | Value |
|---|---|
| Baseline row counts (Task 2) | |
| Slot `restart_lsn` / `confirmed_flush_lsn` / time (Task 6) | |
| Snapshot identifier (Task 6) | |
| Upgrade start / end (Task 7) | |
| Probe result → branch taken (Task 8) | |
| Rollback owner / cutoff time (R0) | |

## Session setup

Run everything in `./manage.py shell` on a production host, with these helpers loaded:

```python
import psycopg2, getpass
from django.conf import settings
from django.db import connections
from commcare_connect.multidb.constants import REPLICATION_ALLOWED_MODELS

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

def primary(sql):
    with connections["default"].cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()

REPLICATED = ", ".join(f'"{m._meta.db_table}"' for m in REPLICATION_ALLOWED_MODELS)
TRUNCATE = f"SET ROLE connect_superset; TRUNCATE {REPLICATED};"   # always follow with RESET ROLE
```

Check `len(REPLICATION_ALLOWED_MODELS) == 29`.

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

### Task 2 — Baselines

- [ ] `./manage.py logical_replication_status` — record the counts.
- [ ] `primary("SELECT count(*) FROM pg_publication_tables WHERE pubname='tables_for_superset_pub';")` → `29`
- [ ] `q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")` → `[('r', 29)]`

### Task 3 — Backups

- [ ] Backup retention > 0:
  ```bash
  aws rds describe-db-instances --db-instance-identifier <instance-id> \
    --query 'DBInstances[].[BackupRetentionPeriod,DBInstanceStatus]' --output table
  ```

### Task 4 — Agreements

- [ ] Outage window agreed.
- [ ] Deploy freeze agreed for the window.
- [ ] Window has ~1 hour of headroom for the slow path: if Task 8 sends you to Task 10, Superset
      stays down until `opportunity_uservisit` (14 GB of the 16 GB) finishes copying — estimated
      10–25 min, allow up to an hour. Prefer low Connect traffic, and say in the downtime notice it
      may run long.

### R0 — Rollback pre-stage

- [ ] Record the restore settings, especially the **`postgres15` parameter group name** (don't delete it):
  ```bash
  aws rds describe-db-instances --db-instance-identifier <instance-id> \
    --query 'DBInstances[0].{Class:DBInstanceClass,SubnetGroup:DBSubnetGroup.DBSubnetGroupName,
             SecurityGroups:VpcSecurityGroups[].VpcSecurityGroupId,ParamGroups:DBParameterGroups[].DBParameterGroupName,
             OptionGroups:OptionGroupMemberships[].OptionGroupName,MultiAZ:MultiAZ,Public:PubliclyAccessible,
             Endpoint:Endpoint.Address}'
  ```
- [ ] Superset endpoint locations (for the R4 fallback):
  - Metadata URI: `SQLALCHEMY_DATABASE_URI` in `/home/ccc_superset/www/superset/superset_config.py`.
    Edit by hand; the ansible_superset repo is out of date, so don't run the playbook.
  - Restart: `sudo supervisorctl restart superset`.
- [ ] *(Recommended)* Dry-run restore of the latest automated snapshot to a scratch instance, using a
      copy of the `postgres15` parameter group with **`max_logical_replication_workers = 0`**. Time it,
      try the R4 rename, then delete the scratch instance and parameter group.

---

## The window

### Task 5 — Announce and freeze

- [ ] Post the downtime notice (mention that Superset users will be logged out).
- [ ] Activate the deploy freeze.
- [ ] **Stop Superset.** It stays down until Task 14.
- [ ] Dump the metadata:
  ```bash
  pg_dump -h <endpoint> -U postgres -d superset_meta -Fc -f superset_meta_window_start.dump
  ```

### Task 6 — Disable and snapshot

- [ ] Disable. Expect `subenabled = False`:
  ```python
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
  q(conn, "SELECT subname, subenabled FROM pg_subscription;")
  ```
- [ ] Record the slot position:
  ```python
  primary("""SELECT slot_name, active, restart_lsn, confirmed_flush_lsn,
             pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn))
             FROM pg_replication_slots;""")
  ```
- [ ] Snapshot, and **wait for it to finish**:
  ```bash
  aws rds create-db-snapshot --db-instance-identifier <instance-id> \
    --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1
  aws rds wait db-snapshot-completed --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1
  ```
- [ ] From now on, re-run the slot query periodically and **watch primary free space**.

### Task 7 — Upgrade

- [ ] RDS console → Modify → engine **16.13** (not `16.13-R2`) → apply immediately.
- [ ] Wait until `available`.
  - Precheck failed → nothing changed. Read `pg_upgrade_precheck.log` and stop.
  - Upgrade failed, or not `available` by the cutoff → **Full rollback**.
- [ ] `conn = secondary(); q(conn, "SELECT version();")` → 16.13
- [ ] PostGIS. Expect `[('3.4.3', '3.4.3')]` for both databases:
  ```python
  for db in ("superset", "superset_meta"):
      print(db, q(secondary(dbname=db), """SELECT e.extversion, a.default_version FROM pg_extension e
            JOIN pg_available_extensions a ON a.name = e.extname WHERE e.extname = 'postgis';"""))
  ```
  If it shows 3.4.6 available, note it as a follow-up. **This is not a rollback trigger.**
- [ ] Geometry queries:
  `q(conn, "SELECT ST_AsText(ST_Centroid(geometry)) FROM microplanning_workarea WHERE geometry IS NOT NULL LIMIT 1;")`

### Task 8 — Probe

- [ ] Expect `subenabled = False` and a count of `0`:
  ```python
  q(conn, "SELECT subname, subenabled FROM pg_subscription;")
  q(conn, "SELECT count(*) FROM pg_subscription_rel;")
  ```
- [ ] Probe:
  ```python
  q(conn, "SELECT pg_has_role('postgres','pg_create_subscription','USAGE');")
  q(conn, "SELECT has_database_privilege(current_user, current_database(), 'CREATE');")
  ```
- [ ] Record the branch:
  - **Both `True`** → Task 9.
  - **Either `False`** → Task 10. Do not attempt Task 9.

### Task 9 — Replay from the slot (probe `True`)

- [ ] Detach and drop:
  ```python
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
  q(conn, "DROP SUBSCRIPTION tables_for_superset_sub;")
  ```
- [ ] Re-create against the existing slot:
  ```python
  q(conn, """CREATE SUBSCRIPTION tables_for_superset_sub
               CONNECTION 'host=<primary> port=5432 dbname=<db> user=postgres_repl password=<pw> sslmode=require'
               PUBLICATION tables_for_superset_pub
               WITH (copy_data = false, slot_name = 'tables_for_superset_sub',
                     create_slot = false, enabled = false);""")
  ```
  - **If refused** → do Task 9b instead of the next step.
- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")` → go to **Verification**.

**Task 9b — only if `create_slot = false` was refused**

- [ ] On the primary: confirm the slot is inactive, then drop it:
  ```python
  primary("SELECT slot_name, active FROM pg_replication_slots WHERE slot_name='tables_for_superset_sub';")
  primary("SELECT pg_drop_replication_slot('tables_for_superset_sub');")
  ```
- [ ] Truncate: `q(conn, TRUNCATE); q(conn, "RESET ROLE;")`
- [ ] Re-create with the same statement, using `WITH (copy_data = true, enabled = false)`.
- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")` → watch the copy, as in Task 10.

### Task 10 — Rebuild (probe `False`)

- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")`
- [ ] Truncate: `q(conn, TRUNCATE); q(conn, "RESET ROLE;")`
- [ ] `./manage.py setup_logical_replication` (no flags).
- [ ] Watch the copy until everything is at `r`. Ignore the command's own output:
      `q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")`
  - A table stuck at `d` → truncate that table again.
  - Not all at `r` by the cutoff → **Full rollback**.

---

## Verification

### Task 12 — Structure

- [ ] `q(conn, "SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;")` → `[('r', 29)]`
- [ ] `q(conn, "SELECT * FROM pg_stat_subscription_stats;")` → error counts `0 | 0`
- [ ] Table owners: `connect_superset` × 90 and `rdsadmin` × 1:
  `q(conn, "SELECT tableowner, count(*) FROM pg_tables WHERE schemaname='public' GROUP BY 1;")`
- [ ] Superset can read every table. Expect `[]`:
  ```python
  q(conn, """SELECT c.relname FROM pg_subscription_rel sr JOIN pg_class c ON c.oid = sr.srrelid
             WHERE NOT has_table_privilege('superset_readonly', c.oid, 'SELECT');""")
  ```
  If any tables are listed, re-grant:
  `q(conn, "SET ROLE connect_superset; GRANT SELECT ON ALL TABLES IN SCHEMA public TO superset_readonly;"); q(conn, "RESET ROLE;")`

### Task 13 — Data moves

- [ ] `./manage.py logical_replication_status` — compare to the baseline.
- [ ] Wait a few minutes, run it again, and confirm the counts **moved**.
- [ ] Insert a test row on the primary, confirm it reaches the secondary, delete it, and confirm the
      delete arrives too. Log both statements.
- [ ] The slot on the primary is `active`, and its retained WAL is small and falling.

### Task 14 — Superset

- [ ] Start Superset (only after Tasks 12–13 pass).
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
| Precheck fails | Nothing changed. Fix and retry another day. |
| PostGIS 3.4.6 available after the upgrade | Not a problem. Follow up later. |
| Tables stuck at `d` | Truncate those tables again. |
| Primary disk filling | **Don't** re-enable early. Press on to Task 8. If you must abandon, do the full rollback. |
| `superset_readonly` missing grants | Re-grant (Task 12). |
| Superset broken, data fine | Metadata restore (below). |
| Upgrade failed / replication not reconnected / verification failing by the cutoff | Full rollback (below). |

### Metadata restore

- [ ] Stop Superset (if running).
- [ ] Restore. Exit code `1` is expected, so read the errors (PostGIS owner and comment errors are harmless):
  ```bash
  pg_restore -h <endpoint> -U postgres -d superset_meta --clean --if-exists superset_meta_window_start.dump
  ```
  Do **not** pass `--no-owner` or `--role`.
- [ ] PostGIS is back at 3.4.3 in `superset_meta`.
- [ ] Start Superset, then check logins and one saved chart.

### Full rollback

**R1 — Detach the upgraded instance**

- [ ] Make sure Superset is stopped.
- [ ] If the upgraded instance is reachable and has a subscription (**detach, do not drop**):
  ```python
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub DISABLE;")
  q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")
  ```

**R2 — Check the slot; this picks R5a or R5b**

- [ ] `primary("SELECT active, confirmed_flush_lsn FROM pg_replication_slots WHERE slot_name='tables_for_superset_sub';")`

  | Result | Go to |
  |---|---|
  | Inactive, LSN **equals** the Task 6 record | R5a |
  | LSN **ahead** of the Task 6 record | R5b |
  | No slot | R5b, skipping the slot drop |
  | `active = true` | Stop. Redo R1 and find the holder in `pg_stat_replication`. |

**R3 — Restore the snapshot**

- [ ] Restore with the R0 settings:
  ```bash
  aws rds restore-db-instance-from-db-snapshot \
    --db-instance-identifier <instance-id>-rollback \
    --db-snapshot-identifier superset-pre-pg16-<yyyymmdd>-1 \
    --db-instance-class <R0> --db-subnet-group-name <R0> --vpc-security-group-ids <R0> \
    --db-parameter-group-name <R0 postgres15 group> --option-group-name <R0>
  aws rds wait db-instance-available --db-instance-identifier <instance-id>-rollback
  ```
- [ ] On the new instance: the subscription is **disabled**, `pg_subscription_rel` shows `('r', 29)`,
      and PostGIS is 3.4.3.

**R4 — Move the endpoint**

- [ ] Preferred: swap the instance identifiers, so no config changes are needed:
  ```bash
  aws rds modify-db-instance --db-instance-identifier <instance-id> \
    --new-db-instance-identifier <instance-id>-failed-pg16 --apply-immediately
  # wait until available
  aws rds modify-db-instance --db-instance-identifier <instance-id>-rollback \
    --new-db-instance-identifier <instance-id> --apply-immediately
  # wait until available and the endpoint matches the R0 record
  ```
- [ ] *Or, as the fallback:* update `secondary_db_url` (1Password *Ansible Secrets - Production*) →
      `inv django-settings`. Then edit `SQLALCHEMY_DATABASE_URI` in
      `/home/ccc_superset/www/superset/superset_config.py`, and once Superset is started, update the
      host on the **Connect New** connection before users get access. Keep `SECRET_KEY` unchanged.
- [ ] Restart the Connect containers. (Superset stays stopped until Task 14 in R6.)

**R5a — Resume (slot untouched)**

- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub ENABLE;")`

**R5b — Re-copy (slot advanced or gone).** Do **not** use Task 10's refresh here; it copies nothing.

- [ ] `q(conn, "ALTER SUBSCRIPTION tables_for_superset_sub SET (slot_name = NONE);")`
- [ ] `q(conn, "DROP SUBSCRIPTION tables_for_superset_sub;")`
- [ ] If the slot exists: `primary("SELECT pg_drop_replication_slot('tables_for_superset_sub');")`
- [ ] Truncate: `q(conn, TRUNCATE); q(conn, "RESET ROLE;")`
- [ ] `./manage.py setup_logical_replication` (no flags), then watch until all tables are at `r`.

**R6–R8 — Finish**

- [ ] Run Verification (Tasks 12–14) against the restored instance (engine 15.17).
- [ ] Lift the freeze and deploy. Confirm `migrate_multi` reaches the restored instance.
- [ ] Stop the failed PG16 instance; don't delete it yet. It auto-restarts after 7 days.
- [ ] Keep the snapshot and the dump. Record the trigger, the branch taken and the durations in the
      investigation doc.
