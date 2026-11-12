import json

from django.contrib.gis.db.models import Union
from django.contrib.gis.db.models.fields import PointField
from django.contrib.gis.db.models.functions import AsGeoJSON
from django.db.models import (
    CharField,
    Count,
    F,
    FloatField,
    Func,
    Q,
    Value,
)
from django.db.models.functions import Cast
from django.http import Http404, JsonResponse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext
from django.views.decorators.http import require_GET
from vectortiles import VectorLayer
from vectortiles.views import MVTView
from waffle.decorators import waffle_flag

from commcare_connect.flags.flag_names import MICROPLANNING
from commcare_connect.microplanning.const import (
    SEARCH_KIND_FILTERS,
    WORKAREA_MIN_ZOOM,
)
from commcare_connect.microplanning.filters import (
    UserVisitMapFilterSet,
    WorkAreaMapFilterSet,
)
from commcare_connect.microplanning.models import (
    ImplementationArea,
    WorkArea,
    WorkAreaGroup,
)
from commcare_connect.microplanning.utils import work_area_bounds
from commcare_connect.opportunity.models import UserVisit, VisitValidationStatus
from commcare_connect.organization.decorators import (
    opp_admin_access_required,
    opportunity_required,
)

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


class WorkAreaVectorLayer(VectorLayer):
    id = "workareas"
    # Same fields the search box's sidebar detail serves, so a work area reads identically
    # however the sidebar was populated.
    tile_fields = MAP_WORK_AREA_FIELDS
    geom_field = "boundary"
    min_zoom = WORKAREA_MIN_ZOOM

    def __init__(self, *args, opportunity=None, filter_params=None, **kwargs):
        self.opportunity = opportunity
        self.filter_params = filter_params
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        qs = _map_work_areas(self.opportunity)
        return WorkAreaMapFilterSet(self.filter_params, queryset=qs, opportunity=self.opportunity).qs


@method_decorator([opp_admin_access_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class WorkAreaTileView(MVTView):
    layer_classes = [WorkAreaVectorLayer]

    def get_layers(self):
        return [
            WorkAreaVectorLayer(
                opportunity=self.request.opportunity,
                filter_params=self.request.GET,
            )
        ]


class UserVisitVectorLayer(VectorLayer):
    id = "user-visits"
    tile_fields = ("work_area_id", "visit_uuid")
    geom_field = "location_point"
    min_zoom = WORKAREA_MIN_ZOOM

    def __init__(self, *args, opportunity=None, filter_params=None, **kwargs):
        self.opportunity = opportunity
        self.filter_params = filter_params
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        """
        Returns the user visits with location_point annotated.

        The user visit location is assumed to be a string in the format:
        <lat> <lng> <altitude> <accuracy>
        """
        qs = UserVisit.objects.filter(
            opportunity=self.opportunity,
            location__isnull=False,
        ).exclude(location="")
        qs = UserVisitMapFilterSet(self.filter_params, queryset=qs, opportunity=self.opportunity).qs
        return (
            qs.annotate(
                lat=Cast(Func(F("location"), Value(" "), Value(1), function="split_part"), output_field=FloatField()),
                lon=Cast(Func(F("location"), Value(" "), Value(2), function="split_part"), output_field=FloatField()),
            )
            .annotate(
                location_point=Func(
                    Func(F("lon"), F("lat"), function="ST_MakePoint"),
                    Value(4326),
                    function="ST_SetSRID",
                    output_field=PointField(srid=4326),
                ),
                visit_uuid=Cast("user_visit_id", output_field=CharField()),
            )
            .values("location_point", "work_area_id", "visit_uuid")
        )


@method_decorator([opp_admin_access_required, opportunity_required, waffle_flag(MICROPLANNING)], name="dispatch")
class UserVisitTileView(MVTView):
    layer_classes = [UserVisitVectorLayer]

    def get_layers(self):
        return [
            UserVisitVectorLayer(
                opportunity=self.request.opportunity,
                filter_params=self.request.GET,
            )
        ]


@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def workareas_group_geojson(request, org_slug, opp_id):
    # This view aggregates group boundaries for map display.
    # To be removed in https://dimagi.atlassian.net/browse/CCCT-2213 for a better performant alternative

    qs = WorkArea.objects.filter(opportunity_id=request.opportunity.id)

    group_features = [
        {
            "type": "Feature",
            "geometry": json.loads(g["geojson"]),
            "properties": {"group_id": g["group_id"]},
        }
        for g in (
            qs.filter(work_area_group__isnull=False)
            .values(group_id=F("work_area_group__id"))
            .annotate(geojson=AsGeoJSON(Union("boundary")))
        )
    ]
    return JsonResponse({"group_features": group_features})


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def workareas_bounds(request, org_slug, opp_id):
    """Bounding box of the work areas matching the map's current filters, for auto-zoom.

    Work areas reach the map as vector tiles, so the browser never holds their geometry and cannot
    work out where a filtered selection sits. This takes the same query params as
    ``WorkAreaTileView`` and returns the extent of the same queryset, so a fit always frames exactly
    what the tiles are showing.

    ``bounds`` is null when nothing matches — the caller leaves the viewport alone.
    """
    # The tile layer starts from _map_work_areas(), but its annotations are display fields only and
    # no filter reads them, so the un-annotated queryset matches the same rows without the extra
    # joins and GROUP BY those annotations would force.
    queryset = WorkArea.objects.filter(opportunity=request.opportunity)
    filtered = WorkAreaMapFilterSet(request.GET, queryset=queryset, opportunity=request.opportunity).qs
    return JsonResponse({"bounds": work_area_bounds(filtered)})


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def search_options(request, org_slug, opp_id):
    """Options for the map's work area search box, fetched once after the page loads."""
    return JsonResponse({"options": _work_area_search_options(request.opportunity)})


@require_GET
@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def work_area_detail_json(request, org_slug, opp_id, work_area_id):
    """One work area's details, so picking it in the search box fills the sidebar without a
    map click — the searched area is often outside the current viewport."""
    detail = _work_area_detail(request.opportunity, work_area_id)
    if detail is None:
        raise Http404("Work area not found")
    return JsonResponse(detail)


@opp_admin_access_required
@opportunity_required
@waffle_flag(MICROPLANNING)
def implementation_areas_geojson(request, org_slug, opp_id):
    # Implementation Areas render as a standalone visual layer on the progress map. They are
    # independent of Work Areas (Work Area boxes may fall outside these boundaries), so this is
    # kept separate from workareas_group_geojson and does not affect the map's auto-zoom.
    features = [
        {
            "type": "Feature",
            "geometry": json.loads(ia["geojson"]),
            "properties": {"name": ia["name"]},
        }
        for ia in (
            ImplementationArea.objects.filter(opportunity_id=request.opportunity.id)
            .values("name")
            .annotate(geojson=AsGeoJSON("boundary"))
        )
    ]
    return JsonResponse({"implementation_area_features": features})


def _map_work_areas(opportunity):
    """Work areas carrying every field the map shows for one.

    Shared by the vector tile layer and ``_work_area_detail`` so the sidebar renders identically
    whether it was filled from a tile click or from the search box.
    """
    return WorkArea.objects.filter(opportunity=opportunity).annotate(
        group_id=F("work_area_group__id"),
        group_name=F("work_area_group__name"),
        assignee_name=F("opportunity_access__user__name"),
        assignee_phone=F("opportunity_access__user__phone_number"),
        visits_completed=Count("uservisit", filter=Q(uservisit__status=VisitValidationStatus.approved)),
    )


def _work_area_detail(opportunity, work_area_id):
    """One work area's map fields, or None if it isn't in this opportunity."""
    return _map_work_areas(opportunity).filter(id=work_area_id).values(*MAP_WORK_AREA_FIELDS).first()


def _work_area_search_options(opportunity):
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
