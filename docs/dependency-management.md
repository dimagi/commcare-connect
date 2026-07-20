# Dependency Management

## Overview

Python dependencies are managed with [uv](https://docs.astral.sh/uv/). Direct dependencies are declared in `pyproject.toml` using minimum-version ranges (`>=`); the full resolved dependency graph (including transitive deps) is recorded in `uv.lock` and committed to the repo.

Two automated systems keep dependencies up to date and secure:

| Tool | Purpose | Runs |
|------|---------|------|
| **Dependabot** | Opens PRs to bump outdated packages | Weekly (Monday mornings) |
| **pip-audit** | Blocks PRs that introduce known CVEs (Python) | On every PR touching `pyproject.toml` / `uv.lock` |
| **npm audit** | Blocks PRs that introduce known CVEs (JS) | On every PR touching `package.json` / `package-lock.json` |

This mirrors the setup used in [commcare-hq](https://github.com/dimagi/commcare-hq), which also runs Dependabot with the native `uv` ecosystem — keeping dependency tooling consistent across Dimagi repos.

---

## Dependabot

Config: [`.github/dependabot.yml`](../.github/dependabot.yml)

Dependabot reads `pyproject.toml` / `uv.lock` (via the native `uv` ecosystem), `package.json`, and the workflow files under `.github/workflows/`, then opens pull requests when newer versions are available.

**Behaviour:**
- Python minor and patch updates are **grouped into a single weekly PR** (`python-non-major`) to reduce noise
- npm minor and patch updates are **grouped into a single weekly PR** (`npm-non-major`)
- Major version bumps get **individual PRs** so each breaking change can be assessed separately
- GitHub Actions are updated weekly
- Up to 5 concurrent open PRs per ecosystem
- Internal Dimagi git-sourced packages (`xml2json`, `git-build-branch`) are excluded — they have no PyPI versions to track

**Reviewing a Dependabot PR:**
1. Check the linked changelog for breaking changes
2. Confirm the CI `Security Audit` and `CI` jobs pass
3. For major bumps, check whether any direct call sites in our code are affected (Dependabot links to release notes)

---

## pip-audit and npm audit (Security Audit CI job)

Config: [`.github/workflows/security.yml`](../.github/workflows/security.yml)

Runs on every pull request that modifies `pyproject.toml`, `uv.lock`, `package.json`, or `package-lock.json`.

- **pip-audit:** exports the production Python dependency set (`uv export --no-dev --group production`) and runs `uvx pip-audit` against it. Dev-only dependencies (`[dependency-groups.dev]`) are excluded.
- **npm audit:** runs `npm audit --audit-level=high` against the frontend dependency tree.

Both jobs fail if any package has a known vulnerability in the OSV / PyPI / npm advisory databases.

---

## Dependency groups

`pyproject.toml` uses three groups:

| Group | Location | Contents |
|-------|----------|----------|
| Runtime | `[project.dependencies]` | Core app dependencies needed in all environments |
| `production` | `[dependency-groups.production]` | Production-only deps (gunicorn, psycopg2, anymail, storages, allow-cidr) |
| `dev` | `[dependency-groups.dev]` | Development and test-only deps |

pip-audit scans runtime + production. Dev deps are not scanned.

---

## Updating dependencies manually

```bash
# Bump a single package
uv add "<package>>=<version>"

# Regenerate the lock after editing pyproject.toml directly
uv lock

# Verify the lock is consistent without changing it
uv lock --check

# Sync your local environment to the current lock
uv sync
```

After bumping, run the full test suite before committing:

```bash
uv run pytest
uv run pre-commit run -a
```

---

## Adding a new dependency

```bash
uv add <package>                        # runtime dependency
uv add --dev <package>                  # dev/test-only dependency
uv add --group production <package>     # production-only dependency
```

`uv` will update both `pyproject.toml` and `uv.lock` automatically.

---

## Periodic full audit

For a deeper review (major version bumps, EoL packages, CVE triage), run the `/audit-dependencies` skill in Claude Code.
