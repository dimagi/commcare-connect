"""
Identify and remediate CompletedWork (and their UserVisit) rows that exceed
the per-worker cap configured on their OpportunityClaimLimit. Caused by the
over_limit status-reset bug fixed in processor.py:280 (PR https://github.com/dimagi/commcare-connect/pull/1151).

The cap (claim_limit.max_visits) is on EARNED PAYMENT UNITS, not on raw
UserVisits. A payment unit can have multiple deliver units (e.g. registration
+ service delivery), so counting visits double-counts in those cases. We count
CompletedWork rows instead — each CW is unique per (access, entity, payment_unit)
and represents one earned payment unit regardless of how many deliver-unit
forms it took to satisfy.

For each over-cap CompletedWork we flip the CW *and all its UserVisits* to
over_limit as a unit. This avoids any partial-flip inconsistency between a
CW and its visits.

Usage:
    Edit OPP_UUID and DRY_RUN below, then:
        ./manage.py shell < scripts/remediate_over_cap_visits.py
"""
from collections import defaultdict
from itertools import islice

from django.db import transaction
from django.db.models import Sum

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    OpportunityAccess,
    OpportunityClaimLimit,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.visit_import import update_payment_accrued_for_user

# ---- knobs --------------------------------------------------------------
OPP_UUID = "<x>"  # set None to scan ALL opportunities
DRY_RUN = True
BATCH_SIZE = 500
# -------------------------------------------------------------------------


def _batched(iterable, n):
    it = iter(iterable)
    while batch := list(islice(it, n)):
        yield batch


def _find_over_cap_completed_works():
    """Yield (claim_limit, [over_cap_cw_id, ...], [over_cap_visit_id, ...])
    for every claim limit whose worker has more active CompletedWork rows than
    the per-payment-unit cap allows.
    """
    qs = OpportunityClaimLimit.objects.select_related(
        "opportunity_claim__opportunity_access__opportunity",
        "payment_unit",
    ).filter(
        opportunity_claim__opportunity_access__opportunity__active=True,
        opportunity_claim__opportunity_access__opportunity__is_test=False,
    )
    if OPP_UUID:
        qs = qs.filter(
            opportunity_claim__opportunity_access__opportunity__opportunity_id=OPP_UUID
        )
    for cl in qs.iterator():
        access = cl.opportunity_claim.opportunity_access
        active_cw_ids = list(
            CompletedWork.objects.filter(
                opportunity_access=access,
                payment_unit=cl.payment_unit,
            )
            .exclude(status=CompletedWorkStatus.over_limit)
            .order_by("date_created")
            .values_list("id", flat=True)
        )
        if len(active_cw_ids) > cl.max_visits:
            over_cap_cw_ids = active_cw_ids[cl.max_visits :]
            # All non-over_limit/trial visits attached to those CWs need flipping too.
            over_cap_visit_ids = list(
                UserVisit.objects.filter(completed_work_id__in=over_cap_cw_ids)
                .exclude(
                    status__in=[
                        VisitValidationStatus.over_limit,
                        VisitValidationStatus.trial,
                    ]
                )
                .values_list("id", flat=True)
            )
            yield cl, over_cap_cw_ids, over_cap_visit_ids


def _accrued_delta_by_access(plan):
    """Return {access_id: {"worker": int, "org": int}} totals across the over-cap
    CWs so the operator can see the projected payment_accrued reduction before
    applying. Both values are >= 0; the corresponding accrued figures will
    *decrease* by these amounts. The "org" total is only meaningful on managed
    opportunities (zero on unmanaged ones)."""
    deltas = defaultdict(lambda: {"worker": 0, "org": 0})
    for cl, cw_ids, _ in plan:
        if not cw_ids:
            continue
        agg = CompletedWork.objects.filter(id__in=cw_ids).aggregate(
            worker=Sum("saved_payment_accrued"),
            org=Sum("saved_org_payment_accrued"),
        )
        access_id = cl.opportunity_claim.opportunity_access_id
        deltas[access_id]["worker"] += agg.get("worker") or 0
        deltas[access_id]["org"] += agg.get("org") or 0
    return deltas


def main():
    plan = list(_find_over_cap_completed_works())

    if not plan:
        print("Nothing to remediate.")
        return

    total_cws = sum(len(cw_ids) for _, cw_ids, _ in plan)
    total_visits = sum(len(visit_ids) for _, _, visit_ids in plan)
    print(
        f"Found {total_cws} over-cap CompletedWork rows "
        f"({total_visits} associated UserVisit rows) across {len(plan)} claim limits."
    )

    accrued_deltas = _accrued_delta_by_access(plan)
    total_worker_drop = sum(d["worker"] for d in accrued_deltas.values())
    total_org_drop = sum(d["org"] for d in accrued_deltas.values())

    if DRY_RUN:
        print("\n--- DRY RUN — printing breakdown ---")
        per_access = defaultdict(lambda: {"cws": 0, "visits": 0})
        for cl, cw_ids, visit_ids in plan:
            access_id = cl.opportunity_claim.opportunity_access_id
            per_access[access_id]["cws"] += len(cw_ids)
            per_access[access_id]["visits"] += len(visit_ids)
        for access_id, counts in sorted(per_access.items()):
            delta = accrued_deltas.get(access_id, {"worker": 0, "org": 0})
            print(
                f"  access {access_id}: {counts['cws']} CW(s), "
                f"{counts['visits']} visit(s) -> over_limit; "
                f"worker payment_accrued -{delta['worker']}, "
                f"org payment_accrued -{delta['org']}"
            )
        print(
            f"\nProjected total reduction across all affected accesses: "
            f"worker -{total_worker_drop}, org -{total_org_drop}"
        )
        print("Re-run with DRY_RUN = False to apply.")
        return

    affected_access_ids = set()
    affected_visit_ids = [vid for _, _, visit_ids in plan for vid in visit_ids]
    affected_cw_ids = [cw_id for _, cw_ids, _ in plan for cw_id in cw_ids]

    with transaction.atomic():
        # bulk_update persists status_modified_date because __setattr__ stamps
        # it in memory when status is assigned, and we list both fields below.
        for batch in _batched(affected_visit_ids, BATCH_SIZE):
            visits = list(UserVisit.objects.select_for_update().filter(id__in=batch))
            for v in visits:
                v.status = VisitValidationStatus.over_limit
                affected_access_ids.add(v.opportunity_access_id)
            UserVisit.objects.bulk_update(visits, fields=["status", "status_modified_date"])

        for batch in _batched(affected_cw_ids, BATCH_SIZE):
            cws = list(CompletedWork.objects.select_for_update().filter(id__in=batch))
            for cw in cws:
                cw.status = CompletedWorkStatus.over_limit
            CompletedWork.objects.bulk_update(cws, fields=["status", "status_modified_date"])

    # Recompute payment_accrued for each affected access (full recompute, not incremental).
    # Outside the atomic block because update_payment_accrued_for_user acquires its own
    # Redis lock per access.
    for access_id in sorted(affected_access_ids):
        access = OpportunityAccess.objects.get(id=access_id)
        update_payment_accrued_for_user(access, incremental=False)

    print(
        f"Done. Flipped {len(affected_visit_ids)} UserVisit rows and "
        f"{len(affected_cw_ids)} CompletedWork rows. "
        f"Recomputed payment_accrued for {len(affected_access_ids)} accesses. "
        f"Projected reduction: worker -{total_worker_drop}, org -{total_org_drop}."
    )
