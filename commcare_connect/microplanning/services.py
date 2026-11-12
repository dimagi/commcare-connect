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
from commcare_connect.microplanning.models import (
    WorkArea,
    WorkAreaGroup,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)
from commcare_connect.microplanning.serializers import WorkAreaCaseSerializer
from commcare_connect.microplanning.tasks import (
    ImplementationAreaCSVImporter,
    WorkAreaCSVImporter,
    send_work_area_assignment_notification,
)
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


def bulk_update_work_areas(opportunity, updates, user):
    """Apply field changes, assignments and unassignments to many work areas at once.

    ``updates`` holds one dict per work area: ``id`` is the WorkArea, the other keys are the fields to
    set. An ``opportunity_access`` key assigns the area, or unassigns it when the value is None.
    Returns the work areas and the assign/unassign results (None when nothing was assigned or
    unassigned), whose ``failed_ids`` list the areas HQ did not accept.
    """
    instances = []
    assign_result = None
    unassign_result = None
    touched_fields = set()
    needs_visit_status_update = []
    needs_hq_resync = []
    to_assign = []
    original_access_status = {}
    to_unassign = []
    recompute_group_ids = set()

    for item in updates:
        item = item.copy()
        instance = item.pop("id")
        has_access_key = "opportunity_access" in item
        access = item.pop("opportunity_access", None)
        old_group_id = instance.work_area_group_id

        for field, value in item.items():
            setattr(instance, field, value)
            touched_fields.add(field)

        if "expected_visit_count" in item:
            needs_visit_status_update.append(instance)

        if "boundary" in item and instance.work_area_group_id is not None:
            # Group centroid is derived from member WorkAreas' boundaries, so an edit here
            # needs a recompute even without a group change.
            recompute_group_ids.add(instance.work_area_group_id)

        if instance.work_area_group_id != old_group_id:
            recompute_group_ids.update({instance.work_area_group_id, old_group_id})

        if access is not None:
            # assign_work_areas_and_sync_to_hq expects opportunity_access/status already set.
            original_access_status[instance.id] = (instance.opportunity_access_id, instance.status)
            instance.opportunity_access = access
            if instance.status == WorkAreaStatus.UNASSIGNED:
                instance.status = WorkAreaStatus.NOT_VISITED
            to_assign.append(instance)
        elif has_access_key:
            # Explicit `opportunity_access: null` — unassign rather than silently ignore.
            to_unassign.append(instance)
        elif item and instance.opportunity_access_id:
            needs_hq_resync.append(instance)

        instances.append(instance)

    if touched_fields:
        # Must run before the group-centroid recompute below: update_centroid() re-reads
        # member WorkAreas' boundaries from the DB, so it needs this write already committed.
        WorkArea.objects.bulk_update(instances, fields=list(touched_fields))

    update_work_area_statuses(needs_visit_status_update)

    recompute_group_ids.discard(None)
    if recompute_group_ids:
        groups = list(WorkAreaGroup.objects.filter(pk__in=recompute_group_ids))
        for group in groups:
            group.update_centroid(commit=False)
        WorkAreaGroup.objects.bulk_update(groups, ["centroid"])

    if needs_hq_resync:
        bulk_create_or_update_cases_by_work_areas(needs_hq_resync, opportunity)

    if to_assign:
        result = assign_work_areas_and_sync_to_hq(opportunity, to_assign, user)
        assign_result = result
        failed_ids = set(result["failed_ids"])
        notified_access_ids = set()
        for wa in to_assign:
            if wa.id in failed_ids:
                orig_access_id, orig_status = original_access_status[wa.id]
                wa.opportunity_access_id = orig_access_id
                wa.status = orig_status
            else:
                notified_access_ids.add(wa.opportunity_access_id)
        for access_id in notified_access_ids:
            transaction.on_commit(lambda aid=access_id: send_work_area_assignment_notification.delay(aid))

    if to_unassign:
        result = unassign_work_areas_for_opportunity(opportunity, [wa.id for wa in to_unassign], user)
        unassign_result = result
        unassigned_ids = set(result["unassigned_ids"])
        for wa in to_unassign:
            if wa.id in unassigned_ids:
                wa.opportunity_access = None
                wa.status = WorkAreaStatus.UNASSIGNED

    return {"work_areas": instances, "assign_result": assign_result, "unassign_result": unassign_result}


def create_work_area_group(opportunity, **fields):
    """Create a work area group in ``opportunity``."""
    return WorkAreaGroup.objects.create(opportunity=opportunity, **fields)


def update_work_area_group(work_area_group, **fields):
    """Set ``fields`` on a work area group and save it."""
    for field, value in fields.items():
        setattr(work_area_group, field, value)
    work_area_group.save()
    return work_area_group


def import_work_areas_from_csv(opportunity_id, csv_source):
    """Create work areas from CSV rows. Returns the importer's result: counts, or ``errors``."""
    return WorkAreaCSVImporter(opportunity_id, csv_source).run()


def import_implementation_areas_from_csv(opportunity_id, csv_source):
    """Create implementation areas from CSV rows. Returns the importer's result: counts, or ``errors``."""
    return ImplementationAreaCSVImporter(opportunity_id, csv_source).run()
