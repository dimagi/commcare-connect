from django.db.models import Count, F, Q, QuerySet
from django.utils import formats, timezone
from django.utils.translation import gettext

from commcare_connect.microplanning.const import SEARCH_KIND_FILTERS
from commcare_connect.microplanning.models import (
    ImplementationArea,
    InaccessibilityRequestStatus,
    WorkArea,
    WorkAreaGroup,
    WorkAreaInaccessibilityRequest,
    WorkAreaStatus,
)
from commcare_connect.opportunity.models import BlobMeta, Opportunity, VisitValidationStatus

MAP_WORK_AREA_FIELDS = (
    "id",
    "slug",
    "status",
    "building_count",
    "expected_visit_count",
    "group_id",
    "group_name",
    "assignee_name",
    "assignee_phone",
    "visits_completed",
    "implementation_area_name",
)


def map_work_areas(opportunity: Opportunity) -> QuerySet[WorkArea]:
    """Work areas carrying every field the map shows for one.

    Shared by the vector tile layer and ``work_area_detail`` so the sidebar renders identically
    whether it was filled from a tile click or from the search box.
    """
    return WorkArea.objects.filter(opportunity=opportunity).annotate(
        group_id=F("work_area_group__id"),
        group_name=F("work_area_group__name"),
        assignee_name=F("opportunity_access__user__name"),
        assignee_phone=F("opportunity_access__user__phone_number"),
        visits_completed=Count("uservisit", filter=Q(uservisit__status=VisitValidationStatus.approved)),
    )


def work_area_detail(opportunity: Opportunity, work_area_id: int) -> dict | None:
    """One work area's map fields, or None if it isn't in this opportunity."""
    return map_work_areas(opportunity).filter(id=work_area_id).values(*MAP_WORK_AREA_FIELDS).first()


def work_area_search_options(opportunity: Opportunity) -> list[dict]:
    """Typeahead options for the map's work area search box, across all three searchable types.

    Each option carries a typed token ("wa:42") rather than a bare pk, because the three models
    share an id space once merged into a single dropdown.

    ``type`` is the translated display label; ``kind`` repeats the prefix as a stable key, so the
    badge colouring in work_area_search.js does not depend on the active language.

    ``filter_name`` is the ``WorkAreaMapFilterSet`` filter this option drives, taken from
    ``SEARCH_KIND_FILTERS`` so the map JS does not have to carry its own copy of that mapping.
    """
    sources = [
        (
            "wa",
            gettext("Work Area"),
            WorkArea.objects.filter(opportunity=opportunity).order_by("slug").values_list("id", "slug"),
        ),
        (
            "wag",
            gettext("Work Area Group"),
            WorkAreaGroup.objects.filter(opportunity=opportunity).order_by("name").values_list("id", "name"),
        ),
        (
            "ia",
            gettext("Implementation Area"),
            ImplementationArea.objects.filter(opportunity=opportunity).order_by("name").values_list("id", "name"),
        ),
    ]
    return [
        {
            "value": f"{prefix}:{pk}",
            "label": label,
            "type": type_name,
            "kind": prefix,
            "object_id": pk,
            "filter_name": SEARCH_KIND_FILTERS[prefix],
        }
        for prefix, type_name, rows in sources
        for pk, label in rows
    ]


def pending_inaccessibility_requests(opportunity: Opportunity) -> list[dict]:
    """Work areas awaiting an inaccessibility review, longest outstanding first.

    The review sidebar renders these rows from JSON rather than through template filters, so the
    date and the pending label arrive display-ready. "Pending" counts from the date of visit the
    worker reported, the only date the request carries.
    """
    today = timezone.localdate()
    pending_requests = (
        WorkAreaInaccessibilityRequest.objects.filter(
            status=InaccessibilityRequestStatus.PENDING,
            work_area__opportunity=opportunity,
            work_area__status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
        )
        .order_by("date_of_visit")
        .values(
            "work_area_id",
            "xform_id",
            "date_of_visit",
            "work_area__slug",
            "work_area__work_area_group__name",
            "work_area__implementation_area_name",
            "opportunity_access__user__name",
        )
    )
    photo_blob_ids = _inaccessibility_photo_blob_ids([request["xform_id"] for request in pending_requests])
    return [
        {
            "work_area_id": pending_request["work_area_id"],
            "slug": pending_request["work_area__slug"],
            "group_name": pending_request["work_area__work_area_group__name"],
            "implementation_area_name": pending_request["work_area__implementation_area_name"],
            "flw_name": pending_request["opportunity_access__user__name"],
            "date_of_visit": formats.date_format(pending_request["date_of_visit"]),
            "pending_label": gettext("{days}d pending").format(days=(today - pending_request["date_of_visit"]).days),
            "photo_blob_id": photo_blob_ids.get(pending_request["xform_id"]),
        }
        for pending_request in pending_requests
    ]


def _inaccessibility_photo_blob_ids(xform_ids: list[str]) -> dict[str, str]:
    """One blob id per form that submitted a photo, for the list's thumbnails.

    A form can carry more than one attachment; the sidebar shows a single thumbnail, so the first
    blob found for a form wins.
    """
    blob_ids = {}
    for parent_id, blob_id in BlobMeta.objects.filter(parent_id__in=xform_ids).values_list("parent_id", "blob_id"):
        blob_ids.setdefault(parent_id, blob_id)
    return blob_ids


def denied_inaccessibility_work_area_ids(opportunity: Opportunity) -> list[int]:
    """Work areas whose inaccessibility request was denied and not since re-raised.

    A denial returns the area to Not Visited, which the tiles cannot tell apart from an area nobody
    ever raised a request on — so the key's "Denied" colour has to be driven by these ids.
    """
    return list(
        WorkAreaInaccessibilityRequest.objects.filter(
            status=InaccessibilityRequestStatus.DENIED,
            work_area__opportunity=opportunity,
        )
        .exclude(work_area__status__in=[WorkAreaStatus.REQUEST_FOR_INACCESSIBLE, WorkAreaStatus.INACCESSIBLE])
        .values_list("work_area_id", flat=True)
        .distinct()
    )
