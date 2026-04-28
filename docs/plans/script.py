"""
Identify and remediate UserVisit rows that exceed the per-worker cap configured
on their OpportunityClaimLimit. Caused by the over_limit status-reset bug fixed
in processor.py:280 (commit <fix-sha>).

Usage:
    Edit OPP_UUID and DRY_RUN below, then:
        ./manage.py shell < scripts/remediate_over_cap_visits.py
"""
from collections import defaultdict
from itertools import islice

from django.db import transaction

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


def _find_over_cap_visit_ids():
    """Yield (claim_limit, [over_cap_visit_id, ...]) for every claim limit with extras."""
    qs = OpportunityClaimLimit.objects.select_related(
        "opportunity_claim__opportunity_access",
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
        active_visit_ids = list(
            UserVisit.objects.filter(
                opportunity_access=access,
                deliver_unit__payment_unit=cl.payment_unit,
            )
            .exclude(
                status__in=[
                    VisitValidationStatus.over_limit,
                    VisitValidationStatus.trial,
                ]
            )
            .order_by("date_created")
            .values_list("id", flat=True)
        )
        if len(active_visit_ids) > cl.max_visits:
            yield cl, active_visit_ids[cl.max_visits :]


def _completed_works_safe_to_flip(over_cap_visit_ids):
    """Return CompletedWork ids whose every linked visit is in the over-cap set.

    A CompletedWork that has at least one visit OUTSIDE the over-cap set is left
    alone and reported as a warning — those need manual review.
    """
    over_cap_set = set(over_cap_visit_ids)
    cw_ids = (
        UserVisit.objects.filter(id__in=over_cap_set)
        .exclude(completed_work__isnull=True)
        .values_list("completed_work_id", flat=True)
        .distinct()
    )
    safe, mixed = [], []
    for cw_id in cw_ids:
        all_visit_ids = set(
            UserVisit.objects.filter(completed_work_id=cw_id).values_list("id", flat=True)
        )
        if all_visit_ids.issubset(over_cap_set):
            safe.append(cw_id)
        else:
            mixed.append(cw_id)
    return safe, mixed


def main():
    plan = []  # list of (claim_limit, over_cap_ids)
    for cl, over_cap_ids in _find_over_cap_visit_ids():
        plan.append((cl, over_cap_ids))

    if not plan:
        print("Nothing to remediate.")
        return

    total = sum(len(ids) for _, ids in plan)
    print(f"Found {total} over-cap UserVisit rows across {len(plan)} claim limits.")
    affected_visit_ids = [vid for _, ids in plan for vid in ids]
    safe_cw_ids, mixed_cw_ids = _completed_works_safe_to_flip(affected_visit_ids)
    print(f"  CompletedWork rows to flip: {len(safe_cw_ids)}")
    if mixed_cw_ids:
        print(
            f"  WARNING: {len(mixed_cw_ids)} CompletedWork rows are linked to BOTH "
            f"in-cap and over-cap visits. They will NOT be flipped automatically. "
            f"Manual review needed: {mixed_cw_ids}"
        )

    if DRY_RUN:
        print("\n--- DRY RUN — printing breakdown ---")
        per_access = defaultdict(int)
        for cl, ids in plan:
            per_access[cl.opportunity_claim.opportunity_access_id] += len(ids)
        for access_id, n in sorted(per_access.items()):
            print(f"  access {access_id}: {n} over-cap visit(s) -> over_limit")
        print("\nRe-run with DRY_RUN = False to apply.")
        return

    affected_access_ids = set()
    with transaction.atomic():
        # Flip UserVisit.status
        for batch in _batched(affected_visit_ids, BATCH_SIZE):
            visits = list(
                UserVisit.objects.select_for_update().filter(id__in=batch)
            )
            for v in visits:
                v.status = VisitValidationStatus.over_limit  # __setattr__ updates status_modified_date
                affected_access_ids.add(v.opportunity_access_id)
            UserVisit.objects.bulk_update(
                visits, fields=["status", "status_modified_date"]
            )

        # Flip CompletedWork.status (only for completed works fully covered by over-cap visits)
        if safe_cw_ids:
            CompletedWork.objects.filter(id__in=safe_cw_ids).update(
                status=CompletedWorkStatus.over_limit
            )

    # Recompute payment_accrued for each affected access (full recompute, not incremental)
    for access_id in sorted(affected_access_ids):
        access = OpportunityAccess.objects.get(id=access_id)
        update_payment_accrued_for_user(access, incremental=False)

    print(
        f"Done. Flipped {len(affected_visit_ids)} UserVisit rows, "
        f"{len(safe_cw_ids)} CompletedWork rows, recomputed payment_accrued for "
        f"{len(affected_access_ids)} accesses."
    )
