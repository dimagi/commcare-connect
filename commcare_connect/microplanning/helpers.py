import logging

import pghistory
from django.db import transaction

from commcare_connect.commcarehq.api import bulk_update_cases
from commcare_connect.microplanning.const import HQ_BULK_CHUNK_SIZE
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

logger = logging.getLogger(__name__)


def exclude_work_areas_for_opportunity(opportunity, work_area_ids, user, exclusion_reason):
    """Exclude work areas and unassign their HQ cases.
    HQ calls are batched by HQ_BULK_CHUNK_SIZE; a batch failure skips DB exclusion
    for the whole chunk.
    """
    excluded = 0
    skipped = 0
    failed = 0

    work_areas_map = {wa.id: wa for wa in WorkArea.objects.filter(id__in=work_area_ids, opportunity=opportunity)}

    api_key = opportunity.api_key
    domain = opportunity.deliver_app.cc_domain if opportunity.deliver_app else None

    needs_hq = []
    db_only = []

    for work_area_id in work_area_ids:
        work_area = work_areas_map.get(work_area_id)
        if work_area is None or work_area.status != WorkAreaStatus.NOT_STARTED:
            skipped += 1
            continue

        if work_area.case_id and api_key and domain:
            needs_hq.append(work_area)
        else:
            db_only.append(work_area)

    for i in range(0, len(needs_hq), HQ_BULK_CHUNK_SIZE):
        chunk = needs_hq[i : i + HQ_BULK_CHUNK_SIZE]  # noqa: E203
        # HQ's "unassigned" convention is "-"; empty string falls back to the submitting user.
        updates = [{"case_id": str(wa.case_id), "owner_id": "-"} for wa in chunk]
        try:
            bulk_update_cases(api_key, domain, updates)
        except CommCareHQAPIException as e:
            logger.warning("Failed to unassign HQ case chunk (size=%d): %s", len(chunk), e)
            failed += len(chunk)
            continue
        _bulk_exclude(chunk, user, exclusion_reason)
        excluded += len(chunk)

    if db_only:
        _bulk_exclude(db_only, user, exclusion_reason)
        excluded += len(db_only)

    logger.info(
        "exclude_work_areas_for_opportunity finished opp=%s requested=%d excluded=%d skipped=%d failed=%d",
        opportunity.id,
        len(work_area_ids),
        excluded,
        skipped,
        failed,
    )
    return {"excluded": excluded, "skipped": skipped, "failed": failed}


def _bulk_exclude(work_areas, user, exclusion_reason):
    for wa in work_areas:
        wa.status = WorkAreaStatus.EXCLUDED
        wa.excluded_by = user
        wa.excluded_reason = exclusion_reason
        wa.work_area_group = None
    with transaction.atomic(), pghistory.context(
        reason=exclusion_reason,
        username=user.username,
        user_email=user.email,
    ):
        WorkArea.objects.bulk_update(
            work_areas,
            fields=["status", "excluded_by", "excluded_reason", "work_area_group"],
        )
