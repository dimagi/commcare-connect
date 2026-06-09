import logging

import pghistory
from django.db import transaction

from commcare_connect.commcarehq.api import bulk_create_or_update_cases
from commcare_connect.microplanning.const import HQ_BULK_CHUNK_SIZE, HQ_UNASSIGN_BULK_CHUNK_SIZE
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.itertools import batched

logger = logging.getLogger(__name__)


def exclude_work_areas_for_opportunity(opportunity, work_area_ids, user, exclusion_reason):
    """Exclude work areas and unassign their HQ cases.
    HQ calls are batched by HQ_BULK_CHUNK_SIZE; a batch failure skips DB exclusion
    for the whole chunk.
    """
    excluded_ids = []
    skipped = 0
    failed = 0

    work_areas_map = {wa.id: wa for wa in WorkArea.objects.filter(id__in=work_area_ids, opportunity=opportunity)}

    api_key = opportunity.api_key
    domain = opportunity.deliver_app.cc_domain if opportunity.deliver_app else None

    needs_hq = []
    db_only = []

    for work_area_id in work_area_ids:
        work_area = work_areas_map.get(work_area_id)
        if work_area is None or work_area.status != WorkAreaStatus.NOT_VISITED:
            skipped += 1
            continue

        if work_area.case_id and api_key and domain:
            needs_hq.append(work_area)
        else:
            db_only.append(work_area)

    pghistory_ctx = dict(reason=exclusion_reason, username=user.username, user_email=user.email)

    for i in range(0, len(needs_hq), HQ_BULK_CHUNK_SIZE):
        chunk = needs_hq[i : i + HQ_BULK_CHUNK_SIZE]  # noqa: E203
        # HQ's "unassigned" convention is "-"; empty string falls back to the submitting user.
        updates = [{"case_id": str(wa.case_id), "owner_id": "-", "create": False} for wa in chunk]
        try:
            with transaction.atomic(), pghistory.context(**pghistory_ctx):
                _bulk_exclude(chunk, user, exclusion_reason)
                bulk_create_or_update_cases(api_key, domain, updates)
        except CommCareHQAPIException as e:
            logger.warning("Failed to unassign HQ case chunk (size=%d): %s", len(chunk), e)
            failed += len(chunk)
            continue
        excluded_ids.extend(wa.id for wa in chunk)

    if db_only:
        with transaction.atomic(), pghistory.context(**pghistory_ctx):
            _bulk_exclude(db_only, user, exclusion_reason)
        excluded_ids.extend(wa.id for wa in db_only)

    logger.info(
        "exclude_work_areas_for_opportunity finished opp=%s requested=%d excluded=%d skipped=%d failed=%d",
        opportunity.id,
        len(work_area_ids),
        len(excluded_ids),
        skipped,
        failed,
    )
    return {"excluded_ids": excluded_ids, "skipped": skipped, "failed": failed}


def _bulk_exclude(work_areas, user, exclusion_reason):
    for wa in work_areas:
        wa.status = WorkAreaStatus.EXCLUDED
        wa.excluded_by = user
        wa.excluded_reason = exclusion_reason
        wa.work_area_group = None
    WorkArea.objects.bulk_update(
        work_areas,
        fields=["status", "excluded_by", "excluded_reason", "work_area_group"],
    )


def unassign_work_areas_for_opportunity(opportunity, work_area_ids, user):
    """Unassign work areas and set their HQ case owner_id to '-'.
    HQ calls are batched by HQ_UNASSIGN_BULK_CHUNK_SIZE and each batch commits in its own
    transaction, so a batch failure rolls back only that batch (its IDs are returned in
    `failed_ids`) while the other batches still succeed.
    """
    unassigned_ids = []
    failed_ids = []
    skipped = 0

    # Dedupe while preserving order so we don't process or HQ-update the same work area twice.
    unique_work_area_ids = list(dict.fromkeys(work_area_ids))
    work_areas_map = {
        wa.id: wa for wa in WorkArea.objects.filter(id__in=unique_work_area_ids, opportunity=opportunity)
    }

    api_key = opportunity.api_key
    domain = opportunity.deliver_app.cc_domain if opportunity.deliver_app else None

    needs_hq = []
    db_only = []

    for work_area_id in unique_work_area_ids:
        work_area = work_areas_map.get(work_area_id)
        # Only assigned, not-yet-visited areas can be unassigned; anything that's progressed
        # (VISITED, INACCESSIBLE, …) or is already unassigned/excluded is skipped.
        if (
            work_area is None
            or work_area.opportunity_access_id is None
            or work_area.status != WorkAreaStatus.NOT_VISITED
        ):
            skipped += 1
            continue

        if work_area.case_id:
            needs_hq.append(work_area)
        else:
            db_only.append(work_area)

    pghistory_ctx = dict(reason="unassigned", username=user.username, user_email=user.email)

    for chunk in batched(needs_hq, HQ_UNASSIGN_BULK_CHUNK_SIZE):
        # HQ's "unassigned" convention is "-"; empty string falls back to the submitting user.
        updates = [{"case_id": str(wa.case_id), "owner_id": "-", "create": False} for wa in chunk]
        try:
            with transaction.atomic(), pghistory.context(**pghistory_ctx):
                _bulk_unassign(chunk)
                bulk_create_or_update_cases(api_key, domain, updates)
        except CommCareHQAPIException as e:
            # Per-batch transaction: a failed HQ batch rolls back only this chunk's DB changes.
            logger.warning("Failed to unassign HQ case chunk (size=%d): %s", len(chunk), e)
            failed_ids.extend(wa.id for wa in chunk)
            continue
        unassigned_ids.extend(wa.id for wa in chunk)

    if db_only:
        with transaction.atomic(), pghistory.context(**pghistory_ctx):
            _bulk_unassign(db_only)
        unassigned_ids.extend(wa.id for wa in db_only)

    logger.info(
        "unassign_work_areas_for_opportunity finished opp=%s requested=%d unassigned=%d skipped=%d failed=%d",
        opportunity.id,
        len(unique_work_area_ids),
        len(unassigned_ids),
        skipped,
        len(failed_ids),
    )
    return {"unassigned_ids": unassigned_ids, "skipped": skipped, "failed_ids": failed_ids}


def _bulk_unassign(work_areas):
    for wa in work_areas:
        wa.opportunity_access = None
        wa.status = WorkAreaStatus.UNASSIGNED
    WorkArea.objects.bulk_update(work_areas, fields=["opportunity_access", "status"])
