---
name: pr-impact
description: 'Grade the impact and risk of a commcare-connect pull request across six dimensions (core flow, data and reversibility, security, external contracts, blast radius, test evidence), roll them up into one overall level, and record it as a Low/Medium/High Risk label plus a short Impact section in the PR body. Use when asked to "grade the risk on this PR", "what risk label should this get", "add impact metrics", "regrade #1234 after my changes", or when preparing a PR description in this repo. For the mobile (commcare-android) repos use dev-utils:pr-risk instead.'
---

# Grade PR Impact

Rate each dimension None / Low / Medium / High, roll them up into one overall level, let the author confirm or override it, then record **exactly one** risk label and one Impact section in the PR body. The label is optional: if the author declines, set nothing.

Labels: `Low Risk`, `Medium Risk`, `High Risk`. Never any other spelling.

Weigh what a defect could break and how likely that is, not how big the diff is. A three-line change to accrual can be High; a 2,000-line template refactor can be Low.

## Two modes

- **Before the PR exists** (local branch, or called while drafting a PR description): run steps 1–3 and 5, and the label check in 6.1, then hand back the label and the Impact section the author approved, for the PR body. `gh pr create --label` fails on a label the repo lacks, so create it first.
- **On an existing PR** (a number, URL, or the current branch's PR): run every step.

## 1. Gather

Before the PR exists, diff against a freshly fetched `origin/main` with three dots (`origin/main...HEAD`), so only this branch's commits count. On an existing PR, `gh pr view <PR> --json number,url,labels,body,files,commits,baseRefName,headRefOid` and `gh pr diff <PR>`.

**Search the PR's code, not your checkout.** On an existing PR, if `HEAD` isn't `headRefOid`, run `git fetch origin pull/<PR>/head` and pass that SHA as the revision in every step 2 search (`git grep -n "<symbol>" <sha> -- …`, `git show <sha>:<path>`). Otherwise callers and tests the PR adds are missed. `<rev>` below means that SHA, or `HEAD` before the PR exists.

Reuse what the caller already gathered instead of fetching it again, including the author's answer about their own testing. Don't ask about testing yet: step 3.6 decides whether the answer can change the grade.

## 2. Collect the automatic signals

Run these on the changed files (`git diff --name-only origin/main...HEAD`, or the PR's `files`). They feed the dimensions in step 3; they are evidence, not grades.

- **Migrations:** any changed path matching `*/migrations/*.py`. Read each one: schema only (`AddField`, `AlterField` with a default, `AddIndex`) or does it touch data (`RunPython`, `RunSQL`, `RemoveField`, `DeleteModel`, a `RenameField` other code still reads)? Is there a reverse operation?
- **Flag gating:** does the diff add a check against a name from `commcare_connect/flags/flag_names.py` or `switch_names.py` around the new behaviour? Does it **remove** one that guarded shipped behaviour?
- **Fan-in:** for each changed function, class or template that other code uses, count callers outside the changed files: `git grep -n "<symbol>" <rev> -- 'commcare_connect/**' ':!**/tests/**'`, or `{% include "<template>"` for templates. Note how many Django apps those callers sit in. Use `git grep` rather than a call-graph tool: much of Connect is wired by string (template includes, URL names in `reverse()`, Celery task names), which static analysis misses.
- **Dependencies:** any change to `pyproject.toml`, `uv.lock`, `package.json`, `package-lock.json` or `.github/workflows/`.
- **Tests:** which changed paths have a test in the diff, or an existing test at `<rev>` under `commcare_connect/<app>/tests/` that exercises them. Also note tests the diff deletes, skips or loosens, and which code they covered.

## 3. Grade each dimension

Five **primary** dimensions decide the level. One **modifier**, test evidence, can only raise it.

Give each dimension the highest level any part of the change reaches, with a reason of ten words or fewer.

**None and Low are different answers.** None means the change has nothing in that area: no migrations, no new entry points, nothing an outside system reads. Low means the change _does_ touch the area, in the safest way it can. Say which: a reversible schema-only migration is Data **Low**; no migration at all is Data **None**. Reviewers read None as "nothing to check here" and Low as "something here, but it's safe", so never write Low to mean "not applicable".

### 3.1 Core flow (criticality)

`docs/core-flows.md` lists the flows Connect exists to run (form ingest, visit status, accrual, invoicing and payment, claiming, worker identity, org access) with the code to start from for each one. Check reach from those starting points: does the changed code run on one of the flows, directly or through a caller?

- **High:** on a core flow, and a defect could block it, lose submitted work, or produce a wrong status, count or amount. For the org access flow, High here means a defect could lock people out of work they need to do; a defect that grants access to the wrong people is graded under Security (3.3), not here, so the same risk isn't counted twice.
- **Medium:** a feature program managers or org admins use regularly but that isn't a core flow (opportunity dashboards, worker pages, exports, microplanning, audit reports, tasks).
- **Low:** admin-only pages, internal reports, prelogin pages; or copy, styling, logging or analytics on a core-flow page that can't change its outcome.
- **None:** no code that runs in production changed: docs, new or strengthened tests, CI, developer tooling, `.claude/`.

**Removing tests is not None.** Deleting, skipping or loosening tests leaves the code they covered unguarded. Grade Core flow by that code as if it had changed, capped at Medium since its behaviour didn't change, and record its test evidence in 3.6 as `Partial` or `Untested`.

### 3.2 Data and reversibility

Could a defect leave data we can't get back, and can we switch the change off without a deploy?

- **High:** migrations that rewrite, delete or backfill data (`RunPython`, `RunSQL`, `RemoveField`, `DeleteModel`); backfills on big tables (`UserVisit`, `CompletedWork`, `Payment`, `OpportunityAccess`, xform-backed models); anything that recomputes stored statuses or amounts; removing a waffle flag or switch that guarded shipped behaviour.
- **Medium:** schema migrations that can't be reversed cleanly or that lock a big table (adding a non-null column, an index created without `CONCURRENTLY`); new writes to existing tables that are not behind a flag; changes to a Celery task that writes data.
- **Low:** reversible schema-only migrations on small or new tables (`CreateModel`, nullable `AddField`, `AddIndex` on a new table); new writes behind a flag or switch that is off by default.
- **None:** no new migrations, no change to what gets written, and no flag or switch removed.

### 3.3 Security and trust boundary

- **High:** authentication, sessions, permission decorators or checks (`organization/decorators.py`, `opp_*_access_required`, DRF permission classes), org scoping of querysets, OAuth providers and token scopes (`data_export`, `commcarehq_provider`, `ocs_provider`), secrets and credentials, or adding personal data to what a view, export or API exposes.
- **Medium:** new views or API endpoints that reuse existing permission decorators unchanged; new parsing of untrusted input (form payloads, uploaded files, query parameters fed into queries).
- **Low:** changes inside an existing, already-gated view or endpoint that show or accept more non-personal data, with no change to who can reach it.
- **None:** no new or changed entry points, input handling, permission code or exposed data.

### 3.4 External contracts

Does something outside this repo depend on what changed?

- **High:** breaking changes to API responses or request shapes the mobile app uses (`opportunity/api/`, `users/api`, anything under versions `1.0`/`2.0`) or to the `form_receiver` payload contract; changes to the URL, method, payload or authentication of an existing call to CommCare HQ or ConnectID (`commcarehq/`, `utils/commcarehq_api.py`, `connect_id_client/`); any new or changed outbound call on a core flow; major-version dependency upgrades.
- **Medium:** a new read-only call to an HQ or ConnectID endpoint Connect already uses, off the core flows, with its failures handled and an explicit timeout; renamed or removed export columns or changed file formats people script against (`data_export/`, CSV exports); OCS or webhook behaviour; Celery task signatures (tasks already queued keep the old arguments until they run); minor or patch dependency upgrades.
- **Low:** additive, backwards-compatible changes: a new optional API field, a new export column appended at the end, a new endpoint nothing depends on yet.
- **None:** nothing an outside system, client or script reads has changed, and no dependency changed.

### 3.5 Blast radius (reach)

From the fan-in signal.

- **High:** shared foundations: `utils/` base models and middleware, `config/settings/`, `config/urls.py`, base templates every page extends, `tailwind/tailwind.css` classes, shared JS in `static/js/` loaded on most pages.
- **Medium:** a helper, template or component with several callers, or callers in more than one Django app.
- **Low:** one call site in one module, or new code nothing else calls yet.
- **None:** no code that runs in production changed.

### 3.6 Test evidence (modifier)

If the author only read the diff, or neither they nor the tests exercise the paths the change touches, raise the overall level from Low to Medium. Testing never lowers a High.

**Ask only when the answer can change the grade.** If no primary dimension is above Low, and the author hasn't already said, ask: "What did you personally do to test this?" Never invent testing. If a primary dimension is already Medium or High, don't ask: grade from the tests in the diff and the existing tests alone, and say so in the details (`author not asked: grade was already High`).

Record it as `Covered`, `Partial` or `Untested`, or `N/A` when no code that runs in production changed and no tests were removed or loosened.

### 3.7 Overall

Overall = the highest primary dimension, then apply the test evidence modifier (it raises only from Low to Medium). Nothing lowers a High. If every primary dimension is None (docs, added tests or tooling only), the overall level is Low: there is no `No Risk` label.

## 4. Read what is already there (existing PR only)

- **Current label:** any of the three risk labels on the PR. If there are several, note them all; step 6 removes the extras.
- **Earlier Impact section:** the block between `<!-- pr-impact:start -->` and `<!-- pr-impact:end -->`. If its bold `**Risk: …**` line contains `(author's call; graded <Level>)`, the author overrode an earlier grade.

## 5. Ask the author

Show the proposal in this shape and wait:

> Proposed risk: **High**. Changes accrual recalculation on re-approval, on the payment core flow.
>
> | Core flow | Data     | Security | Contracts | Reach  | Tests   |
> | --------- | -------- | -------- | --------- | ------ | ------- |
> | **High**  | **High** | None     | None      | Medium | Covered |
>
> Currently labeled: Medium Risk. _(existing PR only)_
>
> Keep High, pick Low or Medium, or skip the label?

**An earlier author override stands** unless the author changes it: propose their level again and show the new grade beside it ("graded Medium now; you set High last time"). Don't argue the grade back up or down.

Then build the **Impact section**:

```markdown
### Impact

<!-- pr-impact:start -->

**Risk: High.** <paragraph>

| Core flow | Data     | Security | Contracts | Reach  | Tests   |
| --------- | -------- | -------- | --------- | ------ | ------- |
| **High**  | **High** | None     | None      | Medium | Covered |

<details><summary>Why each rating</summary>

- **Core flow — High:** recomputes `CompletedWork` accrual.
- **Data — High:** no migration, but recomputes stored amounts; not flag-gated.
- **Reach — Medium:** `update_status` has callers in `opportunity` and `form_receiver`.

</details>
<!-- pr-impact:end -->
```

- Bold every column that set the overall level. Write `None` in the table for untouched areas, never leave a cell blank or `—`.
- In the details, list every dimension that isn't None, Low included, so reviewers can see what the safe-but-present changes are (`**Data — Low:** adds nullable \`notes\` column; reversible.`). Add the test evidence line when it raised the level or when the author wasn't asked.
- If the author overrode the grade, the bold line reads `**Risk: High** (author's call; graded Medium).` and the paragraph gives the author's reason, in their words if they gave one.

**The paragraph** is 2–4 sentences, about 60 words at most:

1. What the riskiest part of the change is and which flow or contract it touches. Lead with the risky behaviour, not the file name.
2. What limits or worsens the damage: a flag, no migration, how a defect would surface and when.
3. Where reviewers should spend their time, and what they can skim.

> **Risk: High.** Changes how accrual is recalculated when a visit is re-approved, on the payment accrual core flow. There is no migration and the change isn't flag-gated, so a bug would surface as wrong amounts in the next invoice cycle. Review the recalculation in `update_status` closely; the template changes are mechanical.

If the author skips the label, there is no Impact section either. On an existing PR, ask whether to also remove the current risk label and Impact section; do only what they say.

## 6. Apply

1. **Label exists?** (both modes) Create the chosen label only if the repo lacks it, with these exact colours and descriptions:

   ```bash
   gh label create "Low Risk"    --color 0E8A16 --description "Small blast radius; unlikely to break existing behaviour"
   gh label create "Medium Risk" --color FBCA04 --description "Changes existing behaviour or shared code; worth a careful review"
   gh label create "High Risk"   --color B60205 --description "Touches a core flow, data, security or an external contract; review closely"
   ```

   Never `--force`: it would rewrite a label the repo already has.

2. **Body.** Place the Impact section:

   - If the body has a `pr-impact:start`/`end` block, replace the block (and keep the `### Impact` heading above it).
   - Otherwise insert `### Impact` and the block as the first subsection under `## Safety Assurance`, before `### Safety story`.
   - No `## Safety Assurance` section: add it with just the Impact section, after `## Technical Summary`.
   - Touch nothing else in the body.

3. **One `gh pr edit`** swaps the label and rewrites the body together, so the PR never carries two risk labels. Add the chosen label unless it's already there, and `--remove-label` every other risk label on the PR, even when the chosen one is unchanged. Pass `--body-file` only if the Impact section changed. If neither labels nor body change, skip the edit.

Then report the level, the label change (`Medium Risk → High Risk`), and the PR URL.

## Hard rules

- At most one of `Low Risk`, `Medium Risk`, `High Risk` on a PR after this skill runs.
- The author confirms or overrides before anything is labeled; never label on the grade alone.
- An earlier author override is kept unless the author changes it.
- Test evidence only raises Low to Medium; nothing lowers a High.
- Label swap and body rewrite happen in one `gh pr edit`.
- Never touch labels other than the three risk labels, and never recolor or rename an existing label.
- Don't backfill old PRs. Grade a PR only when asked.
