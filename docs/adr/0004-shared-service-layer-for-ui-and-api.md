# ADR-0004: Shared service layer for UI and API

**Status:** Proposed

**Author:** Charl Smit · **Created:** 2026-11-19

**Extends:** [ADR-0003](0003-one-access-model-for-ui-and-api.md)

## Context

The UI and the API make the same changes through separate code: forms and views on one side, serializers and API views on the other. Their rules have diverged. The API accepts worker phone numbers the UI rejects. Opportunities created through the API lack the credential configuration the UI creates. Program applications move between statuses under different rules on each side. Reads diverge as well, since each API endpoint builds its own queryset.

The API is to read every table and, later, do anything the UI can, starting with opportunity and program management. Copying each rule into a serializer would multiply these differences.

## Decision

We will put each app's rules in one layer that the UI, the API, Celery tasks, management commands and other apps all call.

- Every write to an app's data goes through a service in that app's `services`. A service takes the acting user and plain values, never a request.
- A service checks access as ADR-0003 defines, enforces the business rules, runs the transaction, and dispatches follow-up work such as tasks, emails and CommCare HQ calls after commit.
- A service rejects bad input with one validation error type whose messages are keyed by field. Forms and API views translate that error into their own format.
- Forms and serializers only parse and shape input. A rule that reads the database or states business policy belongs in the service.
- Every model the API exposes has one function that returns the rows a user may see, built on ADR-0003. UI tables and API endpoints both start from it.
- API serializers list their fields explicitly. Secrets such as HQ API keys, OAuth tokens and credentials are never serialized. Internal tables such as sessions, scheduled tasks, feature flags and history events are exposed only behind a staff permission, if at all.
- Where the UI and the API apply different rules today, the UI's rule is kept unless it is a bug.
- New code follows this decision. Existing code moves to it when it is changed.

## Consequences

- Each business rule exists once, so the UI and the API can't drift apart.
- New API endpoints, read or write, are thin wrappers over existing services and visibility functions.
- Unifying the rules changes behaviour for existing API clients wherever the API differed from the UI.
- Business rules are tested by calling services directly rather than through views.
- Simple create and update operations gain a layer they did not need before.
- Old and new styles coexist until existing code has been moved.

## Alternatives considered

- Put the rules on models — rejected because operations span several models and apps and need the acting user and access checks.
- Let the API call Django forms — rejected because forms carry UI layout and expect HTML-shaped input.
- Let the UI call DRF serializers — rejected because serializers combine validation with saving and expect API-shaped input.
- Expose a generic API over every model — rejected because it would bypass the business rules.
