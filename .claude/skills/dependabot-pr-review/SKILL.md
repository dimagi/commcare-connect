---
name: dependabot-pr-review
description: >
  Reviews a Dependabot (or any dependency-bump) PR — dimagi/commcare-connect by default,
  but works on any repo the user names: checks CI
  and whether the PR is still valid against main, inventories the real version changes,
  researches what actually changed upstream, assesses impact on how this codebase uses each
  package, and produces a risk-rated review with a merge recommendation.

  Also triages a whole queue of open Dependabot PRs, and handles GitHub Actions version
  bumps in .github/workflows/ as well as uv and npm dependency updates.

  Use this whenever the user asks to review, analyze, assess, triage, or "look at" a
  Dependabot PR or the Dependabot queue, a dependency bump, a GitHub Actions version bump,
  or a PR that only touches uv.lock / pyproject.toml / package.json / package-lock.json /
  .github/workflows — including when they just paste a PR number or URL and say something
  like "is this safe to merge?", "what breaks if we take this?", or "can we close any of
  these?".
---

# Dependabot PR Review

Produce a review of a dependency-update PR that tells the team something `gh pr diff`
doesn't: what actually changed upstream, and whether _this_ codebase cares.

`.github/workflows/claude-dependabot.yml` runs this same file automatically on Dependabot
PRs and posts the result as a comment. That workflow holds no review instructions of its
own — it supplies the PR number and tells you to post — so changes here take effect in CI
too, and the two cannot drift apart.

The governing idea: **a dependency review is worth reading only to the extent it rests on
evidence you gathered rather than claims you relayed.** Changelogs are a starting point,
not proof. The techniques below are ordered by how much they actually settle.

Bundled alongside this file:

- `scripts/triage.py` — compares every open Dependabot PR against `origin/main` and
  separates the obsolete from the reviewable. Run it first when triaging a queue.
- `scripts/lockdiff.py` — structurally diffs `uv.lock` and `package-lock.json` for one PR.
- `references/github-actions.md` — the playbook for `.github/workflows/` bumps, where most
  of the lockfile instincts below invert.

## Input

The user gives a PR number or URL. If they didn't, ask rather than guessing — or list the
candidates if they said something like "the open dependabot PRs":

```bash
gh pr list --repo dimagi/commcare-connect --author "app/dependabot" --json number,title --limit 20
```

Commands below say `dimagi/commcare-connect` because that's the usual target, but nothing
here is specific to it — **substitute the repo the user named**, and work from a local
clone of it so the `git` and script steps have something to read. The bundled scripts take
`--repo`; invoke them by absolute path, since a foreign clone has no `.claude/skills/`.

Output goes to the terminal only — do **not** post a comment to GitHub unless the user
explicitly asks.

## Step 0: Classify the PR, and triage the queue

**Which class is this?** The techniques below are built for lockfile-managed dependencies.
If the PR touches only `.github/workflows/`, it's a GitHub Actions bump — a different shape
of problem, where diff size proves nothing and CI often doesn't exercise the change at all.
Read `references/github-actions.md` and follow it instead of Steps 1c-3, then come back for
Step 4's output format.

```bash
gh pr view <N> --repo dimagi/commcare-connect --json files -q '.files[].path'
```

**If the user asked about the queue rather than one PR**, triage it first — an old queue is
mostly dead PRs, and reviewing them individually wastes most of the effort:

```bash
python .claude/skills/dependabot-pr-review/scripts/triage.py
```

It compares every open Dependabot PR against `origin/main` and separates the obsolete from
the ones worth reviewing. Verify anything it calls obsolete (Step 1a) before recommending a
close.

**If you're reviewing several PRs at once**, establish the facts that apply to all of them
before starting any one review — the runner's node-deprecation warnings, which workflows CI
actually runs, what moved on `main` since they opened. Repeating that per PR wastes effort
and buries the one finding that differs.

Two batch questions worth answering up front, both one command:

```bash
git grep -nE "<action-a>|<action-b>|<action-c>" origin/main -- .github/   # call-site coverage
git log origin/main --oneline -10 -- .github/                             # what moved under them
```

And when several PRs touch one file, **test the merge order rather than warning about it** —
merging them in a throwaway worktree takes one command and either finds a real conflict or
retires the concern:

```bash
git worktree add /tmp/mergetest origin/main && cd /tmp/mergetest
for n in <N1> <N2> <N3>; do git fetch -q origin "pull/$n/head" && git merge --no-edit FETCH_HEAD || echo "CONFLICT at $n"; done
```

## Step 1: Establish the facts before reading anything

Four cheap checks, each of which has overturned an otherwise-obvious verdict:

**a. Is the PR still valid?** Do this first. Dependabot PRs sit for months and the
dependency may be gone — and a stale PR looks _safest_ from the diff alone, because
nothing uses a package that no longer exists. That's the failure mode this check exists
to prevent.

Compare against `origin/main`, never the working tree: a dev checkout's `HEAD` is usually
behind, so a working-tree grep can show a package that was already removed upstream.

```bash
git fetch -q origin main
gh pr view <N> --repo dimagi/commcare-connect --json title,author,state,mergeable,mergeStateStatus,createdAt
git show origin/main:pyproject.toml | grep -i <package>
git show origin/main:uv.lock | grep -i "name = \"<package>\""
```

A PR decays in two ways, and the second is far more common in an old queue:

- **The package was removed.** If it's absent from `origin/main`, confirm the removal was
  deliberate and find the commit:

  ```bash
  git log origin/main -S <package> --oneline -- pyproject.toml package.json
  ```

- **`main` moved past the PR's target.** The package is alive and well, but already at or
  beyond the version the PR proposes — so the bump is a no-op or a downgrade. Compare the
  version on `main` against the "to" version in the PR title. This one is invisible from
  the diff, which still looks like a clean forward bump.

Either way the verdict is `OBSOLETE`. A removal commit usually drops several packages at
once, and a big grouped bump supersedes many PRs at once, so **check whether other open
Dependabot PRs died the same way** — often the most valuable thing this review produces:

```bash
python .claude/skills/dependabot-pr-review/scripts/triage.py
```

**b. Is CI green, and if not, is it this PR's fault?** The highest-signal step. A red
check on a grouped bump usually points straight at the one package that broke.

```bash
gh pr checks <N> --repo dimagi/commcare-connect
```

Attribute failures to the PR only after establishing they're not pre-existing. Two ways,
in order of reliability:

- **Read what the job actually does.** `.github/workflows/<name>.yml` often settles it
  outright — `security.yml` audits production deps only (`uv export --no-dev --group
production`), so a dev-only bump cannot change its result, whatever it reports.
- **Compare sibling PRs.** If the same check is red on several unrelated Dependabot PRs,
  it's a queue-wide problem, not this PR's. Note that `gh run list` has no `--branch`
  flag, and a `pull_request`-triggered workflow has no `main` baseline run at all — so
  "is main green" is often unanswerable and sibling comparison is the real method.

To read a failing job, `gh api repos/dimagi/commcare-connect/actions/jobs/<job-id>/logs`
works better than `gh run view --log-failed` (which sometimes returns empty) — but GitHub
expires logs after ~90 days and returns HTTP 410, which is exactly what happens on the
stale PRs where you most want them. Fall back to sibling comparison.

Histogram the failures rather than reading them linearly: 176 failures with one root cause
is a very different finding from 176 unrelated ones.

When a check is red and you can't rule it out from the workflow definition, **compare the
specific failing packages across the merge base**. Pull the package names out of the job
log, then check each one's version on `origin/main` against the PR head: any package at an
identical version on both sides cannot be this PR's doing. That reduces "34 advisories" to
"none of them moved here" in one pass, and it works even when the workflow's scope isn't
obvious.

**Green is not automatically reassuring — ask whether any check exercises this change.** A
`workflow_dispatch`-only workflow never runs in CI; a production-only audit ignores dev
dependencies. Reporting "3/3 green, low risk" on a change nothing executed is worse than
reporting nothing, so when the checks don't reach it, say so and move the verification
burden to Testing focus.

**c. What actually moved?** Check the size of the change first:

```bash
gh pr diff <N> --repo dimagi/commcare-connect --patch | diffstat 2>/dev/null \
  || gh pr diff <N> --repo dimagi/commcare-connect | head -40
```

A single-package PR is often a two-line diff — just read it, and skip to (d). Roughly a
third of this repo's open Dependabot queue is that shape.

This heuristic holds only because a lockfile diff grows with the number of packages moved.
Where there's no lockfile — an Actions bump, a bare requirement-specifier change — diff
size says nothing about blast radius, and a two-line diff can cross three major versions.

For a grouped bump, don't read the lockfile diff as text: it runs to thousands of lines
and hand-written parsers drop packages silently. Use the bundled script, which diffs both
lockfiles structurally against the merge base:

```bash
python .claude/skills/dependabot-pr-review/scripts/lockdiff.py <N>
```

It prints every version change marked `[direct]` and `[MAJOR]`, and calls out **transitive
majors**, which the Dependabot PR body never lists and which are where the surprises live.
It also surfaces added/removed packages — that's how you catch a build tool swapping out
an internal dependency — and flags when the PR is already merged into `main`.

It compares _resolved versions_, so a PR that only loosens a requirement specifier prints
"lockfile TOUCHED but no resolved version moved". That's your cue to read the diff
directly. And because it diffs against the merge base, it cannot tell you the package was
deleted from `main` — that's what (a) is for.

**d. What does the PR body already tell you?** Dependabot embeds release notes, which are
more authoritative than anything you'll find by searching. **GitHub truncates the body** —
often mid-package, silently omitting notes for more than half of a grouped bump. Single
packages aren't safe either: on a multi-major bump the body keeps the newest patch notes
and truncates away the major release with the migration guide, which is the only part that
mattered. So: enumerate which packages and which versions the body actually covers, and
treat the rest as unresearched. Only the version table at the top is complete.

```bash
gh pr view <N> --repo dimagi/commcare-connect --json body -q .body >| "$SCRATCH/pr-<N>-body.md"
```

Use `>|` and a PR-numbered filename: zsh runs with `noclobber` here, so a plain `>` onto an
existing path fails _non-fatally_ and leaves you reading a previous PR's body without any
error to tell you.

Fetch `body` on its own — combined with `files` it returns tens of KB of noise. Write
scratch files to the session scratchpad directory rather than `/tmp`.

## Step 2: Find out what really changed

Work down this list. Stop at the cheapest technique that actually settles the question.

**The PR body** (free, already fetched) — covers whatever it covers.

**Diff the artifacts** — the strongest method available, and underused, but also the most
expensive. Reserve it for the two or three highest-risk packages _whose behavior you still
can't pin down_; on a small or dev-only bump, skip it entirely. Download the new version
and diff it against what's installed. This proves things no changelog mentions:

```bash
# Python -- works without a configured venv, which a fresh clone won't have
uv pip install --target /tmp/old --no-deps <pkg>==<old>
uv pip install --target /tmp/new --no-deps <pkg>==<new>
/usr/bin/diff -ru /tmp/old/<pkg> /tmp/new/<pkg> | head -200

# JavaScript — compare the export surface when a changelog claims exports changed
npm pack <pkg>@<new> --silent && tar xzf <pkg>-<new>.tgz -C /tmp/new
node -e "console.log(Object.keys(require('/tmp/new/package/dist/<file>')))"
```

Behavior changes hide in defaults, `__str__` methods, and settings flags — exactly what a
diff shows and a release note omits.

**Read the changelog** for the rest. Use `raw.githubusercontent.com` URLs for changelog
files; `github.com/<org>/<repo>/blob/main/CHANGELOG.md` returns SPA chrome, not content.
Look at everything _between_ old and new, not just the newest release — a patch bump that
skipped four releases still carries whatever they changed.

**Confirm the changelog actually reaches your target version.** Canonical changelogs get
abandoned or moved, and a stale one still returns HTTP 200 — so "no notes for this version"
can mean the file simply stops before it. Check the newest entry in the file against the
version you care about before concluding there's nothing to find.

Research packages in parallel. A 20-package group is not a few-WebFetch job: batch it by
risk tier (auth/API stack, native/infra, dev tooling) and run a codebase-usage survey
alongside. If you delegate to subagents, give each a distinct scratch filename — they
collide on obvious names otherwise.

Don't invent changelog content. "Couldn't find release notes for X" is more useful than a
confident guess, because the point of this review is telling the team where to look.

## Step 3: Decide whether this codebase cares

A breaking change matters only if the code reaches it. So start with the cheap question
that often ends the analysis:

```bash
grep -rn "<import-name>" --include="*.py" --include="*.js" . | head
```

**Is it still a direct dependency, and does anything import it?** If nothing does, you're
done — skip the rest of this step. The apparatus below is shaped for Django packages wired
into request handling; spending it on a dev-only tool or an unused library is wasted
effort.

**Derive the risk list from configuration, not intuition.** Packages wired into request
handling affect everything; a fixed list of "usual suspects" will miss the one that
matters. Check what's actually load-bearing:

```bash
grep -rn "DEFAULT_AUTHENTICATION_CLASSES\|MIDDLEWARE\|INSTALLED_APPS" config/settings/
```

Anything in `DEFAULT_AUTHENTICATION_CLASSES`, `MIDDLEWARE`, or a `DEFAULT_*` DRF setting
sits on the path of every request — django-oauth-toolkit, DRF, and Django itself rank
above Celery or Pillow on that basis, however routine the version bump looks. Read the
settings module the repo actually has rather than assuming a layout: commcare-connect
splits `config/settings/`, connect-id has a single `connectid/settings.py`.

Whatever else that module turns up is a standing risk for _that_ repo — commcare-connect
being a PostGIS monolith with `ATOMIC_REQUESTS = True` puts psycopg and the GIS bindings
across everything, which tells you nothing about a repo without them.

**Search all the places a dependency is referenced**, not just Python source. The paths
below are commcare-connect's; on another repo, map them to its equivalents (app packages,
settings module, CI config) rather than searching for these literally:

| Where                              | What it settles                                         |
| ---------------------------------- | ------------------------------------------------------- |
| `pyproject.toml`, `package.json`   | Is it still a direct dependency at all?                 |
| `config/settings/*`                | `INSTALLED_APPS` membership; version-sensitive settings |
| `commcare_connect/`                | Python usage                                            |
| `commcare_connect/static/`, `*.js` | JS usage                                                |
| `commcare_connect/templates/`      | Template tags/filters from Django packages              |
| `commcare_connect/**/tests/`       | Tests asserting on behavior the bump changes            |
| `.pre-commit-config.yaml`          | Hook revs pin ruff/djlint _independently_ of `uv.lock`  |
| `Dockerfile`, `docker/`, `deploy/` | Worker classes, runtime flags                           |
| `.github/workflows/`               | CI-pinned versions                                      |

Three more zsh traps worth knowing, all silent rather than loud:

- `diff` may resolve to a shell function — use `/usr/bin/diff` if it errors with "function
  definition file not found".
- Quote the ref in `git show "$SHA:path"`. Unquoted, zsh reads `:u` as the uppercase
  parameter modifier and you get a mangled path with no useful error.
- `gh pr diff <N> -- <path>` does **not** filter by path; it silently ignores the pathspec
  and prints everything. Pipe through `awk`/`grep` instead.

Quote anything containing glob characters — `--include="*.py"`, and URLs with a query
string like `...contents/action.yml?ref=v3`. Unquoted, zsh tries to expand them and the
command dies with "no matches found."

**When you find zero usage, say which kind of zero it is.** No importers and no reverse
dependencies means the package is dead weight — recommend removing it rather than bumping
it. Zero usage of the _changed API_ while the package is still used means genuinely low
risk. These lead to opposite actions, so don't collapse them into "no usage found."

Cite specific files and lines. "Affects `opportunity/views.py:412`" is actionable; "may
affect views" is not.

**Prefer settling a question to flagging it.** Build both sides and diff the bundle, run
`npm audit` on each and compare, run the test suite, diff a function's output across
versions. These take minutes and convert a hedge into a finding.

Running the suite is the single highest-value one, and on an unfamiliar repo the setup is
the only hard part:

```bash
docker run -d --rm -p 55432:5432 -e POSTGRES_PASSWORD=x --name pgtmp postgres:15
uv export --frozen --no-hashes -o /tmp/reqs.txt
sed -i 's/^psycopg2==/psycopg2-binary==/' /tmp/reqs.txt   # source build needs python-dev headers
uv pip install -r /tmp/reqs.txt
DATABASE_URL=postgres://postgres:x@localhost:55432/postgres pytest
```

Swapping `psycopg2` for `psycopg2-binary` and picking a non-default port are what usually
block this. Also worth running: `makemigrations --check` (catches a model change the bump
implies) and the repo's linter at the new pin.

## Step 4: Write the review

Print to the terminal, leading with what blocks a merge. On a 20-package group, a reader
should not have to scroll through changelog summaries to learn CI is red.

Drop any section that would be empty — the template is a checklist of what to consider,
not a form to fill.

Reviewing a batch, lead with one **shared findings** section (what's true of all of them —
runtime deprecations, CI coverage, merge-order result) and a compact verdict table, then a
short per-PR section carrying only what differs. The shared half is usually the real
finding; repeating it per PR hides that.

```markdown
## Summary

<risk LOW / MEDIUM / HIGH>, <verdict>. <one line on why>
<packages as `name: old → new`, grouped by stack; note transitive majors separately>

## Blocking findings

<only what stops a merge>

## CI

<check results; whether main is green; root cause of failures, not a failure list>

## Verification run

| Check | Result |
| ----- | ------ |

<what you actually ran and what it produced — builds, test suites, audits, artifact diffs>

## Changelog review

### <package> (old → new)

- **Changes** / **Breaking** / **Security** / **Migration**
  <omit packages with nothing to say>

## Impact on Connect

- **Breaking changes reaching our code**: <yes/no + specifics>
- **Affected files**: <paths, or "none found">
- **Test impact**: <tests likely to break>
- **Config / migrations**: <new migrations need `migrate_multi`>

## Needs checking against production data

<things no code review can settle — OAuth redirect URIs, DB-stored config>

## Recommendation

- **Testing focus**: <what a human should run>
- **Follow-ups**: <work this upgrade creates>

## Links

<changelogs, migration guides, CVEs, the removal commit for an obsolete PR>
```

**Verdicts:**

| Verdict         | When                                                                                                                                                                                          |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `APPROVE`       | Changes understood, nothing reaches our code, CI green. Residual category risk (rendered output, say) goes in Testing focus.                                                                  |
| `REVIEW_NEEDED` | Something you couldn't verify _would change the recommendation if it went the wrong way_. Not for risk inherent to every bump of that kind.                                                   |
| `HOLD`          | Merging as-is breaks something. Includes CI red with a fix that belongs in this PR — name the fix and the call sites.                                                                         |
| `INCOMPLETE`    | The change is correct but no longer covers everything it needs to — most often a stale Actions PR that patches some call sites and leaves others behind. GitHub will still call it mergeable. |
| `OBSOLETE`      | The PR no longer applies: the dependency was removed, or `main` already sits at or past the target version. Recommend closing.                                                                |

Rate risk on impact, not version numbers. A major bump of a package used in one script is
LOW; a patch bump that changes a queryset default is HIGH.

Match effort to stakes. A single patch bump of a dev-only tool deserves a few lines and
maybe three of these sections; a grouped bump of Django plus five plugins deserves the
whole thing.

## Optional: post to the PR

Only if the user asks. Write the review to a file first, then:

```bash
gh pr comment <N> --repo dimagi/commcare-connect --body-file <file>
```
