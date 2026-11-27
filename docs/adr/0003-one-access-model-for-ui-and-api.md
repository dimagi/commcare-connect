# ADR-0003: One access model for UI and API

**Status:** Proposed

**Author:** Charl Smit · **Created:** 2026-11-19

## Context

Connect decides who may see or change a program or opportunity in four different ways. The UI combines the URL's organization's relationship to the resource with the user's role in that organization. The automation API accepts an admin of the delivering organization or of the program's organization. The data export API's reads accept any member of a related organization, ignoring role and watchers. Its writes accept an admin of any organization with admin access to the opportunity, and require the `write` scope. As a result, delivering-organization admins can add payment units and activate opportunities through the automation API although the UI forbids it, admins of the supervising organization and the funder are refused by it, and watchers can't read through the data export API.

The data export writes already decide access from the user and the opportunity alone, with no acting organization, through `user_is_opportunity_admin` and `user_is_opportunity_pm`. This decision extends that approach to every surface.

API requests have no organization in their URL, so the UI's check can't be reused as it is. The API is to grow until it can read every table and do anything the UI can, so every new endpoint would inherit whichever check it copies.

## Decision

We will decide access in one place, with functions that take a user and a resource rather than a request.

- A user's access level on an opportunity, through one organization they belong to:

  | User's role in the organization | Organization delivers, supervises, runs or funds the program | Organization watches the program |
  | ------------------------------- | ------------------------------------------------------------ | -------------------------------- |
  | admin                           | ADMIN                                                        | VIEW                             |
  | member                          | STANDARD                                                     | VIEW                             |
  | viewer                          | VIEW                                                         | VIEW                             |

- A user in more than one of the opportunity's organizations gets the highest access level any of those organizations gives them.

  Example: a member of the delivering organization who is also an admin of a watcher organization gets STANDARD.

- Program-manager actions require ADMIN through an organization other than the delivering organization.
- Users with the `ALL_ORG_ACCESS` permission have ADMIN everywhere.
- The UI computes the same rule for the URL's organization only.
- API calls name no acting organization. When a call creates a resource, the owning organization is a field of the payload, and the caller needs the required access through that organization.
- UI views, API views and services all check access through these functions.
- An OAuth token's scopes cap that access: API writes also require the `write` scope, and reads the `read` or `export` scope.

## Consequences

- The API and UI grant the same access.
- Delivering-organization admins can no longer add payment units or activate opportunities through the API, which breaks clients that rely on it.
- Admins of the supervising organization and the funder gain API write access, and watchers gain API read access.
- A user in several organizations gets, in one API call, the access the UI gives only after switching organization.
- An API caller can't limit a call to one of their organizations; they always get their highest access level.
- The audit trail records which user made an API change, not which organization they acted for.
- Automation clients whose tokens lack the `write` scope can no longer write.
- A change to the access rules is made once.

## Alternatives considered

- An acting-organization header on every API call — rejected because the resource already names the organizations that matter, and the header adds a parameter every client must send.
- One organization per OAuth token — rejected because a user who works for several organizations would need several tokens.
- Keep a separate check per surface — rejected because the existing checks have already diverged.
