"""
Remediate the small set of UserVisits (and their CompletedWorks) that were
left as approved when they should have been over_limit, due to the bug
fixed in processor.py (PR https://github.com/dimagi/commcare-connect/pull/1151).
"""
from django.db import transaction
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tasks import bulk_update_payment_accrued

XFORM_IDS = []

visit_rows = list(
    UserVisit.objects.filter(xform_id__in=XFORM_IDS).values(
        "user_id", "completed_work_id", "opportunity_id"
    )
)
if not visit_rows:
    print("No matching visits. Check XFORM_IDS.")
else:
    user_ids = list({v["user_id"] for v in visit_rows})
    cw_ids = [v["completed_work_id"] for v in visit_rows if v["completed_work_id"]]
    opp_id = visit_rows[0]["opportunity_id"]

    with transaction.atomic():
        UserVisit.objects.filter(xform_id__in=XFORM_IDS).update(
            status=VisitValidationStatus.over_limit,
            status_modified_date=now(),
        )
        CompletedWork.objects.filter(id__in=cw_ids).update(
            status=CompletedWorkStatus.over_limit,
            status_modified_date=now(),
        )
    bulk_update_payment_accrued.delay(opp_id, user_ids)
    print(
        f"Flipped {len(visit_rows)} UserVisits and {len(cw_ids)} CompletedWorks; "
        f"recompute queued for {len(user_ids)} worker(s)."
    )
