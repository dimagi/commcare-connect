# ADR-0011: Let ConnectID own worker identity and link workers to HQ users at claim

**Status:** Accepted

**Author:** Charl Smit · **Created:** 2026-11-13

**Extends:** [ADR-0006](0006-hq-form-forwarding-ingest.md)

## Context

Workers use one identity across Connect and the CommCare apps they deliver through. That identity, with phone-based sign-up and recovery, is provided by the separate ConnectID service. The forms workers submit reach Connect from CommCare HQ under an HQ mobile worker username in the opportunity's domain (ADR-0006), so Connect must know which Connect user each HQ username belongs to. Organization staff, by contrast, sign in to the Connect website.

## Decision

We will treat ConnectID as the identity provider for workers, and link each worker to an HQ mobile worker per domain and HQ server.

- The mobile app calls Connect's API with a ConnectID OAuth token. Connect validates it by OAuth2 token introspection against ConnectID, and creates the local `User` the first time a token is seen. The ConnectID username is the worker's primary identifier.
- Connect does not store worker passwords or run worker sign-up.
- When a worker claims an opportunity, Connect creates an HQ mobile worker in the deliver app's domain, using the opportunity's `HQApiKey`, and records a `ConnectIDUserLink` with the HQ `commcare_username`, `domain` and `hq_server`. The link is unique on `(user, commcare_username, hq_server)`.
- The form receiver finds the worker by matching the form's username to `ConnectIDUserLink.commcare_username`.
- HQ authenticates to Connect with its own OAuth client credentials, never by forwarding a worker's token.
- Organization staff sign in to the website with a Connect account (username or email) or through CommCare HQ as an OAuth identity provider.

## Consequences

- Accepting a token Connect hasn't seen before depends on ConnectID being reachable.
- A worker can't deliver until claim has created their HQ user; a failure to reach HQ fails the claim.
- A form from an HQ mobile worker with no `ConnectIDUserLink` is rejected with a 400, even for apps without an opportunity.
- Renaming the HQ user breaks the link that routes their forms.
- Worker and staff accounts are the same `User` model, distinguished only by their relationships (`OpportunityAccess` vs `UserOrganizationMembership`).

## Alternatives considered

- Workers sign up and sign in to Connect directly — not chosen; worker identity is shared with other CommCare services through ConnectID.
- HQ forwards the worker's token when it calls Connect — rejected because HQ would be impersonating the worker; HQ uses separate credentials.
