# Automated staging for Connect

## Overview

Today, to get a branch onto Connect staging, you hand-edit
[`commcare-connect-staging.yml`](https://github.com/dimagi/staging-branches/blob/main/commcare-connect-staging.yml)
and run `deploy/rebuildstaging` on your own machine. Nothing rebuilds staging until someone runs the script
again.

This spec has two parts:

1. **Automated rebuild, the same as HQ's.** GitHub Actions rebuilds `autostaging` whenever a listed branch
   gets a push or the YAML changes, and then deploys it. Merged branches are removed from the YAML
   automatically. HQ's setup is described in
   [automated-rebuild-trigger.md](https://github.com/dimagi/staging-branches/blob/main/docs/automated-rebuild-trigger.md).
2. **`inv staging-add`.** Adds the current branch to the YAML. For a compatibility branch (one that merges
   two conflicting branches), it works out the right position in the list from git history.

Conflicts still stop the build, as they do in HQ today.

## Part 1: Automated rebuild

### How it fits together

Staging runs whatever is on the `autostaging` branch. To build that branch, git-build-branch merges every
branch listed in the YAML onto `main` and force-pushes the result. A push to `autostaging` then starts
`deploy.yml`, which deploys it to the staging server.

At the moment a developer runs this build on their own machine. After this change, GitHub Actions runs it.
The work is split across two repos because the YAML lives in staging-branches, while the code and the
deploy live in commcare-connect:

| Workflow | Repo | New? | What it does |
|---|---|---|---|
| `rebuild-staging.yml` | commcare-connect | New, copied from [HQ's](https://github.com/dimagi/commcare-hq/blob/master/.github/workflows/rebuild-staging.yml) | Builds `autostaging`. If the code changed, pushes it and starts `deploy.yml`. Also removes merged branches from the YAML. |
| `deploy.yml` | commcare-connect | Exists, unchanged | Deploys `autostaging` to staging. |
| `trigger-commcare-connect-rebuild.yml` | staging-branches | New, copied from `trigger-commcare-hq-rebuild.yml` | When the Connect YAML changes, starts `rebuild-staging.yml` in commcare-connect. |
| `add-connect-branch.yml`, `remove-connect-branch.yml` | staging-branches | New, copied from `add-branch.yml` and `remove-branch.yml` | Add or remove an entry in the Connect YAML, then start the Connect trigger. See "Connect copies of the YAML workflows". |

The shared `add-branch.yml` and `remove-branch.yml` stay as they are, so HQ isn't affected. The Connect copies
can be merged back into them later.

### What starts a rebuild

1. **A developer pushes to a branch that's on staging.**
   Every push to commcare-connect starts `rebuild-staging.yml`. It first checks whether the pushed branch is
   in the YAML, and stops if it isn't. If it is, it builds `autostaging`. If the result differs from what's
   on staging now, it pushes and starts `deploy.yml`.

2. **Someone changes the list of branches.**
   This can be a hand edit to the YAML, or a run of `add-connect-branch.yml` or `remove-connect-branch.yml`
   (`inv staging-add` in Part 2 uses the first). The change starts `trigger-commcare-connect-rebuild.yml` in
   staging-branches. That starts `rebuild-staging.yml` in commcare-connect, which builds and deploys as in 1.

3. **A PR is merged to `main`.**
   The merge is a push to `main`, so `rebuild-staging.yml` runs.
   - If the merged branch is in the YAML, it no longer needs its own entry. The workflow runs
     `remove-connect-branch.yml` to take it out, and that change starts a rebuild as in 2.
   - If it isn't in the YAML, `main` has still changed, so the workflow rebuilds as in 1.

4. **The build hits a conflict.**
   Two listed branches can't be merged together. The build fails, nothing is pushed or deployed, and staging
   keeps its current version. A Slack message names the conflicting branches. Someone fixes it with a
   compatibility branch, as today.

Branches in forks (`owner:branch`) aren't catered for. A push to a fork can't start workflows in
commcare-connect, so fork branches only update when something else causes a rebuild.

### Connect copies of the YAML workflows

`add-connect-branch.yml` and `remove-connect-branch.yml` start as copies of `add-branch.yml` and
`remove-branch.yml`, with these changes:

- **The file is always `commcare-connect-staging.yml`,** so there's no `file` input.
- **`add-connect-branch.yml` has a `before` input,** passed to a new `edit_branches.py add --before <branch>`
  option that inserts the entry at that position. The option is additive, so the shared workflows keep
  working.
- **Retry the push.** Today a plain `git push` fails if someone else changed the YAML first. If an automatic
  removal loses that race, a deleted branch stays listed and every build fails. Both copies share a
  concurrency group, and on a rejected push they run `git pull --rebase`, re-apply the edit and push again.
- **Start `trigger-commcare-connect-rebuild.yml` at the end,** because commits made with `GITHUB_TOKEN`
  don't start it.

### Other changes and setup

- Every rebuild that changes the code deploys. There are no fixed deploy times.
- `deploy/rebuildstaging` exits 0 when the build fails. Add `exit 1`.
- Install the **Staging Branches Automations** GitHub App on commcare-connect. Add
  `STAGING_BRANCHES_APP_ID` and `STAGING_BRANCHES_APP_PRIVATE_KEY` as repo secrets.

### Differences from HQ's `rebuild-staging.yml`

Most of these exist because in Connect a rebuild also deploys:

- **Put the `concurrency` group on the `rebuild-staging` job, not the whole workflow.** HQ's workflow-level
  group means a push to any branch queues behind a running rebuild. GitHub keeps only one waiting run, so a
  push to an unlisted branch can replace the waiting run for a listed branch, and that change never reaches
  staging. At job level, `check-branch` always runs, and a replaced waiting rebuild is harmless because the
  newer one builds the latest state.
- **Exact branch match with `yq`, not `grep`.** `grep` also matches substrings and commented-out lines, which
  would cause needless deploys.
- **Build with `--no-push`, then push only if the tree changed.** Every build makes new merge commits, so
  without this check every rebuild would deploy.
- **Start `deploy.yml` with `gh workflow run`.** A push made with `GITHUB_TOKEN` doesn't trigger other
  workflows. The job needs `actions: write` for this, as well as HQ's `contents: write`.
- **Find the merged branch through the GitHub API.** HQ reads it from the commit message
  `Merge pull request #N from owner/branch`, which squash and rebase merges don't produce. Both are allowed on
  Connect, and GitHub deletes merged branches, so a missed removal leaves a deleted branch in the YAML and
  stops every build. `gh api repos/dimagi/commcare-connect/commits/<sha>/pulls` returns the PR for every
  merge type.
- **Post conflicts to Slack,** in the staging deploy channel `deploy.yml` already posts to. HQ's job only
  fails.
- **Pin the git-build-branch version.** HQ installs the latest release in a job that can push to the repo.

## Part 2: `inv staging-add`

### Flow

1. Read the current branch. Stop if it's `main`, or if it isn't pushed to `origin`.
2. Fetch the YAML. If the branch is already listed, say so and exit. If it's commented out, say that instead.
3. Work out the position (below).
4. Run a trial build with the branch inserted. If the branch adds a conflict, show it and stop.
5. Ask `Add before '<branch>'? [y/N]`, or "at the end" for a normal branch.
6. Run `add-connect-branch.yml` with `gh workflow run`, wait for it with `gh run watch`, and report whether it
   succeeded. Part 1 then rebuilds and deploys.

### Position

For each listed branch X, its **own commits** are those not on `main` and not on any listed branch X was cut
from. The new branch **contains** X if it has at least one of X's own commits.

- Contains 0 or 1 listed branches: add at the end.
- Contains 2 or more: insert just before the first of them in the list. Those branches then merge as no-ops
  after it.

Checking for "at least one commit", rather than X's whole history, still detects X after X gets new commits.
Excluding the branches X was cut from stops stacked branches from matching. Tested on the two compatibility
branches in today's YAML: both were detected correctly, and a stacked branch was left out.

### Trial build

Run git-build-branch without `--push` in a temporary worktree, on a copy of the YAML. It catches conflicts
that history can't predict, and source branches that were rebased. It also warns about conflicts for
normal branches.

Tested: `git-build-branch <copy> fetch rebuild` in a detached worktree built today's list in about 9 seconds
and left the worktree clean. The task has to account for four things:

- **Pass the actions `fetch rebuild`, so the default `sync` action is skipped.** `sync` checks out every listed
  branch that exists locally, which fails for a branch checked out in another worktree. It also
  fast-forwards the developer's local branches.
- **Write each entry without a colon as `origin:<branch>` in the copy.** git-build-branch prefers a local
  branch over `origin/<branch>`, so without this the trial would merge the developer's local copies, which
  may be stale. Leave fork entries (`owner:branch`) alone, or the fetch fails.
- **Set `name:` in the copy to a throwaway branch and delete it afterwards.** Branches are shared between
  worktrees, so the trial would otherwise overwrite the local `autostaging`.
- **Compare with a trial of the list as it is now.** git-build-branch fails on any conflict or missing
  branch. If staging is already broken, every trial would fail. Run a second trial without the new branch,
  and only stop on conflicts or missing branches that the new branch adds.

### Changes

- **staging-branches:** `trigger-commcare-connect-rebuild.yml`, `add-connect-branch.yml`,
  `remove-connect-branch.yml`, and the `--before` option in `edit_branches.py`.
- **commcare-connect:** a `staging_add` task in `tasks.py`, with options `--branch`, `--yes` and `--skip-trial`.
  Keep the git logic in plain functions and unit-test it against a temporary repo.

Developers need `gh` logged in and permission to run workflows in staging-branches.
