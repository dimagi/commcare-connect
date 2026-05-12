# Superset Table Sync: Options for Updating Replicated Tables Without a Deploy

## Background

Tables are synced to Superset via PostgreSQL logical replication from the primary DB to a read-only secondary DB.
The list of replicated tables is currently defined as `REPLICATION_ALLOWED_MODELS` in
`commcare_connect/multidb/constants.py`.

Adding a table today requires a code change, PR, and a full GitHub Actions deploy (image build → push to ECR →
rolling restart). This couples an infrastructure concern to the release cycle.

---

## Option 1 (Current): Hardcoded Python list

`REPLICATION_ALLOWED_MODELS` is a Python list of model classes checked into the codebase.
The `setup_logical_replication` management command reads this list and issues
`ALTER PUBLICATION ... SET TABLE ...` on the primary and `ALTER SUBSCRIPTION ... REFRESH PUBLICATION`
on the secondary.

**Pros**
- Zero complexity — nothing extra to build or operate
- Full git audit trail; changes go through code review
- Code review acts as an intentional gate on what gets exposed to Superset

**Cons**
- Every table addition is a release event, bundling an infra change with feature code
- Slow — hours to days depending on review and deploy queue
- Whatever is on `main` at the time of the deploy ships alongside the change

---

## Option 2: Environment variable (1Password → Ansible → restart)

Replace the hardcoded list with a `SUPERSET_REPLICATED_TABLES` env var (comma-separated table names).
The management command reads from the env at runtime instead of from the Python constant.

**One-time setup**: a small PR to add the var to `deploy/roles/connect/templates/docker.env.j2`.

**To add a table after that**:
1. Update the value in 1Password (Connect Tech vault)
2. Run `ansible-playbook play.yml -t django_settings` to regenerate `docker.env` on servers
3. `kamal app restart` to reload the env

No image rebuild. No GitHub Actions pipeline. Estimated time: ~10–20 min with infra access.

**Pros**
- Fast after one-time setup
- Decoupled from code releases; table list changes are purely an ops concern
- Low implementation effort — small, low-risk one-time PR
- Uses existing tooling (1Password, Ansible) with no new infrastructure

**Cons**
- Container restart still required — brief interruption or rolling restart risk
- Requires infra access; not self-serve for non-engineers
- Table list lives outside version control after the initial PR

---

## Option 3a: DB-backed config with Django admin + automatic sync

Store replicated table names in a Django model (e.g. `SupersetReplicatedTable`).
Override `save()`/`delete()` or use a post-save signal to immediately issue
`ALTER PUBLICATION ... SET TABLE ...` on the primary and
`ALTER SUBSCRIPTION ... REFRESH PUBLICATION` on the secondary.

Django admin provides a UI to add or remove tables — no dev or infra involvement needed.

**Pros**
- Instant and zero-restart — change takes effect immediately at the DB layer
- Fully self-serve via Django admin
- Completely decoupled from code deploys and the release cycle

**Cons**
- Highest implementation effort — new model, admin registration, signal hook, error handling
- DDL credentials (superuser on secondary DB) must be stored as application secrets, increasing security surface
- A failed `ALTER PUBLICATION` silently diverges what admin shows from what Postgres actually replicates;
  needs careful error handling and monitoring
- Coupling a Django admin save to a DDL statement is unusual and harder to reason about under failure

---

## Option 3b: DB-backed config with Django admin + manual sync

Same model and admin UI as 3a, but with no save hooks. The admin UI is only used to maintain the list of
desired tables. A separate step — either a management command or an admin action — reads the model and
applies the `ALTER PUBLICATION` + `ALTER SUBSCRIPTION REFRESH` when explicitly triggered.

**To add a table**:
1. Add the table name in Django admin
2. Run `./manage.py setup_logical_replication` (or trigger an admin action) to apply the change

**Pros**
- Simpler to implement than 3a — no signal hooks, no DDL credentials in app settings
- DDL is only issued on explicit operator intent, not as a side effect of a model save
- Easier to reason about failures — the sync step is explicit and observable
- Still decoupled from code deploys and the release cycle

**Cons**
- Not fully self-serve — applying the change still requires someone to run a command or trigger an action
- Two-step process introduces a window where the admin shows a table that isn't yet replicated
- `setup_logical_replication` is currently interactive (prompts for DB credentials); would need a
  non-interactive mode or a dedicated admin action to be practical

---

## Recommendation

**Option 3b** This option feels like the lowest effort when adding a new table. It's also very straight forward to do. 