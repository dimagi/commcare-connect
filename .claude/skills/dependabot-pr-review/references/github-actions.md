# Reviewing a GitHub Actions bump

Read this when the PR only touches `.github/workflows/`. Almost every instinct from
lockfile review inverts here, so the differences are worth stating plainly.

| Lockfile PR                          | Actions PR                                                 |
| ------------------------------------ | ---------------------------------------------------------- |
| One manifest declares the dependency | The "manifest" is every `uses:` line, edited independently |
| Diff size tracks blast radius        | A 2-line diff can cross three majors — size proves nothing |
| CI exercises the change              | The workflow may never run in CI at all                    |
| `lockdiff.py` inventories what moved | No lockfile; `lockdiff.py` has nothing to say              |
| Stale PR → dependency vanished       | Stale PR → **new call sites appeared**                     |

## 1. Find every call site

An action has no single declaration, so the count is the first thing to establish:

```bash
git grep -n "<action-name>" origin/main -- .github/
```

**Compare that count against how many lines the PR changes.** Dependabot patches the call
sites that existed when it opened; ones added since are left behind. GitHub still reports
`MERGEABLE`, so a partial upgrade lands silently and leaves the workflow running two
versions of the same action. Nothing else in the review will surface this.

```bash
gh pr diff <N> --repo dimagi/commcare-connect | grep -c "^+.*uses:.*<action-name>"
```

Then read each call site in full — the inputs one step passes are rarely the inputs
another passes, and a bump can break one and not the others.

**Then trace what the step produces, not just what it consumes.** An action's contract
includes its side effects: exported environment variables, files written to the runner,
step outputs read via `steps.<id>.outputs.*`. The risk often sits in a plain `run:` step
downstream, or a script it shells out to — a credentials action feeding a deploy tool that
calls the AWS CLI, say. Those never appear in a `uses:` line, so grep the rest of the job
for what the step hands off.

## 2. Diff the action's contract across tags

This is the decisive technique, and it is one call per tag. `action.yml` is the action's
complete contract: inputs, outputs, and runtime.

```bash
for tag in <old> <new>; do
  for f in action.yml action.yaml; do
    curl -sfL "https://raw.githubusercontent.com/<owner>/<repo>/$tag/$f" \
      -o "/tmp/action-$tag.yml" && break
  done
done
diff -u /tmp/action-<old>.yml /tmp/action-<new>.yml
```

Try both `action.yml` and `action.yaml` — actions use either. Fetching the raw file keeps
this working where `gh api` isn't available; it needs no auth, since any action you can
reference is a public repo.

**Do this before counting call sites when you have several bumps to get through.** The
contract diff is cheap and self-scaling: a one-line diff (only `runs.using` moved) ends the
review immediately, while a fifty-line diff tells you the review has earned real attention.
Letting its size set the budget beats deciding effort up front.

`action.yml` doesn't always state a default. When it matters — whether an input defaults to
on — read the source at that tag (`src/*.ts` for a TypeScript action) rather than inferring
it; that's far more readable than grepping the bundled `dist/index.js`.

What to read out of the diff:

- **Removed or renamed inputs** — cross-check against what each call site passes. An input
  the action no longer declares is usually ignored silently rather than erroring.
- **Removed outputs** — check whether any step has an `id:` and whether later steps or job
  outputs reference `steps.<id>.outputs.*`.
- **`runs.using`** — node16 and node20 are both **retired**: GitHub removed node20 from
  the runners on 2026-09-23, and JavaScript actions now execute on node24 regardless of
  what they declare. So a runtime bump is an argument _for_ merging — it makes the
  declared runtime match what already happens — rather than a risk. Only self-hosted
  runners complicate this.

For a bundled JS action you can go further and confirm behavior directly, which beats any
changelog:

```bash
curl -sL "https://raw.githubusercontent.com/<owner>/<repo>/<tag>/dist/index.js" > /tmp/a.js
grep -c "<input-name>" /tmp/a.js   # 0 means the input is genuinely gone
```

The grep settles most questions on its own. Actually running the bundle
(`INPUT_FOO=bar node /tmp/a.js`) reproduces the step exactly and is sometimes worth it —
but it executes third-party code, so keep it to a throwaway container with no credentials
in the environment, and never do it inside the review workflow.

## 3. Read recent runs of the workflow

For a deploy or release workflow, the logs answer questions no changelog can — which auth
path is actually taken, what the runner version is, which inputs resolve to what, and
which other actions are already printing deprecation warnings.

Use the `github_ci` MCP tools where they're available (they are, inside the review
workflow): `mcp__github_ci__get_ci_status` to find recent runs, then
`mcp__github_ci__get_workflow_run_details` and `mcp__github_ci__download_job_log`. Running
locally instead, the CLI equivalents are:

```bash
gh run list --repo dimagi/commcare-connect --workflow deploy.yml --limit 5
gh api repos/dimagi/commcare-connect/actions/jobs/<job-id>/logs
```

Do this even when nothing is failing — the skill's main flow frames logs as a debugging
tool, but here they are the primary evidence about runtime behavior.

## 4. Ask whether CI proves anything

Check the workflow's own triggers before treating green checks as reassurance:

```bash
sed -n '1,20p' .github/workflows/<file>.yml
```

A `workflow_dispatch`-only workflow (like `deploy.yml`) is never exercised by CI, so green
checks are _irrelevant_ rather than reassuring, and the first real execution is a
production run. Say so explicitly in the review — "3/3 green" on an unexercised workflow
is the most misleading thing you can report. When CI can't verify it, the verification
burden moves entirely onto the contract diff plus a manual dispatch to staging.

## 5. Things specific to this class

- **Version pinning.** A mutable tag (`@v3`) versus a pinned one (`@v3.0.1`) versus a SHA
  matters most for a step holding production credentials.
- **Sibling PRs on the same file.** Several Dependabot PRs often edit one workflow, and
  Dependabot stops auto-rebasing after 30 days. Don't just note the coupling — merge them
  in a throwaway worktree and find out. They frequently turn out to be independent, and
  "verified clean in any order" is worth more to the reader than a warning.
- **Error-handling defaults.** An action that used to fail the step on error may now
  default to swallowing it — a step that goes green while doing nothing is worse than one
  that fails, so check the default rather than assuming continuity.
