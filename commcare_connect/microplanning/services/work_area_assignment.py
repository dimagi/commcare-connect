import logging

import pghistory
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from commcare_connect.commcarehq.api import bulk_create_or_update_cases, bulk_create_or_update_cases_by_work_areas
from commcare_connect.microplanning.const import HQ_ASSIGN_BULK_CHUNK_SIZE, HQ_UNASSIGN_BULK_CHUNK_SIZE
from commcare_connect.microplanning.models import WorkArea, WorkAreaStatus
from commcare_connect.opportunity.models import Opportunity, OpportunityAccess
from commcare_connect.users.models import User
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.itertools import batched

logger = logging.getLogger(__name__)


def assign_work_areas_to_workers(
    opportunity: Opportunity, work_area_ids_by_access_id: dict[int, list[int]], user: User
) -> dict:
    """Assign each worker the listed work areas, sync them to HQ and notify the workers.

    Raises ValidationError when a worker or work area isn't in the opportunity. Returns
    assign_work_areas_and_sync_to_hq's result; a work area whose HQ sync failed keeps its old
    assignment.
    """
    accesses = check_assignees(opportunity=opportunity, access_ids=set(work_area_ids_by_access_id))
    work_area_to_access = {
        work_area_id: accesses[access_id]
        for access_id, work_area_ids in work_area_ids_by_access_id.items()
        for work_area_id in work_area_ids
    }
    requested_ids = set(work_area_to_access)

    work_areas = list(WorkArea.objects.filter(id__in=requested_ids, opportunity=opportunity).select_for_update())

    invalid_ids = requested_ids - {work_area.id for work_area in work_areas}
    if invalid_ids:
        raise ValidationError(_("Invalid work area IDs: %(ids)s") % {"ids": sorted(invalid_ids)})

    for work_area in work_areas:
        work_area.opportunity_access = work_area_to_access[work_area.id]
        if work_area.status == WorkAreaStatus.UNASSIGNED:
            work_area.status = WorkAreaStatus.NOT_VISITED

    result = assign_work_areas_and_sync_to_hq(opportunity, work_areas, user)
    assigned_ids = set(result["assigned_ids"])
    notified_access_ids = {work_area_to_access[wa.id].id for wa in work_areas if wa.id in assigned_ids}
    _notify_workers_of_assignment(notified_access_ids)
    return result


def check_assignees(opportunity: Opportunity, access_ids: set[int]) -> dict[int, OpportunityAccess]:
    """The opportunity's accesses with these ids, by id. Raises ValidationError if any id isn't one."""
    accesses = {
        access.id: access
        for access in OpportunityAccess.objects.filter(id__in=access_ids, opportunity=opportunity).select_related(
            "user"
        )
    }
    invalid_ids = access_ids - accesses.keys()
    if invalid_ids:
        raise ValidationError(_("Invalid assignee IDs: %(ids)s") % {"ids": sorted(invalid_ids)})
    return accesses


def _notify_workers_of_assignment(access_ids: set[int]) -> None:
    from commcare_connect.microplanning.tasks import send_work_area_assignment_notification

    for access_id in access_ids:
        transaction.on_commit(lambda aid=access_id: send_work_area_assignment_notification.delay(aid))


def assign_work_areas_and_sync_to_hq(opportunity: Opportunity, work_areas: list[WorkArea], user: User) -> dict:
    """Persist work-area assignments and push the new owners to HQ, one HQ POST per chunk in
    its own savepoint.

    Each ``work_area`` must already have its target ``opportunity_access``/``status`` set in
    memory.
    """
    assigned_ids = []
    failed_ids = []
    pghistory_ctx = dict(reason="assigned", username=user.username, user_email=user.email)

    for batch in batched(work_areas, HQ_ASSIGN_BULK_CHUNK_SIZE):
        chunk = list(batch)
        try:
            with transaction.atomic(), pghistory.context(**pghistory_ctx):
                WorkArea.objects.bulk_update(chunk, ["opportunity_access", "status"])
                bulk_create_or_update_cases_by_work_areas(chunk, opportunity)
        except CommCareHQAPIException as e:
            logger.warning(
                "Failed to sync work area assignment chunk to HQ opp=%s size=%d: %s", opportunity.id, len(chunk), e
            )
            failed_ids.extend(wa.id for wa in chunk)
            continue
        assigned_ids.extend(wa.id for wa in chunk)
    return {"assigned_ids": assigned_ids, "failed_ids": failed_ids}


def unassign_work_areas_for_opportunity(opportunity: Opportunity, work_area_ids: list[int], user: User) -> dict:
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
        updates = [{"case_id": str(wa.case_id), "owner_id": "-"} for wa in chunk]
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


def _bulk_unassign(work_areas: list[WorkArea]) -> None:
    for wa in work_areas:
        wa.opportunity_access = None
        wa.status = WorkAreaStatus.UNASSIGNED
    WorkArea.objects.bulk_update(work_areas, fields=["opportunity_access", "status"])
