# LLOEntity → Organization Merge + Organization Merging

**Date:** 2026-06-17
**Status:** Design — pending review
**Scope:** Subsystem #1 of 3 in the organization/permission rework. Covers (a) flattening the `LLOEntity` model into `Organization` and (b) adding the ability to merge two organizations. The program/opportunity role restructuring (#3) and the signup/invite overhaul (#2) are separate specs.

## Dependencies & sequencing

1. **`ce/llo-entity-profile-fields` lands first.** This spec is written against the post-branch state, where `LLOEntity` already has profile fields, `verified`, ``LLO_ENTITY_INTERNAL_ACCESS`, `LLOEntityForm`, and the richer `LLOEntityAdmin`.
2. **Spec #3 (program roles) lands before the org-merge feature** — the merge's reassignment logic must handle the FKs spec #3 adds (`Program.funder`, `Program.watchers`, `ManagedOpportunity.supervising_organization`).
3. **`Organization.verified`** is created here (flattened from `LLOEntity`); **spec #2's signup flow consumes it.**

## Decisions (from brainstorming)

- **Flatten** `LLOEntity`'s fields onto `Organization`; remove the `LLOEntity` model and the `Organization.llo_entity` grouping FK. Each org becomes self-describing.
- The long-run "collapse multiple workspaces of one real org into one" is achieved later via the **org-merge feature built in this spec** — flatten duplicates the profile onto each child org; merging the orgs deduplicates.
- **Org-merge is a Django admin action** with an intermediate confirmation page.
- **Membership conflict on merge: target org's role wins** (the user keeps their role in the surviving org; the source membership is discarded).

---

## Part A — Flatten LLOEntity into Organization

### Field moves (`organization/models.py`)

Move these post-branch `LLOEntity` fields onto `Organization` (verbatim definitions, minus `members`):

- `short_name`, `has_used_connect`, `year_of_establishment`, `team_size`, `flws_managed`, `regions`, `website`, `office_address`, `contact_emails`, `eoi_links`, `notes`
- `countries` (M2M to `opportunity.Country`; `related_name` changes from `llo_entities` → `organizations`)
- `primary_sectors` (`ArrayField`; `PrimarySector` choices — still a TODO upstream, inherited as-is)
- `verified` (BooleanField, default `False`) — editable in Django admin
- The `_current_year()` helper and `PrimarySector` move alongside.

Remove the `LLOEntity` model and the `Organization.llo_entity` FK. `Organization.name` is retained as-is.

**Open consideration (flag for review):** `LLOEntity.name` is *not* copied onto the org (the org keeps its own `name`). In ~1:1 cases the entity name is redundant; where they differ, the entity name is dropped. If preserving it matters, we could stash it in `notes` during migration. Defaulting to "drop unless you say otherwise."

### Migration (multi-step, one sequence)

1. `AddField` all new fields to `Organization` (nullable / with defaults as on the branch).
2. `RunPython` backfill: for every `Organization` with a non-null `llo_entity`, copy each profile field from the linked `LLOEntity` onto the org (M2M `countries` copied too). Orgs sharing an entity each get a **copy** (intentional — deduped later via merge). Reverse = no-op.
3. `RemoveField` `Organization.llo_entity`; `DeleteModel` `LLOEntity`.

### Surface updates

- **`organization/forms.py`**: drop `llo_entity` handling from `OrganizationChangeForm`; remove entity select/create + `get_entity_wise_orgs()` from `OrganizationSelectOrCreateForm`; repoint the branch's `LLOEntityForm` to an `Organization` profile form (fold its field set + cleaners — `contact_emails`, `eoi_links`, `year_of_establishment` — into the org edit form).
- **`users/admin.py`**: remove `LLOEntityAdmin`; fold its `list_display`/`list_filter`/`filter_horizontal`/`member_count` (now via memberships) and profile fields into `OrganizationAdmin`, including `verified`.
- **`data_export/`**: repoint `LLOEntityDataView` + `LLOEntityDataSerializer` + the `llo_entity/` URL to serve `Organization` profile data (keep the endpoint name/path for API stability, source from `Organization`). Gate with `LLO_ENTITY_INTERNAL_ACCESS`.
- **`reports/helpers.py`**: change the KPI filter from `...organization__llo_entity` to filter directly on `organization`. The report's `llo` parameter becomes an organization filter. **Interim degradation:** cross-workspace rollups are lost until those orgs are merged (acceptable per the flatten-then-merge plan); note in the report UI/code.
- **Templates**: `header.html` (`request.org.llo_entity` → `request.org.short_name` or org name); `organization_create.html` / `organization_home.html` (remove entity select JS + `get_entity_wise_orgs`).
- **Factories/tests**: remove `LLOEntityFactory`; add profile fields to `OrganizationFactory`; migrate the branch's `test_llo_entity_models.py` / `test_llo_entity_forms.py` and existing `test_models.py` / `test_forms.py` to the org.
- **Permissions**: reconcile `WORKSPACE_ENTITY_MANAGEMENT_ACCESS` (edit profile / create org) and `LLO_ENTITY_INTERNAL_ACCESS` (internal list). Default: keep both, repoint to the org profile; do not rename in this spec.

---

## Part B — Merge two organizations

### Entry point

A custom **Django admin action** on `OrganizationAdmin`:
1. Select two or more orgs in the changelist → action **"Merge selected organizations…"**.
2. Intermediate confirmation page: choose the **target** (surviving) org; show per-related-model counts to be reassigned and a side-by-side of profile fields (target's values win, shown for visibility).
3. Confirm → execute.

The actual logic lives in a reusable, testable service function `merge_organizations(source, target)` in `organization/` (not inline in the admin), wrapped in `transaction.atomic`.

### Reassignment checklist

| Model.field | on_delete | Action |
|---|---|---|
| `CommCareApp.organization` | CASCADE | reassign source→target |
| `Opportunity.organization` | CASCADE | reassign |
| `Payment.organization` (nullable) | DO_NOTHING | reassign |
| `LabsRecord.organization` (nullable) | CASCADE | reassign |
| `Program.organization` | **PROTECT** | reassign **before** deleting source |
| `ProgramApplication.organization` | CASCADE | reassign (then dedupe per program) |
| `Flag.organizations` (M2M) | — | **Do not transfer.** Target's flag set is left unchanged; the source's flag memberships simply drop when the source org is deleted. |
| `UserOrganizationMembership` (unique user+org) | CASCADE | see conflict rule below |
| `Program.funder` (spec #3, SET_NULL) | SET_NULL | reassign |
| `Program.watchers` (spec #3, M2M) | — | add target, remove source, dedupe |
| `ManagedOpportunity.supervising_organization` (spec #3) | **PROTECT** | reassign **before** deleting source |

**Membership conflict (unique_together user+org):** if a user is in both orgs, **target's role wins** — delete the source membership, leave the target membership untouched. If the user is only in the source, move that membership to the target.

**Profile fields:** target org retains its own profile values; the source's profile is discarded (surfaced on the confirmation page).

**Feature flags:** the target org's `Flag` memberships are deliberately *not* modified by the merge. The source org's flag rows vanish with its deletion. Opportunities reassigned from the source fall under whatever flags the target org already has — no per-opportunity flag handling needed.

**Slug:** the source org is deleted; its slug is freed. No redirect is created (internal tooling). Note as a known limitation.

**Order of operations:** reassign all FKs/M2Ms (clearing the PROTECT relations) → resolve membership conflicts → delete the source org. Entire operation in one transaction; any error rolls back.

**Self-merge guard:** refuse if source == target.

---

## Testing

Per repo convention (test functions, not view responses):

1. **Flatten migration** — orgs with a linked `LLOEntity` receive its profile fields (incl. `countries` M2M, `verified`); orgs sharing an entity each get a copy.
2. **`merge_organizations(source, target)`** — exhaustive: every model in the reassignment checklist is moved; PROTECT relations (`Program`, `ManagedOpportunity.supervising_organization`) reassigned before delete; `watchers` M2M deduped; **target's `Flag` memberships are unchanged by the merge** (assert a flag on the source but not the target is absent from the target afterward); membership conflict resolves to target's role; source-only membership moves; source org deleted; self-merge refused; an injected error rolls the whole thing back (source still present, nothing reassigned).
3. **Forms/admin** — org profile form validation (emails, EOI links, year) on `Organization`; `OrganizationSelectOrCreateForm` no longer references entities.
4. **Reports** — `llo` filter now filters by organization; update `test_get_table_data_for_year_month_by_llo`.
5. **data_export** — `llo_entity/` endpoint serves org profile data; permission gate enforced.

---

## Risks / notes

- **Migration data loss:** the `LLOEntity.name` drop (see open consideration) and the report-rollup degradation are the two intentional losses — both flagged for review.
- **Cross-spec ordering:** the merge checklist hard-codes spec #3's fields; if spec #3 hasn't landed, those rows are skipped — keep the checklist and the migration dependency explicit so they aren't silently missed.
- **pghistory:** tracked child models (`Opportunity`, `PaymentInvoice`, etc.) keep their event rows; after reassignment their FKs point at the surviving org. `Organization` itself isn't pghistory-tracked, so no event model to migrate.
- `primary_sectors` carries the upstream `PrimarySector` TODO — real sector values must be filled in before this ships.
