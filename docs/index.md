# CommCare Connect developer docs

CommCare Connect is the Django + PostGIS web application behind Connect: organizations create paid opportunities,
community health workers learn and deliver services through CommCare apps, and Connect tracks their visits,
verifies them and records payments. It integrates with CommCare HQ and ConnectID.

## Start here

- **New to the codebase:** [Getting started](getting-started.md) sets up a local environment,
  [Codebase overview](codebase-overview.md) explains how the app is laid out and what its overloaded terms mean, and
  [Conventions](conventions.md) covers code style, robustness rules and testing.
- **Working locally:** [Local development](local-development.md) covers users, sample data, CommCare HQ login,
  Celery, tests and a command reference.
- **Understanding a feature:** the [Apps](apps/opportunity/index.md) section documents the opportunity app,
  payments and the form receiver; [Topics](audit-history.md) covers cross-cutting machinery such as audit history
  and feature flags.
- **Opening a pull request:** [Pull requests](contributing/pull-requests.md) and
  [Dependency management](dependency-management.md).
- **Deploying:** [Deployment](deployment.md) covers staging and production deploys, server access, logs and
  Django settings.

## About this site

Most pages include a README that lives next to the code it documents, so there is one copy of each doc. To change
a page, edit that README (each stub under `docs/` names it), not the stub. The site is built with
[Zensical](https://zensical.org) from `zensical.toml`; preview it with `uv run --group docs zensical serve`.
