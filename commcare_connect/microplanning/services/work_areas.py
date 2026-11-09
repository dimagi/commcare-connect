import logging

import pghistory
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from commcare_connect.commcarehq.api import bulk_create_or_update_cases, create_or_update_case_by_work_area
from commcare_connect.microplanning.const import HQ_BULK_CHUNK_SIZE
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup, WorkAreaStatus
from commcare_connect.opportunity.models import Opportunity
from commcare_connect.users.models import User
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

logger = logging.getLogger(__name__)


def exclude_work_areas_for_opportunity(
    opportunity: Opportunity, work_area_ids: list[int], user: User, exclusion_reason: str
) -> dict:
    """Exclude work areas and unassign their HQ cases.
    HQ calls are batched by HQ_BULK_CHUNK_SIZE; a batch failure skips DB exclusion
    for the whole chunk.
    """
    excluded_ids = []
    skipped = 0
    failed = 0

    work_areas_map = {wa.id: wa for wa in WorkArea.objects.filter(id__in=work_area_ids, opportunity=opportunity)}

    wa_to_wag_id_map = {wa.id: wa.work_area_group_id for wa in work_areas_map.values()}

    api_key = opportunity.api_key
    domain = opportunity.deliver_app.cc_domain if opportunity.deliver_app else None

    needs_hq = []
    db_only = []

    for work_area_id in work_area_ids:
        work_area = work_areas_map.get(work_area_id)
        if work_area is None or work_area.status not in (WorkAreaStatus.NOT_VISITED, WorkAreaStatus.UNASSIGNED):
            skipped += 1
            continue

        if work_area.case_id and api_key and domain:
            needs_hq.append(work_area)
        else:
            db_only.append(work_area)

    pghistory_ctx = dict(reason=exclusion_reason, username=user.username, user_email=user.email)

    for i in range(0, len(needs_hq), HQ_BULK_CHUNK_SIZE):
        chunk = needs_hq[i : i + HQ_BULK_CHUNK_SIZE]
        # HQ's "unassigned" convention is "-"; empty string falls back to the submitting user.
        updates = [{"case_id": str(wa.case_id), "owner_id": "-"} for wa in chunk]
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

    work_area_group_ids = {wa_to_wag_id_map[wa_id] for wa_id in excluded_ids}
    work_area_groups = list(WorkAreaGroup.objects.filter(id__in=work_area_group_ids))
    for wag in work_area_groups:
        wag.update_centroid(commit=False)
    WorkAreaGroup.objects.bulk_update(work_area_groups, fields=["centroid"])

    logger.info(
        "exclude_work_areas_for_opportunity finished opp=%s requested=%d excluded=%d skipped=%d failed=%d",
        opportunity.id,
        len(work_area_ids),
        len(excluded_ids),
        skipped,
        failed,
    )
    return {"excluded_ids": excluded_ids, "skipped": skipped, "failed": failed}


def _bulk_exclude(work_areas: list[WorkArea], user: User, exclusion_reason: str) -> None:
    for wa in work_areas:
        wa.status = WorkAreaStatus.EXCLUDED
        wa.excluded_by = user
        wa.excluded_reason = exclusion_reason
        wa.work_area_group = None
    WorkArea.objects.bulk_update(
        work_areas,
        fields=["status", "excluded_by", "excluded_reason", "work_area_group"],
    )


def save_work_area_changes(work_area: WorkArea, previous_group_id: int | None, changed_fields: list[str]) -> None:
    """Save an edited work area's visit count and group, then refresh what depends on them.

    Recomputes the status when the visit count changed and the centroids of the groups it joined
    or left, and syncs an assigned area to HQ. An HQ error propagates and rolls back the save.
    """
    group_changed = work_area.work_area_group_id != previous_group_id
    with transaction.atomic():
        work_area.save(update_fields=["expected_visit_count", "work_area_group"])
        if "expected_visit_count" in changed_fields:
            work_area.update_status()

        if group_changed and work_area.work_area_group:
            work_area.work_area_group.update_centroid()

        if changed_fields and work_area.opportunity_access_id:
            create_or_update_case_by_work_area(work_area)

    if group_changed and previous_group_id:
        WorkAreaGroup.objects.get(id=previous_group_id).update_centroid()


def clear_work_areas_for_opportunity(opportunity: Opportunity) -> None:
    """Delete the opportunity's work areas and work area groups so a new file can be uploaded.

    Raises ValidationError while any work area is assigned or has visits.
    """
    work_areas = WorkArea.objects.filter(opportunity_id=opportunity.id)

    access_ids = list(work_areas.select_for_update().values_list("opportunity_access_id", flat=True))
    if any(access_id is not None for access_id in access_ids):
        raise ValidationError(_("Work Areas cannot be cleared as they are assigned to users."))

    if work_areas.filter(uservisit__isnull=False).exists():
        raise ValidationError(_("Visits have been recorded against these Work Areas. They cannot be cleared."))

    work_areas.delete()
    WorkAreaGroup.objects.filter(opportunity_id=opportunity.id).delete()
