---
name: create-adr
description: 'Write an Architecture Decision Record in docs/adr/. Use when a design decision should be recorded as a citable ADR, whether it comes from a design doc, a PR or a conversation.'
---

# Create an ADR

Write one ADR in `docs/adr/` for one decision.

**Format reference:** `docs/adr/_template.md` defines the file structure. ADR files have no YAML frontmatter; status is a `**Status:**` line under the heading. The existing files in `docs/adr/` are worked examples.

## 1. Find the source

The decision is usually described in a design doc, which often isn't on the current branch. Design docs are typically committed in their own PR, which is closed once the design is settled.

If it isn't clear where the decision is described, ask the user with `AskUserQuestion`:

- `A PR (open or closed)` — ask for the PR number or URL. Run `gh pr view <N> --json files` to find the doc, then `git fetch origin pull/<N>/head` and `git show FETCH_HEAD:<path>`. This works for closed PRs. If the PR has several docs, ask which one.
- `A path in this repo` — ask for the path.
- `Another branch` — ask for the branch and path, then `git fetch origin <branch>` and `git show FETCH_HEAD:<path>`.
- `I'll describe it` — wait for the user's description.

Don't change or delete the source doc.

## 2. Check it is one decision

An ADR records a decision that:

- was a real choice between plausible options,
- constrains future work,
- would be useful to cite from code or PRs, and
- **could be superseded on its own.** A choice forced by a bigger decision is not its own ADR; fold it into the parent's Decision, Consequences or Alternatives.

If the source holds several such decisions, list them and ask the user which to record. Write each as its own ADR, one at a time.

## 3. Number it

```bash
ls docs/adr/[0-9]*.md 2>/dev/null | grep -oE '[0-9]{4}' | sort -n | tail -n1
```

Use the next number. The file is `docs/adr/NNNN-kebab-title.md`.

## 4. Draft it

Follow `docs/adr/_template.md`:

- `# ADR-NNNN: Short title`
- `**Status:** <Status>`
- `**Author:** <user's git user.name> · **Created:** <today, YYYY-MM-DD>` (each of these lines is its own paragraph, separated by blank lines)
- `## Context` — why the decision was needed. One or two paragraphs.
- `## Decision` — starts with "We will…". One paragraph, or bullets for a multi-part decision.
- `## Consequences` — bullets, good and bad.
- `## Alternatives considered` — one line each: option and why it was rejected.

**What to leave out.** Accepted ADRs are not edited, so leave out anything that goes stale on a refactor: file paths, line numbers, private helper names, descriptions of how the code is laid out, log strings, migration filenames. Keep names that are part of the decision and need a migration to change: models and fields, DB constraints and indexes, enum values, waffle flag and switch names, URL routes and query parameters, and ORM lookup paths when the join is the decision. Test: "If this is renamed tomorrow, does it need a migration or just a refactor?" Migration → keep. Refactor → cut.

**State requirements, not where they came from.** Don't mention Jira tickets, PRs or people. Write the underlying requirement instead: "Program managers need to see who changed an invoice and when", not "CCCT-2136 asked for an audit log".

**Keep it short.** Wordiness is the most common problem, so write tight from the start:

- One idea per sentence.
- State each fact once. Refer back to Context rather than repeating it.
- No editorializing or self-praise.
- No metaphor or flourish where a literal phrase works.
- Each Consequence and Alternative bullet is one sentence.
- Context ≤ 2 short paragraphs. A single-decision ADR rarely needs more than ~400 words; a complex one rarely more than ~700.
- Final pass: delete every sentence that wouldn't change what a future engineer does.

## 5. Set the status

Ask the user with `AskUserQuestion`: `Accepted` (default for shipped work), `Proposed` (recorded but not implemented), `Rejected`, or `Draft`.

## 6. Check it against the code

For an Accepted ADR, check every claim about behaviour against the code: data model, org/program scoping, defaults, control flow, named settings, flags and modules. The code wins: where the source disagrees with the code, write what the code does and tell the user. Show the user where you checked (file and function), but don't put file paths in the ADR. Skip this for Proposed and Draft ADRs.

## 7. Link related ADRs

If the Context relies on something an existing ADR decided, this ADR extends it. Confirm with the user, then add a line under the author line:

```markdown
**Extends:** [ADR-0010](0010-some-title.md)
```

## 8. Write it

1. Show the user the draft and ask for edits.
2. Write the file.
3. Run `prek run --files <adr-file>` and fix anything it reports.
4. Tell the user the file written and "Review the diff and commit when ready."

## Rules

- **Never commit.** The user reviews and commits.
- **Never edit an existing Accepted ADR** beyond setting it to Superseded and adding a `**Superseded by:**` link, and only with the user's confirmation.
