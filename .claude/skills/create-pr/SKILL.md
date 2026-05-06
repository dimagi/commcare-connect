---
name: create-pr
description: >
  Create a PR from the current branch: reads .github/PULL_REQUEST_TEMPLATE.md,
  generates title + body from the diff, pushes if needed, opens via gh pr create.
allowed-tools: Bash(git diff:*), Bash(git log:*), Bash(git branch:*), Bash(git push:*), Bash(git rev-parse:*), Bash(git status:*), Bash(gh pr create:*), Bash(gh pr list:*), Bash(gh auth status:*), Read
---

# create-pr

Create a pull request from the current branch to `main`.

## Usage

```
/create-pr           # create PR against main
/create-pr --draft   # create as draft
```

---

## Step 1 — Pre-checks (run in parallel)

```bash
gh auth status
git branch --show-current
gh pr list --head "$(git branch --show-current)" --state open --json number,url
```

Stop if:

- Not authenticated → `gh auth login first`
- On `main` → `Switch to a feature branch first`
- PR already open → `PR #<N> already exists: <url>. Aborting.`

---

## Step 2 — Gather diff (run in parallel)

```bash
git log main..HEAD --no-merges --oneline
git diff main...HEAD --stat
git diff main...HEAD
```

Stop if no commits ahead of `main` → `No changes vs main. Nothing to PR.`

---

## Step 3 — Read PR template

Read `.github/PULL_REQUEST_TEMPLATE.md`.

Stop if missing → `PR template not found at .github/PULL_REQUEST_TEMPLATE.md`

---

## Step 4 — Extract context

From the branch name and diff:

- **Jira ticket**: look for `CCCT-\d+` or `CI-\d+` in the branch name or in commit messages (case-insensitive). If found, record as `TICKET` (e.g. `CCCT-2276`).
- **What changed**: files, modules, what was added/modified/removed
- **Why**: from commit messages first, then code shape
- **User-facing effect**: any view, URL, API, form, or UI text a user would notice? If none, say so.
- **Risk**: migrations, auth, payments, permissions, public API
- **Tests**: new or changed test files? Any gaps?

---

## Step 5 — Write the title

- Imperative verb: `Fix`, `Add`, `Remove`, `Change`, `Rename`
- Under 70 characters
- Name the actual thing — no vague words like `update`, `improve`, `refactor`, `misc`
- No ticket prefix, no `feat:` / `fix:` prefix, no trailing period

---

## Step 6 — Fill the PR body

Use the template from Step 3 as the exact structure. Fill every section:

- Replace all `<!-- ... -->` comments with real content
- Never leave a section empty — if it doesn't apply, write one line: `Not applicable — <reason>`
- Do not add or remove sections
- Keep the `- [ ]` checkbox as-is (don't check it)

Section guidance:

**Product Description** — what a user sees or gains. If no user-facing change: `No user-facing changes.`

**Technical Summary** — 2–4 sentences on what changed and why. If `TICKET` was found: add `Ticket: [<TICKET>](https://dimagi.atlassian.net/browse/<TICKET>)` as the first line.

**Safety story** — what was tested locally, why it's safe, blast radius, impact on existing data.

**Automated test coverage** — list test files added/changed and what they cover. If none: state why that's acceptable.

**QA Plan** — numbered steps: page → action → expected result. If trivially safe: `No manual QA needed.`

**Labels & Review** — keep the checkbox as-is.

---

## Step 7 — Show preview

Print this before creating:

```
Branch: <current-branch>  →  main
Draft: <yes|no>

Title: <title>

Body:
---
<body>
---
```

---

## Step 8 — Push

```bash
git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null
```

If no upstream → `git push -u origin HEAD`. Stop on push failure.

---

## Step 9 — Create the PR

```bash
cat > /tmp/pr-body.md <<'EOF'
<body>
EOF

gh pr create --base main --title "<title>" --body-file /tmp/pr-body.md [--draft]

rm -f /tmp/pr-body.md
```

Print the PR URL on its own line: `PR created: <url>`
