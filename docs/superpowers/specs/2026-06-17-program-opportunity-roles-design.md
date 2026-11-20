# Program / Opportunity Organization Role Restructuring

**Date:** 2026-06-17
**Status:** Design — pending review
**Scope:** Subsystem #3 of 3 in the larger organization/permission rework. This spec covers only the program/opportunity role restructuring. The LLOEntity merge + org-merging (#1) and the signup/invite overhaul (#2) are separate specs.

## Problem

Today the organization that owns a `Program` (`Program.organization`, an org with `program_manager=True`) holds blanket "program manager" (PM) oversight over every `ManagedOpportunity` in that program. PM-ness is derived from `request.org.program_manager` + an admin membership and is checked in `program/utils.py`, `organization/decorators.py`, the `OrganizationMiddleware`, and several views/mixins.

We need a richer set of org-to-program and org-to-opportunity relationships:

- A program may have an optional **funder** organization with the same access as the program org.
- A program may have any number of **watcher** organizations with read-only access to the program and its opportunities.
- PM-level oversight is **pushed down** to a per-opportunity **supervising organization** (an additional delegate that gets PM-level access to that one opportunity).

## Decisions (from brainstorming)

1. **Supervising org is a separate new role**, distinct from `opportunity.organization` (the executing Network Manager). An opportunity therefore has both an executor and a supervisor, often different orgs.
2. **Program org keeps blanket access** to all its opportunities; the supervising org is an *additional* delegate that also gets PM-level access to its opportunity.
3. **Data model: dedicated relations (Approach A)** — direct FKs / M2M, matching the existing FK-based design, rather than generalized role tables.
4. **Access = min(relationship ceiling, internal org role).** Manager/funder/supervisor mirror today's program-org behavior (admin→manage, member→standard, viewer→read). Watcher caps *every* member at read-only regardless of internal role.
5. **Supervisor choices:** the program org, its funder, or orgs with an accepted `ProgramApplication` (participating Network Managers).
6. **funder is creation-only** (immutable after the program is created). **watchers are editable** post-creation by users with manage access.

## Out of scope

- The `funder` boolean is added to `Organization` here because this spec depends on it; the rest of the `Organization` structural changes (LLOEntity merge, org merging, `verified`) live in spec #1.
- No changes to opportunity execution, payments, or the Network Manager application flow beyond what permission rewiring requires.

---

## Data model changes

### `Organization` (`commcare_connect/organization/models.py`)

Add a boolean alongside the existing `program_manager`:

```python
funder = models.BooleanField(default=False)
```

Editable in Django admin (add to `OrganizationAdmin` list/fields). Default `False`.

### `Program` (`commcare_connect/program/models.py`)

```python
funder = models.ForeignKey(
    Organization,
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="funded_programs",
)
watchers = models.ManyToManyField(
    Organization,
    blank=True,
    related_name="watched_programs",
)
```

- `funder` must reference an org with `funder=True` — enforced in `ProgramForm.clean()` and by limiting the form field queryset. Set only at creation (see UI section).
- An org may not be both `funder` and a `watcher` of the same program — enforced in `ProgramForm.clean()`.

### `ManagedOpportunity` (`commcare_connect/program/models.py`)

```python
supervising_organization = models.ForeignKey(
    Organization,
    on_delete=models.PROTECT,
    related_name="supervised_opportunities",
)
```

- **Non-nullable.** `ManagedOpportunity` uses multi-table inheritance, so this column lives on the child table (`program_managedopportunity`) and does **not** exist on the base `opportunity` table — making it non-null has no effect on unmanaged opportunities. Every managed opportunity must have a supervisor.
- Defaults to the program's org in the create form.
- Any non-form creation path must supply it — notably the `ManagedOpportunity` test factory needs a `supervising_organization` (default it to the program's org via a factory `SubFactory`/`LazyAttribute`).

### Migration

Three-step migration so existing rows are not rejected by the NOT NULL constraint (one migration file, operations in order):

1. `AddField` `supervising_organization` as nullable.
2. `RunPython` backfill: for every existing `ManagedOpportunity`, set `supervising_organization = program.organization` (idempotent — only fills nulls; reverse is a no-op).
3. `AlterField` to `null=False`.

---

## Permission / access model

Effective access for a user = **min(relationship ceiling, the user's internal role in the related org)**.

| Relationship | Ceiling | Scope |
|---|---|---|
| Program org (`Program.organization`) | full (admin→manage, member→standard, viewer→read) | program + all its opportunities |
| Funder org (`Program.funder`) | same as program org | program + all its opportunities |
| Watcher org (`Program.watchers`) | **read-only for every member** | program + all its opportunities |
| Supervising org (`ManagedOpportunity.supervising_organization`) | same as program org | that one opportunity |
| Executing NM (`opportunity.organization`) | unchanged | that one opportunity |

### New helpers (`commcare_connect/program/utils.py`)

```python
def org_program_role(org, program) -> "manage" | "view" | None:
    # manage if org == program.organization or org == program.funder
    # view   if org in program.watchers
    # None   otherwise

def request_can_manage_program(request) -> bool:
    # org_program_role(request.org, program) == "manage"
    #   AND request.org_membership is admin/member (not viewer)
    # OR request.user.has_perm(ALL_ORG_ACCESS)

def request_can_view_program(request) -> bool:
    # org_program_role(...) in {"manage", "view"} (any membership for view)
    # OR ALL_ORG_ACCESS

def request_supervises_opportunity(request, opp) -> bool:
    # True if request.org is the program org, funder, or the opp's
    #   supervising_organization, modulated by internal admin/member role.
    # Watchers -> view only (handled by request_can_view_program).
    # OR ALL_ORG_ACCESS
```

`manage` requires a non-viewer internal role (mirrors today's PM-requires-admin/member). `view` is granted to any member (including viewers) of an org with a manage/view relationship.

### Rewired entry points

These existing checks delegate to the new helpers (behavior preserved for the program org, extended to funder/watcher/supervisor):

- `program/utils.py`: `is_program_manager`, `is_program_manager_of_opportunity`, `get_managed_opp`.
- `users/middleware.py`: `request.is_opportunity_pm` (via `_is_opportunity_pm`).
- `organization/decorators.py`: `request_user_is_program_manager`, `@org_program_manager_required`, `@opportunity_program_manager_required`, `OrganizationProgramManagerMixin`.
- `program/views.py`: `ProgramManagerMixin`.

### New gates

- A **read-only gate** (decorator + CBV mixin) for watcher-accessible program/opportunity views: passes for any org with manage/view relationship.
- **Write paths must require `manage`/supervisor explicitly.** Watchers must be blocked at the form/POST/API-mutation layer, not merely hidden in templates. Every program/opportunity mutation view is audited to require the manage-level gate.

---

## Creation / edit UI

### Program create/edit (`ProgramForm`, `program/views.py: ProgramCreateOrUpdate`)

- Add `funder` select — queryset filtered to `Organization.objects.filter(funder=True)`, excluding the program org. **Shown only on create**; on edit the field is rendered read-only / omitted (funder is immutable).
- Add `watchers` multi-select — editable on both create and edit. Excludes the program org and the funder.
- `clean()` validates: funder has `funder=True`; funder ∉ watchers.

### Managed opportunity create/edit (`ManagedOpportunityInitForm`, `program/views.py`)

- Add `supervising_organization` select — queryset = {program org} ∪ {funder if set} ∪ {orgs with an accepted `ProgramApplication` for this program}. Default = program org.
- Required for managed opportunities.

---

## Testing

Per repo convention ("test functions, not view responses"):

1. **`program/utils.py` helpers** — full matrix: each relationship (program org / funder / watcher / supervisor / unrelated / executing NM) × each internal role (admin / member / viewer) → expected `manage` / `view` / `None`, plus `ALL_ORG_ACCESS` override. Parametrized.
2. **Form validation** — `ProgramForm`: invalid funder (org without `funder=True`), funder==watcher overlap, funder hidden/ignored on edit. `ManagedOpportunityInitForm`: supervisor default = program org, supervisor queryset restricted to allowed orgs, required for managed opps.
3. **Migration backfill** — existing `ManagedOpportunity` rows get `supervising_organization = program.organization`.
4. **Write-path guards** — focused view tests asserting a watcher org member receives 403/404 on representative program and opportunity mutation endpoints, and that funder/supervisor members succeed where the program org would.

---

## Risks / notes

- The biggest risk is an un-audited write path that a watcher can reach. Mitigation: enumerate every program/opportunity mutation view during implementation and assert the manage gate in tests.
- `ManagedOpportunity` is a multi-table-inherited subclass of `Opportunity`; the new FK lives on the child table — confirm migrations and `select_related` paths in querying code.
- `on_delete=models.PROTECT` on `supervising_organization` blocks deleting an org that supervises opportunities; org-merge (spec #1) must reassign supervisors before deletion.
