"""Reconcile Connect ``WorkArea.opportunity_access`` with CommCare HQ case ``owner_id``.

One-off remediation for divergence caused by a partially-applied assignment sync:
``save_assignment`` writes the new assignee to Connect, then pushes owner changes to HQ in
100-case chunks. If a later chunk fails, the whole Connect transaction is rolled back
(``set_rollback``) while the HQ chunks that already committed stay put -- so HQ shows the new
owner and Connect reverts to the old assignee, with no audit trail. Visits by the true (HQ)
owner are then linked but never counted, because ``WorkArea.update_status`` only counts
visits belonging to the work area's *current* ``opportunity_access``.

This script treats **HQ as the source of truth**, aligns Connect's ``opportunity_access`` to
match, and recomputes work-area status so already-recorded visits are counted (areas flip to
VISITED / EXPECTED_VISIT_REACHED). Connect-side only -- it does **not** push to HQ (HQ is
already correct). Idempotent, so it's safe to re-run.

Scope: a pure re-point of *already-assigned* work areas from one user to another. Areas that
are currently unassigned in Connect are left alone (assigning from scratch is a separate
operation), as are terminal states (EXCLUDED/INACCESSIBLE/REQUEST_FOR_INACCESSIBLE).

Usage: set ``OPP_ID`` below (and ``APPLY = True`` to write), then run via the Django shell::

    ./manage.py shell < scripts/reconcile_work_area_owners_from_hq.py

Run once with ``APPLY = False`` and review the ``old -> new: count`` summary before setting
``APPLY = True`` and re-running.
"""

from collections import Counter

import pghistory
from django.db import transaction

from commcare_connect.commcarehq.api import get_case_list
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.users.models import ConnectIDUserLink

OPP_ID = ""  # required — set the Connect Opportunity PK before running
APPLY = False  # set to True to persist changes

# States we won't silently reassign.
SKIP_STATUSES = {
    WorkAreaStatus.EXCLUDED,
    WorkAreaStatus.INACCESSIBLE,
    WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
}

opp = Opportunity.objects.select_related("api_key__hq_server", "deliver_app").get(id=OPP_ID)
domain = opp.deliver_app.cc_domain

# HQ owner keyed by case_id -- the canonical, unique WorkArea<->HQ case link (same key the
# form receiver uses). Open cases only: the API returns closed cases too, and a closed case's
# owner is historical, so we don't reconcile against it. One request (limit == HQ's max).
owner_by_case_id = {
    hq_case.case_id: hq_case.owner_id
    for hq_case in get_case_list(opp.api_key, domain, {"case_type": "work-area", "closed": "false", "limit": 5000})
}

# Resolve each real HQ owner_id -> Connect OpportunityAccess on this opp, via the
# owner -> ConnectIDUserLink -> user -> OpportunityAccess chain. An opp has one access per
# worker, so load them all keyed by user, then walk the links to key them by HQ owner.
hq_owner_ids = {owner_id for owner_id in owner_by_case_id.values() if owner_id and owner_id != "-"}
access_by_user = {
    access.user_id: access for access in OpportunityAccess.objects.filter(opportunity=opp).select_related("user")
}
access_by_owner = {
    link.hq_user_uuid: access_by_user[link.user_id]
    for link in ConnectIDUserLink.objects.filter(
        hq_user_uuid__in=hq_owner_ids, domain=domain, hq_server=opp.api_key.hq_server
    )
    if link.user_id in access_by_user
}

# Already-assigned work areas whose HQ owner differs from the current Connect assignee. This
# is a pure re-point (old user -> new user); currently-unassigned areas are left alone --
# assigning from scratch is a different operation, out of scope for this reconciliation.
changes = []
work_areas = (
    WorkArea.objects.filter(opportunity=opp, opportunity_access__isnull=False)
    .exclude(case_id=None)
    .select_related("opportunity_access__user")
)
for wa in work_areas:
    target = access_by_owner.get(owner_by_case_id.get(wa.case_id))
    if target and wa.opportunity_access_id != target.id and wa.status not in SKIP_STATUSES:
        changes.append((wa, target))

print(f"Opportunity {OPP_ID}: {len(changes)} work areas to align:")
for (from_user, to_user), count in Counter(
    (wa.opportunity_access.user.username, target.user.username) for wa, target in changes
).items():
    print(f"  {from_user} -> {to_user}: {count}")

if not APPLY:
    print("DRY RUN — set APPLY = True and re-run to write.")
else:
    with transaction.atomic(), pghistory.context(reason="HQ owner reconciliation"):
        for wa, target in changes:
            wa.opportunity_access = target
        WorkArea.objects.bulk_update([wa for wa, _ in changes], ["opportunity_access"])
        # Recompute status so the new assignee's already-recorded visits are counted.
        # update_status never downgrades (it only advances on visit counts).
        for wa, _ in changes:
            wa.update_status()
    print(f"Applied {len(changes)} changes.")
