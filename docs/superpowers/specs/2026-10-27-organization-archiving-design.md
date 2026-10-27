# Archiving organizations from the LLO Directory

## Goal

Retire an organization that no longer works with Connect without deleting it, so none of its records are
lost. An archived organization disappears from the app. The LLO Directory is the one place it can still
be found, and where it can be restored if it starts working with Connect again.

## Archiving is refused while the organization has unfinished work

An organization can't be archived while it is any of these on live or upcoming work:

- the **owner** of a program;
- the **funder** of a program;
- the **network manager** (the delivering organization) of an opportunity;
- the **supervising organization** of an opportunity.

An accepted program application doesn't count, and neither does watching a program.

A live or upcoming opportunity under a program the organization owns or funds also blocks archiving,
even if the program itself has ended. That combination occurs in real data:
- An opportunity's dates are only checked against its program's at creation.
- Opportunities can be extended past their program, and programs can be shortened below their
  opportunities.
- Before opportunities had to belong to a program, the migration that made the link required put every
  existing opportunity without one into a placeholder program, owned by an organization it created for
  them ("Legacy Real Opportunities" or "Legacy Test Opportunities"). Those programs end on the day that
  migration ran, so any of their opportunities still running is already past its program's end date.

When the rules are unclear, they err on the side of blocking.

### Live or upcoming

- **A program** is live or upcoming when its end date is today or later.
- **An opportunity** is live or upcoming when it isn't archived and either:
  - its end date is today or later, whether or not it is switched to active or a test opportunity; or
  - it has no end date and is still active.

  An archived opportunity is finished, so it isn't live or upcoming. It can still block archiving through
  unsettled money or reviews.

### Unsettled money and reviews

These block archiving whatever the dates. They are checked on every opportunity linked to the
organization through any of the four roles above. Even an organization that only delivered or
supervised an opportunity may be the one that still has to act on it. An opportunity blocks when it has:

- **an invoice that isn't settled:** any status other than paid, cancelled by the network manager,
  rejected by the program manager or archived. Listing the settled statuses rather than the pending ones
  means a status added later blocks until it is added to that list;
- **payments still owed to workers:** they have earned more than they have been paid;
- **visits awaiting review:** by the network manager, or by the program manager. Opportunities that
  verify visits automatically are left out, matching the "pending review" figures on the program homes.

Payments that workers haven't confirmed receiving do **not** block. Confirming is up to the worker, and
many never do it, so old opportunities could otherwise never be cleared.

**Known limitation, to revisit.** A visit's program manager review status starts as pending and was never
backfilled. Approved visits on older opportunities that never had a program manager review therefore still
count as awaiting review. They could stop an organization that delivered one of those opportunities from
being archived, with nobody left to review them. The check stays as described for now. If it blocks real
organizations, count a visit as awaiting program manager review only once a review was actually requested.

When archiving is refused, the user sees every program and opportunity that blocks it, the
organization's role on each, and why each one blocks.

## What archiving does

- **The organization's contacts are archived with it.**
- **An optional reason** is appended to the organization's notes as a clearly labelled line, for example
  `[System Note] Archived on 2026-10-27 by someone@example.com: <reason>`. Notes are part of the
  organization's change history, so the reason is also on the audit trail.
- **Before confirming,** the user is shown:
  - what will happen;
  - the programs that will become inaccessible, each with a count of its opportunities.
- **Archived is its own flag**, separate from the organization's Active / Inactive / Prospective status.
  A restored organization therefore comes back with the status it had.

## What disappears

Visibility follows **ownership**:

- **The archived organization** disappears everywhere except the LLO Directory and Django admin.
- **A program** disappears when the organization that owns it is archived.
- **An opportunity** disappears when the organization that owns its program is archived, whoever
  delivers it. For opportunities created before they had to belong to a program, the owner is the
  placeholder organization described above, so archiving the organization that delivers one leaves it
  visible, as for any network manager.
- **Funding, supervising or delivering isn't owning.** Archiving a funder, supervising organization or
  network manager hides nothing beyond that organization itself.

Because of the blocking rules, everything that disappears has already finished.

"Disappears" covers:
- **Listings, pickers, counts and reports.**
- **The data-export API.**
- **Background jobs and emails**, such as reminders, audit reports and automated invoices.
- **Direct links.** A link to the archived organization, or to a program or opportunity hidden with it,
  shows a page saying the organization no longer exists.
- **Members.** A member whose only organization was archived lands on that page after logging in, and
  can create a new organization from there.

Where the archived organization's name appears on records that stay visible, how to show it is decided
place by place.

**Pending invites and applications** are left in place and don't block archiving.

**Form submissions are not affected.** Late submissions for an archived organization's opportunities are
still recorded, so nothing is lost.

## Restoring

- **From the directory,** an archived organization can be restored, with an optional reason recorded the
  same way as when archiving.
- **Contacts stay archived.** Restoring brings back the organization only. The restore step and the
  success message say so, and the restore step links to the organization's archived contacts in the
  Contacts tab, where they can be restored one by one. Some of them may have been archived for their own
  reasons before the organization was, so restoring them is left to the user rather than guessed.
- **Everything else** that was hidden comes back as it was, including members' access.
- **A single contact** of an archived organization can still be archived or restored on its own.

## Open, pending confirmation

These are left out until confirmed:
- **Notifying an organization's admins** when it is archived.
- **Hiding archived opportunities from mobile workers.** Until confirmed, workers can still see their past
  work and earnings in the app and confirm payments.
