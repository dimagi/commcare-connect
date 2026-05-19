import uuid
from unittest.mock import patch

import pytest

from commcare_connect.commcarehq.api import (
    CommCareCase,
    bulk_create_or_update_cases_by_work_areas,
    bulk_update_usercases,
    create_or_update_case_by_work_area,
)
from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.microplanning.const import WORK_AREA_CASE_TYPE
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityAccessFactory, OpportunityFactory

DOMAIN = "test-domain"


def make_commcare_case(**kwargs) -> CommCareCase:
    defaults = dict(
        domain=DOMAIN,
        case_id=str(uuid.uuid4()),
        case_type=WORK_AREA_CASE_TYPE,
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
        work_area_group = WorkAreaGroupFactory(opportunity=opp_access.opportunity)
        work_area = WorkAreaFactory(
            opportunity=opp_access.opportunity,
            work_area_group=work_area_group,
            opportunity_access=opp_access,
            case_id=existing_case_id,
        )

        owner_id = str(uuid.uuid4())
        user_case = make_commcare_case(case_id=owner_id, owner_id=owner_id)
        work_area_case = make_commcare_case(case_id=str(existing_case_id or uuid.uuid4()))

        with (
            patch("commcare_connect.commcarehq.api.get_usercase", return_value=user_case),
            patch(
                "commcare_connect.commcarehq.api.create_or_update_case", return_value=work_area_case
            ) as mock_create_or_update,
        ):
            create_or_update_case_by_work_area(work_area)

        mock_create_or_update.assert_called_once()
        _, call_kwargs = mock_create_or_update.call_args
        if has_case_id:
            assert call_kwargs.get("case_id") == existing_case_id
        else:
            assert call_kwargs.get("case_id") is None

    def test_no_opportunity_access(self):
        work_area = WorkAreaFactory()

        with pytest.raises(ValueError, match="Work Area must have an assigned Opportunity Access"):
            create_or_update_case_by_work_area(work_area)


@pytest.mark.django_db
class TestBulkCreateOrUpdateCasesByWorkAreas:
    def test_matches_cases_to_work_areas_by_external_id(self):
        # Regression: HQ may return cases in a different order than they were
        # sent. The function must match each returned case back to its
        # WorkArea via external_id, not by list position.
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        opportunity = OpportunityFactory(api_key=api_key)
        work_areas = [
            WorkAreaFactory(
                opportunity=opportunity,
                opportunity_access=OpportunityAccessFactory(opportunity=opportunity),
                case_id=None,
            )
            for _ in range(3)
        ]

        # Build HQ response in reversed order to prove ordering is not assumed.
        expected_case_ids = {str(wa.pk): str(uuid.uuid4()) for wa in work_areas}
        returned_cases = [
            make_commcare_case(case_id=expected_case_ids[str(wa.pk)], external_id=str(wa.pk))
            for wa in reversed(work_areas)
        ]
        user_case = make_commcare_case(case_id=str(uuid.uuid4()))

        with (
            patch("commcare_connect.commcarehq.api.get_usercase", return_value=user_case),
            patch(
                "commcare_connect.commcarehq.api.bulk_create_or_update_cases",
                return_value=returned_cases,
            ),
        ):
            bulk_create_or_update_cases_by_work_areas(work_areas, opportunity)

        for wa in work_areas:
            wa.refresh_from_db()
            assert str(wa.case_id) == expected_case_ids[str(wa.pk)]


@pytest.mark.django_db
class TestBulkUpdateUsercases:
    def test_falls_back_to_get_usercase_when_link_missing(self):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        access = OpportunityAccessFactory(opportunity__api_key=api_key)
        hq_case_id = str(uuid.uuid4())
        user_case = make_commcare_case(case_id=hq_case_id, owner_id=hq_case_id)

        with (
            patch("commcare_connect.commcarehq.api.get_usercase", return_value=user_case) as mock_get_usercase,
            patch("commcare_connect.commcarehq.api.bulk_create_or_update_cases") as mock_bulk,
        ):
            bulk_update_usercases({access: {"properties": {"prop": "value"}}})

        mock_get_usercase.assert_called_once_with(access)
        mock_bulk.assert_called_once()
        cases_data = mock_bulk.call_args[0][2]
        assert cases_data == [{"case_id": hq_case_id, "properties": {"prop": "value"}}]
        assert mock_bulk.call_args[1]["create"] is False

    def test_raises_when_updates_span_multiple_opportunities(self):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        access_a = OpportunityAccessFactory(opportunity__api_key=api_key)
        access_b = OpportunityAccessFactory(opportunity__api_key=api_key)

        with pytest.raises(ValueError, match="same opportunity"):
            bulk_update_usercases(
                {
                    access_a: {"properties": {"prop": "1"}},
                    access_b: {"properties": {"prop": "1"}},
                }
            )
