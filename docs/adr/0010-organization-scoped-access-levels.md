# ADR-0010: Grant access as the weaker of the organization's relationship and the user's role

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

## Context

Connect is shared by many organizations. A program is owned by one organization, may have a funder and read-only watchers, and runs opportunities that other organizations deliver. Some opportunities are overseen by a supervising organization other than the program's owner. Within each organization, users are admins, members or viewers. Access used to be derived from whether the request's organization was a program manager plus the user's admin membership, checked separately in many places, which couldn't express funders, watchers or per-opportunity supervision.

## Decision

We will scope every web request to one organization and compute access to a program or opportunity from two independent levels.

- Web pages live under `/a/<org_slug>/`. The organization in the URL is the one the user is acting as; a user who isn't a member of it gets no access.
- `AccessLevel` has four ordered values: `NONE`, `VIEW`, `STANDARD`, `ADMIN`.
- The user's level comes from their role in the acting organization: admin → `ADMIN`, member → `STANDARD`, viewer → `VIEW`.
- The organization's level comes from its relationship to the resource. On a program: owner (`Program.organization`) and `funder` → `ADMIN`; `watchers` → `VIEW`. On an opportunity: the delivering `organization` and the `supervising_organization` → `ADMIN`; otherwise the program relationship applies.
- Effective access is the lower of the two levels.
- Relationships are stored as direct foreign keys and a many-to-many (`Program.funder`, `Program.watchers`, `Opportunity.supervising_organization`), not as a generic role table.
- Every opportunity belongs to a program (`Opportunity.program`).
- Users with the `ALL_ORG_ACCESS` permission get `ADMIN` on everything.

## Consequences

- A watcher organization can't grant edit rights to its members, whatever their role.
- A user acting in two organizations sees different access to the same opportunity depending on which `org_slug` they use.
- Adding a new kind of relationship means a new field and a change to the organization-level rules, not data in a role table.
- Views check access through shared decorators per scope, so a view that skips them has no access control.
- Mobile and API endpoints authenticate the worker, not an organization, and are outside this model.

## Alternatives considered

- Keep deriving access from the organization's program-manager flag and admin membership — rejected because it couldn't express funders, watchers or per-opportunity supervision.
- Generic role tables relating organizations to programs and opportunities — rejected in favour of dedicated relations, matching the existing foreign-key design.
- Give watchers' members their normal role — rejected because a watcher relationship is meant to be read-only for everyone in that organization.
