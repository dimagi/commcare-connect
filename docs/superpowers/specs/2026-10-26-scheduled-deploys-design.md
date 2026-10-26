# Scheduled production deploys + automatic release notes

Ticket: CCCT-2932

## Why

Production deploys happen when someone remembers to run the `Deploy` workflow. We want a predictable cadence, every
Monday, Wednesday and Friday at 12:00 UTC, without anyone having to press the button. We also want stakeholders told
what changed: today someone runs the `connect-tech:connect-web-release-notes` Claude Code skill by hand after a release,
and it should happen on its own.

## Goals

1. Production deploys from `main` run automatically Mon/Wed/Fri at 12:00 UTC.
2. Anyone on the team can stop upcoming scheduled deploys from the GitHub UI, before they start.
3. After every successful production deploy, release notes are generated and published to the Confluence Web Release
   Notes page and to Slack `#connect-product-feed`.

## Non-goals

- Staging deploys. The `autostaging` push trigger stays as it is.
- A Slack reminder or heads-up before a scheduled deploy. **The user explicitly declined this.**
- Cancelling from Slack. Cancelling happens in GitHub.
- A "skip just this one slot" mechanism. Turning the schedule off in the GitHub UI is enough.
- Changing how manual or hotfix deploys work.

## Anti-goals

- **Turning off the schedule must not block manual deploys.** If disabling the schedule also disabled `Deploy`, a
  hotfix couldn't ship.
- **A release-notes failure must not mark a good deploy as failed** or send anyone hunting for a deploy problem.
- **No half-working publishing.** If the Confluence or Slack credentials are missing, skip that step and say so. Don't
  error with a cryptic message, and don't post partial content.
- **Claude must not hold the publishing credentials.** It writes the notes. Plain workflow steps publish them.

## Current state

`.github/workflows/deploy.yml`:

- Triggers: push to `autostaging` (staging) and `workflow_dispatch` with `environment` (staging/production) and
  `deploy_type` (scheduled/adhoc). `deploy_type` currently only shows in `run-name`.
- The `deploy` job runs `kamal deploy`. For production it also creates a Sentry release and a GitHub release, with a
  timestamp tag such as `2026-10-26_12-00-03` and `generate_release_notes: true`. It posts start and end messages to
  the production deploy channel (`C09HWCJSVJ6`) and exposes the start message's `slack_ts` as a job output.
- The `running-qa-test` job runs after a successful production deploy. It has `continue-on-error: true`, and on
  failure it posts a warning into the deploy's Slack thread using `needs.deploy.outputs.slack_ts`. **That job is the
  pattern for the new release-notes job.**
- `concurrency: deploy-<env>`, with `cancel-in-progress: false`.

Existing repo secrets: `ANTHROPIC_API_KEY` (the user said the team has an Anthropic API key; using this secret for release notes is assumed), `SLACK_BOT_TOKEN`, `SSH_KEY`, `KNOWN_HOSTS`, `SENTRY_AUTH_TOKEN`,
`QA_SUITE_DISPATCH_TOKEN`. The repo is public.

The release-notes skill is at `plugins/connect-tech/skills/connect-web-release-notes/SKILL.md` in the public repo
`dimagi/dimagi-claude-workflows`. It works in these steps:

- Steps 1–5: find the latest two GitHub releases, list the merged PRs between their dates, read the PR bodies, sort
  them into New / Improvements / Bug Fixes (skipping internal-only changes and revert pairs), and write Markdown.
- Step 6: save the Markdown to `./outputs/release_notes_{version}.md`.
- Step 7: prepend the notes to Confluence page `3870556169` on `dimagi.atlassian.net`, with the version message
  `Add v{version} release notes`.
- Step 8: post a Slack mrkdwn summary to `C08MRUZ8T9A`, ending with a link to the Confluence page.

Steps 7 and 8 use MCP tools tied to a person's login, so they can't run in CI as written.

## Design (decided with the user)

### 1. Schedule

- New workflow file: `.github/workflows/scheduled-deploy.yml`, named e.g. "Scheduled production deploy".
- `on.schedule` cron `0 12 * * 1,3,5`. GitHub may start it a few minutes late, and that's accepted.
- It runs the existing production deploy with `environment: production` and `deploy_type: scheduled`.
- It's a separate file so that disabling it (in the Actions tab, the workflow's **⋯** menu, **Disable workflow**) stops
  only the automatic deploys. `Deploy` stays runnable by hand.

**Builder's call, recommended:** add `workflow_call` to `deploy.yml` with the same two inputs, and have the schedule
call it as a reusable workflow with `secrets: inherit`. The caller must grant the permissions `deploy.yml` needs
(`id-token: write`, `contents: write`, plus `actions: read` if anything below needs it). This keeps everything in one
run. Check how `run-name` and `concurrency`, which both read `inputs.*`, behave when the workflow is called, and check
the `inputs.environment == 'production'` conditions on the downstream jobs.

**My call (not confirmed by the user):** if `main` HEAD is the same commit as the latest GitHub release, the scheduled
run skips the deploy and the release notes and logs "nothing to deploy". Otherwise a no-op deploy would create an empty
GitHub release and post empty release notes. This check applies to scheduled runs only. A manual deploy always deploys.
Key the check off `github.event_name == 'schedule'` in the scheduled workflow, not off `deploy_type`: the manual
dispatch default for `deploy_type` is already `scheduled`, so keying off it would make manual runs skip too.

**CI gate (decided by the user: skip and warn).** Before deploying, the scheduled run checks the CI status of `main`
HEAD, i.e. the latest `CI` workflow run (`ci.yml`) for that SHA. Anything other than a completed, successful run blocks
the deploy, including a failed run, a run still in progress, or no run at all. When that happens, the scheduled run
skips the deploy and posts a warning to the production deploy channel (`C09HWCJSVJ6`). The warning says why, names the
commit, and links the run. It doesn't wait or retry. The next slot tries again. This gate applies only to scheduled
runs, and manual deploys are unchanged. Note that `ci.yml` has `paths-ignore: ['docs/**']`, so a commit that only
touches docs gets no CI run. The builder decides how to handle that, for example by checking the most recent commit
that did run CI.

### 2. Cancelling

Anyone with write access disables the "Scheduled production deploy" workflow in the GitHub Actions UI and re-enables it
when deploys should resume. Nothing reminds anyone to re-enable it, and the user accepted that. A run that has already
started can be cancelled with the normal **Cancel workflow run** button.

### 3. Release notes

Release notes run after **every successful production deploy**: scheduled, manual or hotfix. Every one of them creates
a GitHub release, and the skill is release-based.

**My call:** put this in a new `.github/workflows/release-notes.yml` with both `workflow_call` (used by `deploy.yml`)
and `workflow_dispatch` (for testing and re-running by hand). In `deploy.yml`, a new job calls it with `needs: deploy`,
under the same condition as `running-qa-test`. It runs after the GitHub release step, which already happens because it
runs inside the `deploy` job.

The flow:

1. **Generate (Claude).** Check out `dimagi/dimagi-claude-workflows` at `main` so that skill updates come through
   automatically. Run `anthropics/claude-code-action` with `ANTHROPIC_API_KEY`. The prompt tells Claude to follow
   steps 1–6 of that `SKILL.md`, to skip steps 7–8, and instead write three files:
   - the Markdown notes (the skill's step 6 output, uploaded as a workflow artifact),
   - the same notes as a **Confluence storage-format** (XHTML) fragment, ready to prepend,
   - the Slack message as plain Slack mrkdwn, following the skill's step 8 formatting rules. One difference: use
     `*single*` asterisks for bold. The skill's `**double**` rule exists only because its MCP tool converts Markdown,
     and the raw Slack API doesn't.

   **My call:** the prompt overrides the skill's step 2 on one point. It selects PRs by the commit range between the
   previous and latest release tags (`git log PREV_TAG..LATEST_TAG`, or exact timestamps), not by
   `merged:PREV_DATE..LATEST_DATE`. Same-day releases already happen (two on 2026-10-15). With whole-day dates, every
   later release that day would list PRs again, and the automatic posting would publish those duplicates. Fixing the
   skill upstream as well would be good, but this change doesn't depend on it.

   Claude needs read-only `gh` access (`GH_TOKEN` = `GITHUB_TOKEN` with `contents: read`, `pull-requests: read`) and
   file writing. Restrict its tools to what that takes, with no network tools beyond `gh`. If Claude finds no
   user-facing changes, it writes nothing and nothing gets posted.
2. **Publish to Confluence (script).** Only if `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` are both set. GET page
   `3870556169` with its storage body and version number. Build the new body as the new fragment + `<hr/>` + the
   existing body. PUT it with version+1 and the message `Add v{version} release notes`. The credentials belong to an
   Atlassian **service account** with a scoped API token, so requests go through the gateway,
   `https://api.atlassian.com/ex/confluence/<cloudId>/...` with Basic auth (email:token). Look up the cloud ID once at
   `https://dimagi.atlassian.net/_edge/tenant_info` and hardcode it. The token has scopes `read:page:confluence` and
   `write:page:confluence`, so use REST v2 page endpoints.
3. **Publish to Slack (script).** Call `chat.postMessage` to `C08MRUZ8T9A` with `SLACK_BOT_TOKEN`. The message ends
   with the Confluence page link from the skill. It's posted even if the Confluence step was skipped.
4. **Failure handling.** The job has `continue-on-error: true`. If generation fails, any publish step fails, or the
   Confluence secrets are missing, post one warning into the deploy's Slack thread (`C09HWCJSVJ6`, `thread_ts` from
   `needs.deploy.outputs.slack_ts`) that says which part failed or was skipped and links the run. This follows the
   pattern of the existing "Notify QA trigger failure" step. When run by `workflow_dispatch` there's no thread, so post
   the warning as a top-level message or just fail the run (builder's call).

**Version label:** the skill's `{version}` is the release tag name (e.g. `2026-10-26_12-00-03`).

### 4. One-time setup (outside the code, by people)

These steps go in `deploy/README.md` as part of this change:

1. An Atlassian org admin creates a service account (admin.atlassian.com, **Directory → Service accounts**) and gives
   it Confluence access on `dimagi.atlassian.net`. They grant it View + Add/Edit in the `connectpublic` space, check
   that the Web Release Notes page has no edit restrictions that exclude it, and create a scoped API token
   (`read:page:confluence`, `write:page:confluence`) with an expiry.
2. A repo admin adds the secrets `CONFLUENCE_EMAIL` (the service account's generated email) and `CONFLUENCE_API_TOKEN`.
   A copy goes in the 1Password **Connect Tech** vault, with a reminder set before the token expires.
3. In `#connect-product-feed`, run `/invite` for the bot that owns `SLACK_BOT_TOKEN`.

If service accounts aren't available on Dimagi's Atlassian plan, a team member's personal scoped API token goes in the
same two secrets. That token uses Basic auth too, but against `https://dimagi.atlassian.net/wiki/api/v2/...` instead of
the gateway. Pick the base URL accordingly: it can be a third, optional setting, or detected from the email domain.
That choice is the builder's.

## Constraints

- Follow the repo's robustness rules (AGENTS.md): degrade gracefully when config is missing, say why, and set explicit
  timeouts on outbound calls (`curl --max-time` or similar).
- `deploy.yml`'s concurrency group and `cancel-in-progress: false` stay as they are.
- Keep `deploy.yml`'s existing behaviour for staging and manual production deploys identical.

## Left to the builder

- Exact workflow, job and step names.
- Whether the publish steps are inline shell (`curl` + `jq`) or a small script in `.github/scripts/`.
- The prompt wording for Claude, and which model to use (the latest Sonnet is a sensible default for cost).
- How the job checks whether secrets are present (e.g. map them to env vars and test them in a step).

## Done when

- `scheduled-deploy.yml` exists, and a dry check (`actionlint`, plus a manual reasoning-through of `inputs.*` under
  `workflow_call`) shows that it deploys production from `main` with `deploy_type: scheduled`.
- Disabling the scheduled workflow leaves `Deploy` runnable by hand.
- A scheduled run against a `main` HEAD without green CI skips the deploy and posts a warning to the deploy channel.
- `release-notes.yml` can be run by hand against the latest release. With the Confluence secrets set, it prepends to the
  page and posts to Slack. Without them, it posts to Slack only and warns that Confluence was skipped.
- A release-notes failure leaves the deploy run green and posts a warning in the deploy thread.
- `deploy/README.md` documents the schedule, how to turn it off, the release-notes flow, and the one-time setup.
