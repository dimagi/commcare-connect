# Connect

Connect is a Django + PostGIS web application: organizations create paid opportunities, community health workers
learn and deliver services through CommCare apps, and Connect tracks their visits, verifies them and records
payments. It integrates with CommCare HQ and ConnectID.

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/commcare-connect)

The [developer docs site](https://dimagi.github.io/commcare-connect/) publishes this README together with the
app and deployment docs.

<!-- --8<-- [start:local-setup] -->

## Local setup

This assumes you will use the docker compose file in this repo to run services. If that is not the case you may
need to edit some settings.

### Python environment

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) to manage Python versions and
dependencies:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create a virtual environment in `.venv` and install the requirements:

```bash
uv sync
```

Activate it (or prefix commands with `uv run`):

```bash
source .venv/bin/activate
```

Install the git hooks and run them once:

```bash
prek install
```

```bash
prek run -a
```

Create your env file and edit the settings as needed (or export settings directly):

```bash
cp .env_template .env
```

### GeoDjango / PostGIS

Connect uses GeoDjango and PostGIS. To learn more about the required dependencies, see the
[Django documentation](https://docs.djangoproject.com/en/5.2/ref/contrib/gis/install/geolibs/#geolibs-list).

**For macOS (Intel / Apple Silicon)** – using Homebrew:

```bash
brew install gdal geos proj
```

Django may struggle to find the GDAL and GEOS libraries even after they are installed. Get your local library
paths:

```bash
ls $(brew --prefix gdal)/lib/libgdal.dylib
```

```bash
ls $(brew --prefix geos)/lib/libgeos_c.dylib
```

Then set them in your `.env` file:

```
GDAL_LIBRARY_PATH=...
GEOS_LIBRARY_PATH=...
```

Install [WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#macos):

```bash
brew install weasyprint
```

**For Ubuntu / Debian Linux:**

```bash
sudo apt-get install -y binutils libproj-dev gdal-bin
```

**Enabling the PostGIS extension**

No migration creates the `postgis` extension. The `inv up` setup works because the `postgis/postgis` image
enables it for you when it initialises the database.

If you are using your own PostgreSQL instead, create the extension in the target database before migrating, or
the first spatial migration will fail:

```bash
psql -d commcare_connect -c "CREATE EXTENSION IF NOT EXISTS postgis;"
```

### Running the app

Start the docker services (PostgreSQL/PostGIS and Redis):

```bash
inv up
```

Install the JS dependencies and build them (add `-w` to rebuild on changes):

```bash
npm ci
```

```bash
inv build-js
```

Migrate the database and start Django:

```bash
./manage.py migrate
```

```bash
./manage.py runserver
```

### Working in git worktrees

To work on several branches at once, give each one its own git worktree. The repository has
hooks for [worktrunk](https://worktrunk.dev/) (`wt`) in `.config/wt.toml`.

Set up the main checkout first (above): worktrees copy its `.env`, and the docker compose
services must be running.

#### Create a worktree

By default, a worktree gets its own empty database and its own Redis database:

```bash
wt switch --create my-branch
```

`wt new my-branch` does the same; it is a shorter alias defined in `.config/wt.toml`.

The database has no data in it; create a user with `./manage.py createsuperuser`.

To use the main checkout's database and Redis database instead, for example to work with data
you already have, pass `--share=1`:

```bash
wt new my-branch --share=1
```

Setup doesn't run `migrate` on a shared database. If the branch adds migrations, running them
changes the main checkout's schema too, so give that branch its own database instead.

To start Claude Code in the new worktree as soon as it is set up, add `--execute claude`:

```bash
wt switch --create --execute claude my-branch
```

#### Remove a worktree

```bash
wt remove my-branch
```

This drops the databases created for the branch. A worktree created with `--share=1` has none,
so the main checkout's database is left alone.

#### What setup does

When a worktree is created, `scripts/setup-worktree.sh`:

- copies `.env` from the main checkout, and runs `uv sync` and `npm ci`
- creates an empty PostgreSQL database for the branch, `connect_wt_<branch>_<hash>`, and runs `migrate`
- picks a Redis database (1-14) that no other worktree's `.env` uses, and points `REDIS_URL` and
  `CELERY_BROKER_URL` at it, so the branch's Celery worker never runs another branch's tasks
- comments out `SECONDARY_DATABASE_URL`, which would otherwise point at the main checkout's replica

With `--share=1`, it skips the database and Redis steps and copies `DATABASE_URL`, `REDIS_URL`
and `CELERY_BROKER_URL` from the main checkout's `.env` instead. Either way, the JS and CSS then
build in the background.

#### Other worktree tools

Run `scripts/setup-worktree.sh` in the new worktree (add `--share` to use the main checkout's
databases), and `uv run python scripts/worktree.py teardown <branch>` after removing it.

<!-- --8<-- [end:local-setup] -->

<!-- --8<-- [start:local-development] -->

## Local development

### Useful commands

`tasks.py` defines [invoke](https://www.pyinvoke.org/) tasks for common jobs. List them with:

```bash
inv -l
```

To add a Python dependency, use `uv add <pkg>`; see
[Dependency management](https://github.com/dimagi/commcare-connect/blob/main/docs/dependency-management.md)
for the details.

### Setting up your users

You don't need the CommCare HQ social app to create local users. The "Log in with CommCare HQ" button stays
hidden until a `commcarehq` social app exists (see
[Setting up auth with CommCare HQ](#setting-up-auth-with-commcare-hq)).

To create a **superuser**, run the command below. `--email` is not accepted, because the custom `User` model sets
`REQUIRED_FIELDS = []`; set the address afterwards in the admin if you need one.

```bash
./manage.py createsuperuser
```

To create a **normal user account**, go to Sign Up and fill out the form. Local settings use
`ACCOUNT_EMAIL_VERIFICATION = "optional"`, so you are logged straight in; the confirmation email is still printed to
the console if you want to verify the address.

To promote an existing user to superuser:

```bash
./manage.py promote_user_to_superuser <email>
```

For convenience, you can keep your normal user logged in on Chrome and your superuser logged in on Firefox (or
similar), so that you can see how the site behaves for both kinds of users.

### Sample data

To populate a local database with organizations, opportunities, visits and payments
(`generate_sample_data <num_visits> <org_slug>`):

```bash
./manage.py generate_sample_data 50 demo-org
```

The organization is created if the slug does not already exist. Each run first deletes the existing
opportunities, visits, payments, programs and attachments belonging to the two organizations it is about to use,
including the attachment files under `MEDIA_ROOT`. Optional flags:

- `--invited_org_slug` — reuse a specific organization as the invited org. Without it, a new invited org with a
  random slug is created on every run, and the previous one is left behind.
- `--managed_opportunities` — how many managed opportunities to create (default 3)
- `--image_percent` — percentage of visits that get a photo (default 0, none)
- `--audio_percent` — percentage of visits that get one to three audio recordings, each with a stand-in
  transcript and translation (default 0, none)
- `--media_dir` — a directory to search (recursively) for image / audio files to use as the attachments for
  generated visits if requested. Otherwise placeholders are used.

Adding yourself to the generated organizations is a separate step, either through
`/admin/organization/userorganizationmembership/` or the organization's own member admin pages. Note there are
two: the primary org from `<org_slug>` and the invited org, which owns the managed opportunities — join only the
first and those stay hidden. Worker tabs list only workers whose `OpportunityAccess` is accepted, which the
generated ones are.

#### Microplanning, without CommCare HQ

To seed an opportunity with work areas, groups, workers and visits for the Progress Map:

```bash
./manage.py generate_microplanning_data --fake-hq
```

It prints the Progress Map URL and creates the superuser `mpadmin` with password `mpadmin`. `--help` lists the
other options.

Assigning, unassigning, excluding and editing an assigned work area all update cases on CommCare HQ. `--fake-hq`
sends those calls to a stand-in for HQ on `localhost:8001` and gives each worker an HQ user there. Run the
stand-in in its own terminal:

```bash
./manage.py run_fake_hq
```

It answers every case update and prints the request with its body, so you can see what Connect sends. Start it
with `--fail` to answer case updates with an error instead. To see the push notification that assignment sends to
ConnectID too, start the Celery worker with `CONNECTID_URL=http://localhost:8001/connectid`.

### Setting up auth with CommCare HQ

**Expose your local service to the internet**

- Create an account on ngrok and [install ngrok](https://dashboard.ngrok.com/get-started/setup/)
- Create a custom domain on ngrok using this [link](https://dashboard.ngrok.com/domains/new)
- Run `ngrok http --url=[my-unique-subdomain].ngrok-free.app 8000`
- Set `DJANGO_ALLOWED_HOSTS=[my-unique-subdomain].ngrok-free.app` in your `.env` file

**Create an OAuth2 application on CommCare HQ**

- Navigate to https://staging.commcarehq.org/oauth/applications/
- Create a new application with these settings:

| Setting                  | Value                                                                              |
| ------------------------ | ---------------------------------------------------------------------------------- |
| Client Type              | Confidential                                                                       |
| Authorization Grant Type | Authorization Code                                                                 |
| Redirect URIs            | `https://[my-unique-subdomain].ngrok-free.app/accounts/commcarehq/login/callback/` |
| Pkce required            | False                                                                              |

- Copy the Client ID and Client Secret
- Create a new SocialApp locally at http://localhost:8000/admin/socialaccount/socialapp/

**Test the OAuth2 flow**

- Confirm `COMMCARE_HQ_URL` points at `https://staging.commcarehq.org`. That is the default, so you only need to
  set it in `.env` if you have overridden it.
- Navigate to http://[my-unique-subdomain].ngrok-free.app/accounts/login/
- Click the "Log in with CommCare HQ" button
- You should be redirected to CommCare HQ to log in
- After logging in, you should be redirected back to the app and logged in

### Celery

Local settings run Celery tasks eagerly, inside the request (`CELERY_TASK_ALWAYS_EAGER` defaults to `True` in
`config/settings/local.py`), so you don't need a worker. That blocks the request and hides progress UIs, though.
To run tasks the way production does, set `CELERY_TASK_ALWAYS_EAGER=False` in `.env` and start a worker:

```bash
celery -A config.celery_app worker -l info
```

For Celery's import magic to work, run the commands from the folder that holds `manage.py`.

To run [periodic tasks](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html), you'll need to start
the celery beat scheduler service. You can start it as a standalone process:

```bash
celery -A config.celery_app beat
```

or embed the beat service inside a worker with the `-B` option (not recommended for production use):

```bash
celery -A config.celery_app worker -B -l info
```

### Tests

Run the test suite:

```bash
pytest
```

To check your test coverage and generate an HTML coverage report:

```bash
coverage run -m pytest
```

```bash
coverage html
```

Then open `htmlcov/index.html`.

### Live reloading and Sass CSS compilation

```bash
inv build-js -w
```

<!-- --8<-- [end:local-development] -->

## Deployment

The application runs as Docker containers on EC2, deployed with [Kamal](https://kamal-deploy.org/), and
provisioned with Ansible. See [deploy/README.md](deploy/README.md) for deploying to staging and production,
server access, logs, Django settings and logical replication.
