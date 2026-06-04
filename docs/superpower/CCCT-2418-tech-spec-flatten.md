# Tech Spec: Remove Non-Managed Opportunities (Flatten `ManagedOpportunity` into `Opportunity`)

**Ticket:** [CCCT-2418: Remove Non-Managed Opportunities from Connect](https://dimagi.atlassian.net/browse/CCCT-2418)

---

## Goal

Collapse Connect's two opportunity types into one. After this work every opportunity is a **managed** opportunity, so Program Managers see a single, consistent model with no conditional feature gaps.

Concretely:

- **One creation path** — opportunities are created only inside a Program. The standalone "create a plain opportunity" flow is removed.
- **One UI** — no screen branches on `managed`. Program header, PM-review / justification workflow, invoices, budgets, and the "Org pay per delivery" column are always present.
- **One permission rule** — per-opportunity PM actions use `is_opportunity_pm` only. The org-level `program_manager` flag stays Program/NM-scoped and is no longer a per-opportunity fallback.
- **One model** — `ManagedOpportunity` is removed entirely. `Opportunity` carries the `program` link directly.

---

## Background

`ManagedOpportunity` is a multi-table inheritance (MTI) subclass of `Opportunity`, identified by the `managed` flag. Supporting both managed and non-managed opportunities adds significant complexity to the codebase through conditional logic, template branching, and separate permission models.

The MTI layer itself is very thin: `ManagedOpportunity` adds only a single field, `program` (a foreign key to `Program`), and no other model has a foreign key to `ManagedOpportunity`; visits, accesses, claims, and invoices all reference `Opportunity` directly. As a result, flattening the child table into the parent is a relatively straightforward change.


### Current Production State

There are currently no active, real-world non-managed opportunities in production.

- The only `active=True, managed=False` opportunity is an incomplete demo [opportunity](https://connect.dimagi.com/a/ai-demo-space/opportunity/01022406-a4a8-4374-8b9e-e1885c75de50/payment_units/create).
- All other non-managed opportunities are test opportunities.

### Codebase footprint

- ~67 `ManagedOpportunity` references across 16 files (views, helpers, tasks, API serializers, reports), plus test factories.
- ~33 `opportunity.managedopportunity` accessor uses across Python and templates.
- Live queryset filters on `managed=True` in `program` views/tasks and `opportunity` tasks.

All of these are mechanical to update once the fields live on `Opportunity`.

## Approach: flatten, migrate, then simplify

Move `program` onto `Opportunity`, backfill from the child table, point every legacy non-managed opportunity at a legacy Program, then delete the `ManagedOpportunity`.

**Product sign-off:** [confirmed](https://dimagi.atlassian.net/browse/CCCT-2418?focusedCommentId=470025) — migrate the legacy opps under **two legacy PM orgs** (one for test, one for real).

### Data migration

Three small migrations replace the MTI promotion entirely:

**Migration 1 — Schema:** Add `program` to `Opportunity`.

* Rename the `ManagedOpportunity.program` field to `program_old` and make it nullable.
* Add a new `program` field directly to `Opportunity` as **nullable** initially, to allow zero-downtime deployment. Once Migration 2 completes and every opportunity has a program assigned, a follow-up migration in Release 2 will add a `NOT NULL` constraint.
* Backfill the new `Opportunity.program` field from the existing `ManagedOpportunity.program_old` values.

  This approach minimizes code changes. The primary impact is that reverse relationship queries will need to be updated from `program__managedopportunity` to `program__opportunity`. We currently use this reverse relationship in only a few places (4), so the required code changes should be small. All other access patterns should continue to work with little or no modification.

    > [!NOTE]
    > The `claimed` field can be ignored during the migration, as it is not currently used anywhere in the codebase. It can be dropped as part of the cleanup.

----

**Migration 2 — Assign Programs to Legacy Non-Managed Opportunities**

This migration will create a small number of supporting objects (an LLO entity, PM organizations, Programs, and delivery types) before migrating existing opportunities. These models have minimal required fields, so creating them as part of the migration should be straightforward.


> Some parts of this migration (such as creating the legacy Entity, PM organizations, and Programs) could be performed manually. However, I prefer to include them in the migration to ensure a consistent setup across all environments and avoid manual intervention in production, staging, or local development environments.

1. Create a single **LLO Entity** to own the legacy space.
2. Under it, create **two PM organizations** (`program_manager=True`): one for test opportunities and one for inactive real opportunities. Create a **Program** for each organization with a delivery type, a budget of `0`, a start date of **January 1, 2020**, and an end date of **today**. Since these opportunities are inactive, the specific budget and date values are not important as long as the Program requirements are satisfied.
3. Assign a Program to every `Opportunity`:

   * Assign the test Program to all test opportunities (`is_test=True`).
   * Assign the real Program to all other non-managed opportunities.

   Once the supporting objects have been created, this update should be a straightforward bulk update.


---
After Migration 2, every opportunity will have a `program`, and "managed" will no longer be a meaningful state.

At that point, all application code can safely assume that `Opportunity.program` is populated.

**Migration 3 — Drop `ManagedOpportunity`** *(shipped in the next release; see Sequencing)*

Delete the `ManagedOpportunity` model, which will also remove the `program_managedopportunity` table.

> **Note:** I am not proposing to remove the `managed` column at this time, as it can serve as the original source of truth for whether an opportunity was historically managed or non-managed.


### Work areas

**Area 1 — Remove non-managed opportunity creation.**

Remove the standalone `opportunity:init` and `opportunity:init_edit` URLs, along with the **Add Opportunity** button on the opportunities list page. Retain the shared base views and forms (`OpportunityInit`, `OpportunityInitUpdate`, `OpportunityFinalize`, and `OpportunityInitForm`), as they are still used by the managed opportunity flow.

Update the setup wizard's edit link to use `program:opportunity_init_edit`. Per the PRD, add a notice on the opportunity creation screen indicating that the opportunity will be created under the current Program.

**Area 2 — Replace `ManagedOpportunity` with `Opportunity`.**

Remove `ManagedOpportunity` from the application layer and migrate all functionality to `Opportunity`, using the new `Opportunity.program` relationship.

**Area 3 — Remove managed/non-managed branching.**

Remove all managed/non-managed conditional logic throughout the codebase and standardize on a single opportunity model and workflow.

**Area 4 — Unify permissions.**

Simplify opportunity permissions by using is_opportunity_pm for all opportunity-level actions. Introduce is_opportunity_nm for organization-level access patterns where needed.

## Release Plan
Two releases, to keep the schema drop safe:

**Release 1**
1. **Area 1:** Remove non-managed opportunity creation, so no new non-managed opportunities appear.
2. Migration 1 and Migration 2, along with the minimal code changes required to support the new Opportunity.program relationship.
3. **Area 2:** Update all references from `ManagedOpportunity` to `Opportunity`.
4. Area 3 + Area 4 -> Remove managed/non-managed branching + Unify permissions


**Release 2**
1. **Migration 3:** Drop the `ManagedOpportunity` model and the underlying `program_managedopportunity` table once Release 1 is verified in production.
2. **Migration: NOT NULL constraint** — Add a `NOT NULL` constraint to `Opportunity.program` now that every row is guaranteed to have a program assigned.

### Alternative Approach Considered — Promote Non-Managed Opportunities into the MTI Child Table

Keep the `ManagedOpportunity` model and create a child row for every non-managed opportunity (using raw SQL, since Django multi-table inheritance does not support adding a child row to an existing parent through the ORM). These opportunities would be assigned to the legacy Programs, after which the managed/non-managed branching could be removed. The model flattening would then be handled in a separate future effort.

**Why this approach was not chosen**

This approach is lower risk in the short term because it only requires a data migration and avoids schema changes. However:

* It delays the model cleanup and requires touching much of the same code twice—once to remove managed/non-managed branching and again when the model is eventually flattened.
* It introduces awkward interim semantics for the `managed` flag, where promoted opportunities would have a `ManagedOpportunity` child row while still retaining `managed=False`.

While flattening the model requires additional work up front (a schema migration, reference updates, and a more careful deployment), it results in less overall effort and lands directly on the desired end state. The child-table promotion approach remains a fallback option if a schema change becomes undesirable.


## Rollback and Risk

**Overall risk is low** — there are no active real-world non-managed opportunities in production, so the migration affects only test data and one inactive demo opportunity.


**Release 1 is fully reversible.** Each step can be undone cleanly:

- *Migration 1*: drop `Opportunity.program`, rename `program_old` back to `program`.
- *Migration 2*: the legacy LLO entity, PM organizations, and Programs are freshly created with no organic user data (no memberships, no real interactions). They can be deleted in reverse order and `Opportunity.program` NULLed for the affected rows.
- *Code revert (Areas 1–4)*: a git revert of the release branch restores all `managed` branching and `.managedopportunity` accessor calls. These still work because the `ManagedOpportunity` rows and the `managed` flag are untouched throughout Release 1.

**The only irreversible step is Migration 3 (Release 2).** Dropping the `ManagedOpportunity` table cannot be undone without a database restore. This is why Migration 3 is intentionally deferred to a separate release, after Release 1 is verified in production.


## Test Plan

Smoke testing should be performed before release 1 to validate the critical flows, including opportunity creation, program assignment, and permission checks after the migration changes.



## Implementation Tickets

- Remove non-managed opportunity creation.(Release 1)
- Add `program` to `Opportunity`, backfill, and promote legacy non-managed opportunities. (Release 1)
- Replace `ManagedOpportunity` usage with `Opportunity` (model sweep). (Release 1)
- Remove managed/non-managed branching and unify opportunity permissions. (Release 1)
- Drop the `ManagedOpportunity` model and the underlying `program_managedopportunity` table. (Release 2)
