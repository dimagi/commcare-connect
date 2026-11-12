import logging

import pghistory
from django.db import transaction

from commcare_connect.commcarehq.api import (
    CommCareCase,
    bulk_create_or_update_cases,
    create_or_update_case,
    resolve_hq_user_uuid,
)
from commcare_connect.microplanning.const import HQ_ASSIGN_BULK_CHUNK_SIZE, HQ_UNASSIGN_BULK_CHUNK_SIZE
from commcare_connect.microplanning.models import WorkArea, WorkAreaInaccessibilityRequest, WorkAreaStatus
from commcare_connect.microplanning.serializers import WorkAreaCaseSerializer
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException
from commcare_connect.utils.itertools import batched

logger = logging.getLogger(__name__)


def assign_work_areas_and_sync_to_hq(opportunity, work_areas, user):
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


def _bulk_unassign(work_areas):
    for wa in work_areas:
        wa.opportunity_access = None
        wa.status = WorkAreaStatus.UNASSIGNED
    WorkArea.objects.bulk_update(work_areas, fields=["opportunity_access", "status"])


def create_or_update_case_by_work_area(work_area: WorkArea) -> CommCareCase:
    if not work_area.opportunity_access:
        raise ValueError("Work Area must have an assigned Opportunity Access")

    opp_access = work_area.opportunity_access
    api_key = opp_access.opportunity.api_key
    domain = opp_access.opportunity.deliver_app.cc_domain
    user = opp_access.user
    case_data = WorkAreaCaseSerializer(work_area).data
    case_data["owner_id"] = resolve_hq_user_uuid(user, domain, api_key)

    with transaction.atomic():
        # Re-fetch with a row-level lock to prevent a race condition where two
        # concurrent calls both see case_id as None
        locked_work_area = WorkArea.objects.select_for_update().get(pk=work_area.pk)
        case = create_or_update_case(api_key, domain, case_data, case_id=locked_work_area.case_id)
        if locked_work_area.case_id is None:
            locked_work_area.case_id = case.case_id
            locked_work_area.save(update_fields=["case_id"])
    return case


def bulk_create_or_update_cases_by_work_areas(
    work_areas: list[WorkArea], opportunity: Opportunity
) -> list[CommCareCase]:
    """Sync a batch of work areas to HQ in a single UPSERT call keyed on external_id."""
    if not work_areas:
        return []

    api_key = opportunity.api_key
    domain = opportunity.deliver_app.cc_domain

    wa_by_username: dict[str, WorkArea] = {wa.opportunity_access.user.username.lower(): wa for wa in work_areas}
    owner_id_by_username: dict[str, str] = {
        username: resolve_hq_user_uuid(wa.opportunity_access.user, domain, api_key)
        for username, wa in wa_by_username.items()
    }

    cases_data = []
    for wa in work_areas:
        case_data = dict(WorkAreaCaseSerializer(wa).data)
        case_data["owner_id"] = owner_id_by_username[wa.opportunity_access.user.username.lower()]
        cases_data.append(case_data)

    # UPSERT: HQ decides create vs update via external_id
    cases = bulk_create_or_update_cases(api_key, domain, cases_data, create=None)

    wa_by_id = {str(wa.pk): wa for wa in work_areas if wa.case_id is None}
    newly_created = []
    for case in cases:
        if case.external_id in wa_by_id:
            wa = wa_by_id[case.external_id]
            wa.case_id = case.case_id
            newly_created.append(wa)
    if newly_created:
        WorkArea.objects.bulk_update(newly_created, ["case_id"])

    return cases


def request_work_area_inaccessibility(
    *, work_area, opportunity_access, xform_id, date_of_visit, location, reason, additional_details, user
):
    """Record a worker's request to mark a work area inaccessible, and set the area to await review."""
    WorkAreaInaccessibilityRequest.objects.create(
        work_area=work_area,
        opportunity_access=opportunity_access,
        xform_id=xform_id,
        date_of_visit=date_of_visit,
        location=location,
        reason=reason,
        additional_details=additional_details,
    )
    work_area.status = WorkAreaStatus.REQUEST_FOR_INACCESSIBLE
    with pghistory.context(username=user.username, user_email=user.email):
        work_area.save(update_fields=["status"])


def update_work_area_statuses(work_areas):
    """Recalculate each work area's status from its visits, e.g. after visits are added or reviewed."""
    for work_area in work_areas:
        work_area.update_status()
