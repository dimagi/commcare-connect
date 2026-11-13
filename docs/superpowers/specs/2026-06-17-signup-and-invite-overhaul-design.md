# Signup & Org-Invite Overhaul

**Date:** 2026-06-17
**Status:** Design — pending review
**Scope:** Subsystem #2 of 3 in the organization/permission rework. Covers open org creation on signup, the no-org landing page, and a rebuilt org-invitation flow (new + existing users, 7-day expiry, accept-only-by-invited-email, admin notification on accept). The program/opportunity roles (#3) and the LLOEntity merge + org-merging (#1) are separate specs.

## Dependencies & sequencing

- **Depends on spec #1** for `Organization.verified` (consumed here) and for the simplified `OrganizationSelectOrCreateForm` (spec #1 removes the LLO-entity select from org creation). This spec only changes the *gate* and *defaults* of org creation, not the form's field structure.
- Independent of spec #3.

## Decisions (from brainstorming)

- **Dedicated `OrganizationInvitation` model.** No `UserOrganizationMembership` is created until acceptance.
- **`verified` is purely informational** — tracked/editable in Django admin only. No capability gating, **no UI banner**.
- Any authenticated user may create an organization; new orgs default `verified=False` and the creator becomes ADMIN.

---

## Part A — Open org creation

`organization/views.py: organization_create` currently requires `WORKSPACE_ENTITY_MANAGEMENT_ACCESS`.

- **Remove** the `@permission_required(WORKSPACE_ENTITY_MANAGEMENT_ACCESS, ...)` decorator; keep `@login_required`. Any authenticated user may create an org.
- New org: `verified=False` (default), creator added via `UserOrganizationMembership` as `ADMIN` (unchanged behavior).
- No limit on orgs-per-user (creating multiple is allowed).
- "Create on signup" is **offered, not forced**: a freshly-signed-up user with no membership lands on the no-org page (Part B), which links to org creation. Org creation is not a mandatory signup step.

---

## Part B — No-org landing page

Today membership-less users are sent to `home` (`pages/home.html`); the `create_org_for_user` signal that tries to redirect them is a **no-op** (returning a redirect from a signal receiver does nothing) and is removed.

- New view + template `organization/no_organization.html`: explains the user isn't a member of any organization and offers two paths — **(1)** reach out to your organization's administrator to be invited, and **(2)** create a new organization (links to `organization_create`).
- Wire-up: `users/views.py: UserRedirectView.get_redirect_url()` — when `request.user.memberships` is empty, redirect to this page (replacing the current `reverse("home")`).
- Remove the dead `create_org_for_user` signal (or repurpose it into the redirect logic; the redirect already lives in `UserRedirectView`, so deletion is cleanest).
- The page is reachable by any authenticated user with no memberships; a user who later gains a membership is routed normally.

---

## Part C — Rebuilt invitation flow

### `OrganizationInvitation` model (`organization/models.py`)

```python
class OrganizationInvitation(BaseModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=UserOrganizationMembership.Role.choices,
                            default=UserOrganizationMembership.Role.MEMBER)
    token = models.CharField(max_length=64, unique=True, default=<secrets.token_urlsafe>)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="sent_invitations")
    expires_at = models.DateTimeField()        # set to created + 7 days on creation
    accepted_at = models.DateTimeField(null=True, blank=True)
```

- `expires_at` defaults to creation time + 7 days (set in `save()`/at creation).
- Helper props: `is_expired` (`now > expires_at`), `is_accepted` (`accepted_at is not None`), `is_pending` (not expired, not accepted).
- Index on `token` (unique already) and on `(organization, email)`.
- The old `UserOrganizationMembership.invite_id` field becomes vestigial — drop it (and its index) in a migration; nothing in the new flow reads it.

### Sending an invite (rewrite `MembershipForm` + `add_members_form`)

- `MembershipForm` (rename to `OrganizationInvitationForm`): fields `email`, `role`. **Drop** the "user must already exist" validation — invites may target any email, new or existing.
- Validation: reject if the email already belongs to a current member of this org; if a pending (non-expired, non-accepted) invite already exists for this email+org, **refresh it** (new token + new `expires_at`) rather than erroring.
- On submit: create the `OrganizationInvitation`, send the invite email (below). Keep the existing `@org_admin_required` gate and the redirect back to the members tab.

### Invite email (templated)

- Replace the hardcoded string in `organization/tasks.py: send_org_invite` with proper templates: `templates/organization/email/invite.txt` (+ optional `.html`).
- Content: inviting user's name, org name, assigned role, expiry date, and the **token-based accept URL**.
- Sent to the invitation's `email` (works whether or not a User exists yet) via `send_mail_async`.

### Accept URL + view

- New token-based route **outside** `/a/<org_slug>/`: e.g. `path("invitations/<str:token>/accept/", ...)` (invitee may be unauthenticated and is not yet an org member).
- `accept_invitation(request, token)` logic:
  1. Look up invitation by `token` (404 if unknown). If `is_accepted` → friendly "already accepted" message. If `is_expired` → "this invite has expired, ask the admin to resend" message.
  2. **Unauthenticated** + email has **no account** → send to allauth **signup** with the email pre-filled and locked to `invitation.email`; carry the token through so acceptance completes after signup.
  3. **Unauthenticated** + email **has an account** → send to login (as that email), then return to acceptance.
  4. **Authenticated**: enforce **accept-only-by-invited-email** — `request.user.email` must equal `invitation.email` (case-insensitive); otherwise reject with a clear message (e.g. "this invite was sent to a different address").
  5. On success: create `UserOrganizationMembership(user, organization, role=invitation.role)` (idempotent if somehow already a member), set `accepted_at=now`, and **notify admins** (below). Redirect to `opportunity:list` for the org.
- **Email verification:** because the token was delivered to `invitation.email`, acceptance proves control of that address — mark the allauth `EmailAddress` verified on acceptance to avoid a redundant verification step. *(Flag for review — alternative is to keep standard mandatory verification.)*

### Admin notification on accept

- New templated email (`templates/organization/email/invite_accepted.txt` + optional `.html`) sent via `send_mail_async` to **all current ADMIN-role members** of the org (memberships with `role=ADMIN` and non-null email).
- Content: who accepted (name/email), org name, role granted.

### Pending-invite management (admin UI)

- On the org members tab, list **pending invitations** (email, role, invited_by, expires_at) alongside members.
- Actions for org admins: **resend** (refresh token + expiry, re-send email) and **revoke** (delete the invitation).
- Register `OrganizationInvitationAdmin` in Django admin for support visibility (read-mostly).

---

## Migrations

1. Create `OrganizationInvitation`.
2. Remove `UserOrganizationMembership.invite_id` (+ its index). No data backfill needed — the old `accept_invite` was a no-op and created no pending state worth preserving (existing memberships are already "accepted").

---

## Testing

Per repo convention (test functions, not view responses):

1. **Invitation model** — `expires_at` set to +7 days; `is_expired`/`is_pending`/`is_accepted` transitions.
2. **Invite creation** — `OrganizationInvitationForm` accepts a brand-new email and an existing-user email; rejects an existing member; refreshes an existing pending invite instead of duplicating.
3. **Acceptance** (test the underlying acceptance function, not the HTTP view): creates membership with the invite's role + sets `accepted_at`; rejects when authenticated user's email ≠ invite email; rejects expired; rejects/no-ops double-accept.
4. **New-user flow** — accepting as an unregistered email routes to signup locked to that email; membership created post-signup; (if adopted) the `EmailAddress` is marked verified.
5. **Admin notification** — accepting an invite enqueues an email to all ADMIN members and no one else.
6. **No-org redirect** — a membership-less authenticated user is routed to the no-org page; org creation is reachable without `WORKSPACE_ENTITY_MANAGEMENT_ACCESS`; created org has `verified=False` and the creator is ADMIN.

---

## Risks / notes

- **Auto-verifying the invited email** (acceptance step) is a security-sensitive convenience — flagged for review. If rejected, fall back to allauth's mandatory verification before membership takes effect.
- **Token security**: use `secrets.token_urlsafe`; tokens are single-use (consumed at acceptance) and time-boxed (7 days). Don't reuse the old guessable scheme.
- **Email casing**: store/compare `email` case-insensitively to avoid mismatches between the invite and the account (`User.email` is unique-when-non-null; allauth lowercases on signup).
- **Open-registration toggle**: org creation now relies on normal signup, which is still governed by `ACCOUNT_ALLOW_REGISTRATION` — unchanged, but worth noting that invites to brand-new users assume registration is open.
- **Cross-spec**: this spec assumes spec #1 already simplified the org-creation form (no LLO-entity select). If specs land in a different order, reconcile `OrganizationSelectOrCreateForm` accordingly.
