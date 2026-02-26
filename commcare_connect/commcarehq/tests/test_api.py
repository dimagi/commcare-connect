import uuid
from unittest.mock import patch

import pytest

from commcare_connect.commcarehq.api import CommCareCase, create_or_update_case_by_work_area
from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityAccessFactory

DOMAIN = "test-domain"


def make_commcare_case(**kwargs) -> CommCareCase:
    defaults = dict(
        domain=DOMAIN,
        case_id=str(uuid.uuid4()),
        case_type="work-area",
        case_name="area-1",
        external_id=None,
        owner_id=str(uuid.uuid4()),
        date_opened="2024-01-01T00:00:00Z",
        last_modified="2024-01-01T00:00:00Z",
        server_last_modified="2024-01-01T00:00:00Z",
        indexed_on="2024-01-01T00:00:00Z",
        closed=False,
        date_closed=None,
        properties={},
        indices={},
    )
    defaults.update(kwargs)
    return CommCareCase(**defaults)


@pytest.mark.django_db
class TestCreateOrUpdateCaseByWorkArea:
    @pytest.mark.parametrize("has_case_id", [False, True])
    def test_success(self, has_case_id):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        existing_case_id = uuid.uuid4() if has_case_id else None
        opp_access = OpportunityAccessFactory(opportunity__api_key=api_key)
        work_area_group = WorkAreaGroupFactory(
            opportunity=opp_access.opportunity,
            assigned_user=opp_access,
        )
        work_area = WorkAreaFactory(
            opportunity=opp_access.opportunity,
            work_area_group=work_area_group,
            case_id=existing_case_id,
        )

        owner_id = str(uuid.uuid4())
        user_case = make_commcare_case(case_id=owner_id, owner_id=owner_id)
        work_area_case = make_commcare_case(case_id=str(existing_case_id or uuid.uuid4()))

        with patch("commcare_connect.commcarehq.api.get_usercase", return_value=user_case), patch(
            "commcare_connect.commcarehq.api.create_or_update_case", return_value=work_area_case
        ) as mock_create_or_update:
            create_or_update_case_by_work_area(work_area)

        mock_create_or_update.assert_called_once()
        _, call_kwargs = mock_create_or_update.call_args
        if has_case_id:
            assert call_kwargs.get("case_id") == existing_case_id
        else:
            assert call_kwargs.get("case_id") is None

    def test_no_assigned_user(self):
        work_area_group = WorkAreaGroupFactory(assigned_user=None)
        work_area = WorkAreaFactory(work_area_group=work_area_group)

        with pytest.raises(ValueError, match="assigned user"):
            create_or_update_case_by_work_area(work_area)
