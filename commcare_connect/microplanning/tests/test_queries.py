import datetime
import json

import pytest
from django.utils import formats, timezone, translation

from commcare_connect.microplanning.models import InaccessibilityRequestStatus, WorkArea, WorkAreaStatus
from commcare_connect.microplanning.queries import (
    approved_visit_count,
    assignee_options,
    assignee_work_area_totals,
    denied_inaccessibility_work_area_ids,
    has_assigned_work_areas,
    implementation_area_boundaries,
    pending_inaccessibility_requests,
    work_area_bounds,
    work_area_detail,
    work_area_group_boundaries,
    work_area_search_options,
    work_areas_for_download,
)
from commcare_connect.microplanning.tests.factories import (
    ImplementationAreaFactory,
    WorkAreaFactory,
    WorkAreaGroupFactory,
    WorkAreaInaccessibilityRequestFactory,
    square,
)
from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.opportunity.tests.factories import BlobMetaFactory, OpportunityAccessFactory, UserVisitFactory


@pytest.mark.django_db
class TestWorkAreaSearchOptions:
    def test_includes_all_three_searchable_types(self, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        impl_area = ImplementationAreaFactory(opportunity=opportunity)

        assert work_area_search_options(opportunity) == [
            {
                "value": f"wa:{work_area.id}",
                "label": work_area.slug,
                "type": "Work Area",
                "kind": "wa",
                "object_id": work_area.id,
                "filter_name": "work_area",
            },
            {
                "value": f"wag:{group.id}",
                "label": group.name,
                "type": "Work Area Group",
                "kind": "wag",
                "object_id": group.id,
                "filter_name": "work_area_group",
            },
            {
                "value": f"ia:{impl_area.id}",
                "label": impl_area.name,
                "type": "Implementation Area",
                "kind": "ia",
                "object_id": impl_area.id,
                "filter_name": "implementation_area",
            },
        ]

    def test_kind_is_untranslated_so_styling_can_key_on_it(self, opportunity):
        """``type`` is a translated display string; ``kind`` is the stable key the badge colours
        are chosen by, so it must not change with the active language."""
        WorkAreaFactory(opportunity=opportunity)

        with translation.override("fr"):
            option = work_area_search_options(opportunity)[0]

        assert option["kind"] == "wa"

    def test_excludes_other_opportunities(self, opportunity):
        WorkAreaFactory()
        WorkAreaGroupFactory()
        ImplementationAreaFactory()

        assert work_area_search_options(opportunity) == []

    def test_orders_each_type_by_label(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, slug="zeta-area")
        WorkAreaFactory(opportunity=opportunity, slug="alpha-area")
        WorkAreaGroupFactory(opportunity=opportunity, name="Zeta Group")
        WorkAreaGroupFactory(opportunity=opportunity, name="Alpha Group")

        labels = [option["label"] for option in work_area_search_options(opportunity)]

        assert labels == ["alpha-area", "zeta-area", "Alpha Group", "Zeta Group"]


@pytest.mark.django_db
class TestWorkAreaDetail:
    def test_returns_the_fields_the_map_sidebar_renders(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        group = WorkAreaGroupFactory(opportunity=opportunity)
        impl_area = ImplementationAreaFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(
            opportunity=opportunity,
            opportunity_access=access,
            work_area_group=group,
            implementation_area=impl_area,
            implementation_area_name=impl_area.name,
            status=WorkAreaStatus.NOT_VISITED,
        )

        detail = work_area_detail(opportunity, work_area.id)

        assert detail == {
            "id": work_area.id,
            "slug": work_area.slug,
            "status": WorkAreaStatus.NOT_VISITED,
            "building_count": work_area.building_count,
            "expected_visit_count": work_area.expected_visit_count,
            "group_id": group.id,
            "group_name": group.name,
            "assignee_name": access.user.name,
            "assignee_phone": access.user.phone_number,
            "visits_completed": 0,
            "implementation_area_name": impl_area.name,
        }

    def test_visits_completed_counts_only_approved_visits(self, opportunity):
        access = OpportunityAccessFactory(opportunity=opportunity)
        work_area = WorkAreaFactory(opportunity=opportunity, opportunity_access=access)
        for status in (
            VisitValidationStatus.approved,
            VisitValidationStatus.approved,
            VisitValidationStatus.rejected,
        ):
            UserVisitFactory(opportunity=opportunity, user=access.user, work_area=work_area, status=status)

        assert work_area_detail(opportunity, work_area.id)["visits_completed"] == 2

    def test_returns_none_for_another_opportunitys_work_area(self, opportunity):
        other_work_area = WorkAreaFactory()

        assert work_area_detail(opportunity, other_work_area.id) is None

    def test_returns_none_for_a_missing_work_area(self, opportunity):
        assert work_area_detail(opportunity, 999999) is None


@pytest.mark.django_db
class TestPendingInaccessibilityRequests:
    @pytest.fixture
    def make_request(self, opportunity):
        def _make(
            days_ago,
            request_status=InaccessibilityRequestStatus.PENDING,
            work_area_status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
        ):
            work_area = WorkAreaFactory(
                opportunity=opportunity,
                status=work_area_status,
                work_area_group=WorkAreaGroupFactory(opportunity=opportunity),
            )
            return WorkAreaInaccessibilityRequestFactory(
                work_area=work_area,
                date_of_visit=timezone.localdate() - datetime.timedelta(days=days_ago),
                status=request_status,
            )

        return _make

    def test_longest_outstanding_first(self, opportunity, make_request):
        recent = make_request(2)
        oldest = make_request(9)

        assert pending_inaccessibility_requests(opportunity) == [
            self.expected_row(oldest, days_outstanding=9),
            self.expected_row(recent, days_outstanding=2),
        ]

    def expected_row(self, pending_request, days_outstanding, photo_blob_id=None):
        work_area = pending_request.work_area
        return {
            "work_area_id": pending_request.work_area_id,
            "slug": work_area.slug,
            "group_name": work_area.work_area_group.name,
            "implementation_area_name": work_area.implementation_area_name,
            "flw_name": pending_request.opportunity_access.user.name,
            "date_of_visit": formats.date_format(pending_request.date_of_visit),
            "pending_label": f"{days_outstanding}d pending",
            "photo_blob_id": photo_blob_id,
        }

    @pytest.mark.parametrize(
        ("request_status", "work_area_status"),
        [
            (InaccessibilityRequestStatus.APPROVED, WorkAreaStatus.INACCESSIBLE),
            (InaccessibilityRequestStatus.DENIED, WorkAreaStatus.NOT_VISITED),
            (InaccessibilityRequestStatus.PENDING, WorkAreaStatus.NOT_VISITED),
        ],
        ids=["approved", "denied", "pending_on_reverted_area"],
    )
    def test_excludes_requests_no_longer_awaiting_review(
        self, opportunity, make_request, request_status, work_area_status
    ):
        make_request(3, request_status=request_status, work_area_status=work_area_status)

        assert pending_inaccessibility_requests(opportunity) == []

    def test_scoped_to_the_opportunity(self, opportunity, make_request):
        own_request = make_request(1)
        WorkAreaInaccessibilityRequestFactory(
            work_area=WorkAreaFactory(status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE)
        )

        result = pending_inaccessibility_requests(opportunity)

        assert [pending["work_area_id"] for pending in result] == [own_request.work_area_id]

    def test_row_carries_the_forms_photo(self, opportunity, make_request):
        pending_request = make_request(4)
        photo = BlobMetaFactory(parent_id=pending_request.xform_id)
        BlobMetaFactory(parent_id="another-form")

        assert pending_inaccessibility_requests(opportunity) == [
            self.expected_row(pending_request, days_outstanding=4, photo_blob_id=photo.blob_id)
        ]


@pytest.mark.django_db
class TestDeniedInaccessibilityWorkAreaIds:
    @pytest.mark.parametrize(
        ("work_area_status", "still_denied"),
        [
            (WorkAreaStatus.NOT_VISITED, True),
            (WorkAreaStatus.VISITED, True),
            (WorkAreaStatus.REQUEST_FOR_INACCESSIBLE, False),
            (WorkAreaStatus.INACCESSIBLE, False),
        ],
        ids=["not_visited", "visited", "re_requested", "approved_since"],
    )
    def test_excludes_areas_that_moved_on_from_the_denial(self, opportunity, work_area_status, still_denied):
        work_area = WorkAreaFactory(opportunity=opportunity, status=work_area_status)
        WorkAreaInaccessibilityRequestFactory(work_area=work_area, status=InaccessibilityRequestStatus.DENIED)

        result = denied_inaccessibility_work_area_ids(opportunity)

        assert result == ([work_area.id] if still_denied else [])

    def test_lists_an_area_denied_more_than_once_only_once(self, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)
        WorkAreaInaccessibilityRequestFactory.create_batch(
            2, work_area=work_area, status=InaccessibilityRequestStatus.DENIED
        )

        assert denied_inaccessibility_work_area_ids(opportunity) == [work_area.id]


@pytest.mark.django_db
class TestWorkAreaBounds:
    def test_covers_every_work_area(self, opportunity):
        WorkAreaFactory(opportunity=opportunity, boundary=square(1, 2))
        WorkAreaFactory(opportunity=opportunity, boundary=square(5, 7))

        assert work_area_bounds(WorkArea.objects.filter(opportunity=opportunity)) == (1.0, 2.0, 6.0, 8.0)

    def test_is_none_when_empty(self, opportunity):
        assert work_area_bounds(WorkArea.objects.filter(opportunity=opportunity)) is None


@pytest.mark.django_db
def test_work_areas_for_download_skips_excluded_and_carries_group_name(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity)
    grouped = WorkAreaFactory(opportunity=opportunity, work_area_group=group)
    ungrouped = WorkAreaFactory(opportunity=opportunity)
    WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.EXCLUDED)
    WorkAreaFactory()

    rows = {work_area.id: work_area.group_name for work_area in work_areas_for_download(opportunity)}

    assert rows == {grouped.id: group.name, ungrouped.id: None}


@pytest.mark.django_db
def test_work_area_group_boundaries_unions_each_groups_work_areas(opportunity):
    group = WorkAreaGroupFactory(opportunity=opportunity)
    WorkAreaFactory(opportunity=opportunity, work_area_group=group, boundary=square(0, 0))
    WorkAreaFactory(opportunity=opportunity, work_area_group=group, boundary=square(1, 0))
    WorkAreaFactory(opportunity=opportunity)

    [row] = work_area_group_boundaries(opportunity)

    assert row["group_id"] == group.id
    geometry = json.loads(row["geojson"])
    assert geometry["type"] == "Polygon"
    xs = [x for x, _ in geometry["coordinates"][0]]
    assert (min(xs), max(xs)) == (0, 2)


@pytest.mark.django_db
def test_implementation_area_boundaries_lists_the_opportunitys_areas(opportunity):
    area = ImplementationAreaFactory(opportunity=opportunity)
    ImplementationAreaFactory()

    [row] = implementation_area_boundaries(opportunity)

    assert row["name"] == area.name
    assert json.loads(row["geojson"])["type"] == "Polygon"


@pytest.mark.django_db
def test_assignee_options_lists_only_active_workers(opportunity):
    active = OpportunityAccessFactory(opportunity=opportunity, accepted=True)
    OpportunityAccessFactory(opportunity=opportunity, accepted=False)
    OpportunityAccessFactory(opportunity=opportunity, accepted=True, suspended=True)

    assert assignee_options(opportunity) == [
        {
            "id": active.id,
            "user__name": active.user.name,
            "user__username": active.user.username,
            "user__user_id": active.user.user_id,
        }
    ]


@pytest.mark.django_db
def test_assignee_work_area_totals(opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    WorkAreaFactory(opportunity=opportunity, opportunity_access=access, building_count=3, expected_visit_count=4)
    WorkAreaFactory(opportunity=opportunity, opportunity_access=access, building_count=5, expected_visit_count=6)
    WorkAreaFactory(opportunity=opportunity)

    assert assignee_work_area_totals(opportunity, access.id) == {"buildings": 8, "visits": 10, "work_areas": 2}


@pytest.mark.django_db
def test_approved_visit_count_counts_only_approved_visits(opportunity):
    work_area = WorkAreaFactory(opportunity=opportunity)
    for status in (VisitValidationStatus.approved, VisitValidationStatus.rejected):
        UserVisitFactory(opportunity=opportunity, work_area=work_area, status=status)

    assert approved_visit_count(work_area) == 1


@pytest.mark.django_db
def test_has_assigned_work_areas(opportunity):
    WorkAreaFactory(opportunity=opportunity)
    assert not has_assigned_work_areas(opportunity)

    WorkAreaFactory(opportunity=opportunity, opportunity_access=OpportunityAccessFactory(opportunity=opportunity))
    assert has_assigned_work_areas(opportunity)
