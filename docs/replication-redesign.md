# Auto-discovered logical replication — design options

## Goal

Replace the hand-maintained `REPLICATION_ALLOWED_MODELS` list with an
auto-discovery mechanism so new tables are picked up without code changes,
while preserving a clear, low-friction way to *exclude* tables that contain
sensitive data. Setup should run automatically (likely tied to migrations)
rather than via a developer-invoked interactive command.

## Current state (one-page recap)

- `commcare_connect/multidb/constants.py` holds an explicit allowlist of
  ~25 Django model classes and the publication/subscription names.
- `./manage.py setup_logical_replication` is interactive: it prompts for
  primary-side replication credentials and secondary-side superuser
  credentials, then issues `CREATE PUBLICATION` / `ALTER PUBLICATION SET
  TABLE` on the primary and `CREATE SUBSCRIPTION` / `ALTER SUBSCRIPTION
  REFRESH PUBLICATION` on the secondary.
- A developer runs it after every model addition or removal.
- `./manage.py migrate_multi` runs Django migrations on every configured
  database in parallel (gevent), so the secondary keeps its schema.
- Routing: `ConnectDatabaseRouter` forces all ORM reads/writes to the
  default DB; the secondary is populated solely via Postgres logical
  replication and consumed by Superset.

## Decision dimensions

The following 3 decision dimensions will be discussed:
1. Where we define which models should be included/excluded from replication
2. When we trigger the update to the replication object in postgres to add/remove tables
3. Where we read the necessary credentials from   

### D1. Where does the "exclude this table" marker live?

This dimension is for deciding where we will specify which tables to replicate to superset.

**Option A — Central denylist constant** (symmetric flip of today's list)

```python
# commcare_connect/multidb/constants.py
REPLICATION_EXCLUDED_MODELS = [
    SomePIIModel,       # PII
    SomeSecretsModel,   # secrets
]
```

Discovery: `[m for m in apps.get_models() if m not in EXCLUDED]`.

- **Pros:** symmetrical with today's pattern; one place to audit all
  excluded tables; trivially diff-able in PR review; no Django magic.
- **Cons:** marker lives away from the model — adding a new sensitive
  model means remembering to edit a file in another app; central file
  becomes a magnet for merge conflicts; nothing physically prevents
  forgetting.

**Option B — Twin lists (included + excluded), CI-enforced membership**

Maintain two central constants and require every model to appear in
exactly one of them. A CI check (or `AppConfig.ready()` system check)
fails if any model in `apps.get_models()` is missing from both lists.

```python
# commcare_connect/multidb/constants.py
REPLICATION_INCLUDED_MODELS = [
    Opportunity, OpportunityAccess, UserVisit, ...
]
REPLICATION_EXCLUDED_MODELS = [
    SomePIIModel,       # PII
    SomeSecretsModel,   # secrets
]

# Enforced via a Django system check (runs on `manage.py check`, in CI,
# and on `runserver`):
unclassified = set(apps.get_models()) - set(INCLUDED) - set(EXCLUDED)
if unclassified:
    raise SystemCheckError(unclassified)
```

- **Pros:** every model is *explicitly* classified — no implicit default,
  so adding a new model without a deliberate decision fails a Django
  system check (and therefore CI);
  symmetric audit trail (both "what is replicated" and "what is
  deliberately not" are visible in one place); reviewable as a single
  diff on every PR that adds a model; uses plain class references (no
  Django Meta magic, no per-model startup cost beyond one check);
  naturally integrates with the existing
  `commcare_connect/multidb/constants.py` layout.
- **Cons:** maintains *two* lists instead of one — every model add/remove
  touches the central file (merge-conflict magnet, scales linearly with
  table count); marker still lives away from the model definition
  (same con as Option A); developers must remember to update on rename
  or move; only protects at the table level — adding a sensitive
  *column* to an already-included model is invisible (same blind spot
  as the other table-level options).

**Option C — Base class / mixin**

```python
class SensitiveModel(BaseModel):  # marker base
    class Meta:
        abstract = True

class SomePIIModel(SensitiveModel):
    ...
```

Discovery: `issubclass(m, SensitiveModel)`.

- **Pros:** strongly typed; very visible — the base class is right at
  the top of the model definition; impossible to "forget the flag" if
  the dev chooses the right base.
- **Cons:** requires touching every sensitive model's method resolution order; awkward when
  a model already has a deliberate base (e.g. `BaseModel`,
  `AbstractUser`); creates a parallel base-class hierarchy that other
  engineers may copy by accident; less ergonomic for one-off exceptions.

**Option D — Decorator on the model class**

```python
@not_replicated
class SomePIIModel(BaseModel):
    ...
```

Discovery: decorator pushes the model into a module-level set.

- **Pros:** lives with the model; explicit; cheap to implement.
- **Cons:** non-idiomatic for Django (we don't decorate models
  elsewhere); the set lives in module-import-order — fine in practice but
  surprising; no enforcement that every model is classified (a developer
  who forgets the decorator silently gets the default).

**Option E — Per-app declaration in `AppConfig`**

```python
class UsersConfig(AppConfig):
    excluded_from_replication = ["SomePIIModel", "SomeSecretsModel"]
```

- **Pros:** keeps the marker in the app that owns the model.
- **Cons:** uses strings, not class refs (typo-prone); marker isn't
  next to the model definition; least familiar of the options.

---

### D2. What triggers the publication refresh?

This dimension is for deciding when the publication SQL actually runs.

#### Background: ordering constraint imposed by logical replication

Postgres logical replication only replicates row events — never DDL. For
a newly-added model to start streaming to the secondary, four steps must
happen in this order:

1. `CREATE TABLE` on the **primary** (the model's Django migration).
2. `CREATE TABLE` on the **secondary** (the same migration applied to
   the secondary DB).
3. `ALTER PUBLICATION tables_for_superset_pub SET TABLE ...` on the
   **primary** — adds the new table to the publication.
4. `ALTER SUBSCRIPTION tables_for_superset_sub REFRESH PUBLICATION` on
   the **secondary** — triggers the initial `COPY` of the new table
   from primary → secondary, then puts it into streaming mode.

If step 4 runs before step 2, the initial `COPY` lands in a missing
target table and errors. If step 4 runs before step 3, the table simply
isn't in the publication yet and `REFRESH` is a no-op for that table.

`migrate_multi` runs primary and secondary migrations **in parallel**
(via `gevent.spawn` + `joinall`), so there is no ordering guarantee that
either DB finishes first — only that both are done when `joinall`
returns. Any trigger that fires *inside* the migration plan, or as soon
as one DB's `migrate` finishes, races against the other DB. This rules
out two otherwise-plausible designs:

- A `post_migrate` signal that issues the publication/subscription SQL
  fires per-database as each `migrate` finishes — so it can run on the
  secondary before the primary's `ALTER PUBLICATION` (silent miss), or
  cross-connect to the secondary before its `CREATE TABLE` has landed
  (initial `COPY` errors). Making it safe means coordinating across two
  `post_migrate` firings, effectively reinventing a join.
- A `RunPython` data migration in the `multidb` app runs *inside* the
  primary's migration plan while the secondary is still migrating in
  parallel — same race, plus the additional problems that `RunPython`
  can't cleanly reach across to a second database mid-plan and
  `CREATE PUBLICATION` / `ALTER SUBSCRIPTION` aren't transactional in
  the way Django data migrations expect.

The only safe place to issue this SQL is **after `migrate_multi`'s
`joinall` has returned**, i.e. after both DBs are known to be at the
new schema.

#### Trigger: separate management command, invoked by CI/CD

Keep refresh as `./manage.py refresh_replication`, but make it
non-interactive and call it explicitly in the deploy pipeline (Kamal),
sequenced after `migrate_multi` returns:

```
1. migrate_multi          # both DBs reach the new schema after this returns
2. refresh_replication    # ALTER PUBLICATION (primary), ALTER SUBSCRIPTION REFRESH (secondary)
```

By the time the command runs, `migrate_multi` has joined both parallel
`migrate`s, so the schema is guaranteed to exist on both sides and the
command can sequence the `ALTER PUBLICATION` → `ALTER SUBSCRIPTION
REFRESH` steps internally. The deploy pipeline is also the *one* place
that needs replication credentials.

The main cost is that it runs on every deploy, mitigated by the
short-circuit below.

#### Short-circuit: skip the refresh when the publication is already in sync

The refresh logic can cheaply check whether the publication's current
table set already matches the desired set, and exit early when it does.
Postgres exposes the live publication membership via
`pg_publication_tables`:

```python
from django.apps import apps
from django.db import connection
from commcare_connect.multidb.constants import PUBLICATION_NAME

def is_publication_in_sync() -> bool:
    desired = {m._meta.db_table for m in get_replicated_models()}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_publication_tables WHERE pubname = %s",
            [PUBLICATION_NAME],
        )
        actual = {row[0] for row in cursor.fetchall()}
    return desired == actual
```

#### Gating: an env flag for whether replication runs on deploy

Since not all environments have replication set up we should gate when
this happens. We can thus add a `REPLICATION_ENABLED = env.bool("REPLICATION_ENABLED",
default=False)` in `base.py` — set to `True` only on the environment(s)
that should replicate (via the per-environment `docker.env`). The refresh
command checks it first and skips as a no-op when it's `False`.

---

### D3. How are credentials handled in a non-interactive context?

This dimension is for deciding how replication and subscription credentials reach a non-interactive
run.

Today the `setup_logical_replication` command prompts for: 
- primary DB replication user `username` (defaults to `postgres_repl`)
- secondary DB superuser credential
- primary DB user credentials with replication priviledge 
- secondary DB superset username (defaults to `superset_readonly`).

Most of these are needed only for initial subscription
setup; a steady-state refresh has a smaller surface area.

**Option A — All-in-one, creds from env / settings**

Update the existing `setup_logical_replication` command to read the necessary
credentials from the env file:
- `REPL_PRIMARY_REPL_USER`
- `REPL_PRIMARY_REPL_USER_PASSWORD` (embedded into the `CREATE
  SUBSCRIPTION ... CONNECTION '...'` string)
- `REPL_SECONDARY_SUPERUSER`
- `REPL_SECONDARY_SUPERUSER_PASSWORD`
- `REPL_SECONDARY_SUPERSET_USER`

Let's evaluate some tradeoffs:
- **Pros:** one command does everything (and already exists).
- **Cons:** adds the secondary superuser password (and the primary
  replication user's password) to the env file on every host that runs
  deploys — strictly more secret exposure than Option B, which keeps
  the superuser credential confined to one-time bootstrap.

**Option B — Split lifecycle** (bootstrap vs. refresh)

Today's single interactive command does two very different jobs:

1. **Bootstrap (once per environment)** — `CREATE PUBLICATION` on the
   primary, `CREATE SUBSCRIPTION` on the secondary, plus the GRANT
   statements for the replication user (`postgres_repl`) and the
   Superset reader (`superset_readonly`). This is the part that needs
   cross-database superuser credentials, including one-time use of a
   secondary superuser.
2. **Refresh (every deploy / every model change)** — recompute the
   desired table set and run `ALTER PUBLICATION ... SET TABLE` on the
   primary, then `ALTER SUBSCRIPTION ... REFRESH PUBLICATION` on the
   secondary. Postgres has no per-statement `GRANT` for these
   commands; the right to run them comes from *owning* the object.
   Bootstrap therefore transfers ownership of each object to the
   role that will need to alter it during refresh:
   - on the primary: `tables_for_superset_pub` is owned by the app's
     existing primary DB role.
   - on the secondary: `tables_for_superset_sub` is owned by the
     app's existing secondary DB role (the same role `migrate_multi`
     already uses). Subscription ownership is the minimum
     authorisation required to call `ALTER SUBSCRIPTION ...
     REFRESH PUBLICATION` and does not require superuser.

This option keeps those two jobs on different lifecycles. The
bootstrap stays a one-time operator action with the existing
interactive prompts (or a separate, rarely-run command). The refresh
becomes a fully automatic, non-interactive command the deploy
pipeline runs, sequenced after `migrate_multi` returns. It reuses
the same primary and secondary connections Django already has
configured for `migrate_multi` — no new credentials and no superuser
anywhere in the recurring path.


- **Pros:** no superuser password lives outside the one-time
  bootstrap — the secondary superuser is typed by an operator at
  bootstrap and forgotten; the recurring path uses only the
  connections `migrate_multi` already has, with two ownership
  transfers established at bootstrap (the app's primary role owns
  `tables_for_superset_pub`; the app's secondary role owns
  `tables_for_superset_sub`); no new secrets in app config or the
  deploy pipeline; deploys can't accidentally re-issue
  `CREATE SUBSCRIPTION` or mutate GRANTs.
- **Cons:** two code paths to maintain (bootstrap command + refresh
  command); the app's secondary role gains subscription ownership
  on top of its existing schema-migration privileges, so a
  compromise of that role can also mutate the subscription (still
  bounded — no superuser, no authority to grant privileges to other
  roles); if an environment is rebuilt from scratch and the
  operator forgets the bootstrap step, deploys will appear to
  succeed but nothing will actually replicate until someone notices
  and runs bootstrap; documenting "you must run bootstrap once" is
  a process risk. 

---

## Recommendation
The following options are recommended:

- D1.B: Maintaining two lists makes it trivial to enforce explicit thinking about which models should be replicated and which ones not.
- D2: deploy-pipeline command, sequenced after `migrate_multi` returns
  (the only safe trigger — see D2 background on the ordering constraint
  imposed by logical replication)
- D3.A: Since we already have the command written, swapping the inputs for reading the necessary credentials should be minimum effort while accepting the trade-off that the secondary superuser password (and primary replication user password) stay in env on every deploy host, rather than doing the ownership-transfer work D3.B would require.

### Out of scope

- **Column-level sensitivity.** Every D1 option classifies at the table
  level. Adding a sensitive *column* to an already-included model will
  silently flow to the secondary; this redesign does not address that.
- **`logical_replication_status.py`.** This command also imports
  `REPLICATION_ALLOWED_MODELS` and prompts for secondary superuser
  credentials. It will need the same rename and the same env-var
  treatment as `setup_logical_replication` for consistency.

### Example flow for adding a new model
1. Developer adds a new model in the code.
2. A Django system check (run by `manage.py check` locally and in CI) fails unless the new model is specified in either `REPLICATION_INCLUDED_MODELS` or `REPLICATION_EXCLUDED_MODELS`.
3. On deploy, after `migrate_multi` has executed, a new command will be run which
   - skips as a no-op if `REPLICATION_ENABLED` is `False`
   - checks to see if the publication is in sync with the codebase
   - if so: exit (noop)
   - if not: invoke `setup_logical_replication` command (which obtains credentials from django settings)


