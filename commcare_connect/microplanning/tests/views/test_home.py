import datetime

import pytest
from django.utils import formats, timezone

from commcare_connect.microplanning.models import InaccessibilityRequestStatus, WorkAreaStatus
from commcare_connect.microplanning.tests.factories import (
    WorkAreaFactory,
    WorkAreaGroupFactory,
    WorkAreaInaccessibilityRequestFactory,
)
from commcare_connect.microplanning.views.home import (
    _denied_inaccessibility_work_area_ids,
    _pending_inaccessibility_requests,
)
from commcare_connect.opportunity.tests.factories import BlobMetaFactory


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

        assert _pending_inaccessibility_requests(opportunity) == [
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

        assert _pending_inaccessibility_requests(opportunity) == []

    def test_scoped_to_the_opportunity(self, opportunity, make_request):
        own_request = make_request(1)
        WorkAreaInaccessibilityRequestFactory(
            work_area=WorkAreaFactory(status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE)
        )

        result = _pending_inaccessibility_requests(opportunity)

        assert [pending["work_area_id"] for pending in result] == [own_request.work_area_id]

    def test_row_carries_the_forms_photo(self, opportunity, make_request):
        pending_request = make_request(4)
        photo = BlobMetaFactory(parent_id=pending_request.xform_id)
        BlobMetaFactory(parent_id="another-form")

        assert _pending_inaccessibility_requests(opportunity) == [
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

        result = _denied_inaccessibility_work_area_ids(opportunity)

        assert result == ([work_area.id] if still_denied else [])

    def test_lists_an_area_denied_more_than_once_only_once(self, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.NOT_VISITED)
        WorkAreaInaccessibilityRequestFactory.create_batch(
            2, work_area=work_area, status=InaccessibilityRequestStatus.DENIED
        )

        assert _denied_inaccessibility_work_area_ids(opportunity) == [work_area.id]
