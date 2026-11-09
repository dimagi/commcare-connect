from unittest.mock import patch

import pytest

from commcare_connect.microplanning.const import InaccessibilityReviewAction
from commcare_connect.microplanning.exceptions import InaccessibilityRequestNotPendingError
from commcare_connect.microplanning.models import InaccessibilityRequestStatus, WorkAreaStatus
from commcare_connect.microplanning.services.inaccessibility_requests import review_work_area_inaccessibility_request
from commcare_connect.microplanning.tests.factories import (
    WorkAreaFactory,
    WorkAreaGroupFactory,
    WorkAreaInaccessibilityRequestFactory,
)
from commcare_connect.opportunity.tests.factories import OpportunityAccessFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

HQ_SYNC = "commcare_connect.microplanning.services.inaccessibility_requests.create_or_update_case_by_work_area"
PUSH_NOTIFICATION = "commcare_connect.opportunity.tasks.send_push_notification_task.delay"


@pytest.fixture
def pending_request(opportunity):
    work_area = WorkAreaFactory(
        opportunity=opportunity,
        work_area_group=WorkAreaGroupFactory(opportunity=opportunity),
        opportunity_access=OpportunityAccessFactory(opportunity=opportunity),
        status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE,
    )
    return WorkAreaInaccessibilityRequestFactory(work_area=work_area)


@pytest.mark.django_db
class TestReviewWorkAreaInaccessibilityRequest:
    @pytest.mark.parametrize(
        ("action", "work_area_status", "request_status", "notified"),
        [
            (
                InaccessibilityReviewAction.APPROVE,
                WorkAreaStatus.INACCESSIBLE,
                InaccessibilityRequestStatus.APPROVED,
                0,
            ),
            (InaccessibilityReviewAction.DENY, WorkAreaStatus.NOT_VISITED, InaccessibilityRequestStatus.DENIED, 1),
        ],
    )
    def test_review_updates_both_statuses_and_syncs_to_hq(
        self,
        pending_request,
        action,
        work_area_status,
        request_status,
        notified,
        django_capture_on_commit_callbacks,
    ):
        work_area = pending_request.work_area
        with (
            patch(HQ_SYNC) as hq_sync,
            patch(PUSH_NOTIFICATION) as notify,
            django_capture_on_commit_callbacks(execute=True),
        ):
            new_status = review_work_area_inaccessibility_request(work_area=work_area, action=action)

        assert new_status == work_area_status
        work_area.refresh_from_db()
        pending_request.refresh_from_db()
        assert work_area.status == work_area_status
        assert pending_request.status == request_status
        hq_sync.assert_called_once_with(work_area)
        assert notify.call_count == notified

    def test_area_without_a_pending_request_is_rejected(self, opportunity):
        work_area = WorkAreaFactory(opportunity=opportunity, status=WorkAreaStatus.REQUEST_FOR_INACCESSIBLE)

        with pytest.raises(InaccessibilityRequestNotPendingError):
            review_work_area_inaccessibility_request(work_area=work_area, action=InaccessibilityReviewAction.APPROVE)

    def test_hq_failure_rolls_back_the_review(self, pending_request):
        work_area = pending_request.work_area
        with patch(HQ_SYNC, side_effect=CommCareHQAPIException("HQ down")), pytest.raises(CommCareHQAPIException):
            review_work_area_inaccessibility_request(work_area=work_area, action=InaccessibilityReviewAction.APPROVE)

        work_area.refresh_from_db()
        pending_request.refresh_from_db()
        assert work_area.status == WorkAreaStatus.REQUEST_FOR_INACCESSIBLE
        assert pending_request.status == InaccessibilityRequestStatus.PENDING
