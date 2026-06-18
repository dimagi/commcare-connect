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
from commcare_connect.users.models import ConnectIDUserLink
from commcare_connect.users.tests.factories import ConnectIdUserLinkFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

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
    def _make_work_area(self, case_id=None):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        opp_access = OpportunityAccessFactory(opportunity__api_key=api_key)
        work_area_group = WorkAreaGroupFactory(opportunity=opp_access.opportunity)
        return WorkAreaFactory(
            opportunity=opp_access.opportunity,
            work_area_group=work_area_group,
            opportunity_access=opp_access,
            case_id=case_id,
        )

    def _make_link(self, work_area, hq_user_uuid=None):
        opp_access = work_area.opportunity_access
        domain = opp_access.opportunity.deliver_app.cc_domain
        user = opp_access.user
        return ConnectIdUserLinkFactory(
            user=user,
            commcare_username=f"{user.username.lower()}@{domain}.commcarehq.org",
            domain=domain,
            hq_server=opp_access.opportunity.api_key.hq_server,
            hq_user_uuid=hq_user_uuid,
        )

    @pytest.mark.parametrize("has_case_id", [False, True])
    def test_uses_stored_hq_user_uuid_when_available(self, has_case_id):
        existing_case_id = str(uuid.uuid4()) if has_case_id else None
        work_area = self._make_work_area(case_id=existing_case_id)

        hq_user_uuid = "stored-uuid"
        self._make_link(work_area, hq_user_uuid=hq_user_uuid)
        work_area_case = make_commcare_case(case_id=str(existing_case_id or str(uuid.uuid4())))

        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid") as mock_fetch,
            patch(
                "commcare_connect.commcarehq.api.create_or_update_case", return_value=work_area_case
            ) as mock_create_or_update,
        ):
            create_or_update_case_by_work_area(work_area)

        mock_fetch.assert_not_called()
        call_args, call_kwargs = mock_create_or_update.call_args
        assert call_args[2]["owner_id"] == hq_user_uuid
        assert call_kwargs.get("case_id") == existing_case_id

    def test_fetches_and_persists_uuid_when_link_has_none(self):
        work_area = self._make_work_area()
        link = self._make_link(work_area)

        fetched_uuid = "fetched-uuid"
        work_area_case = make_commcare_case()

        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=fetched_uuid) as mock_fetch,
            patch(
                "commcare_connect.commcarehq.api.create_or_update_case", return_value=work_area_case
            ) as mock_create_or_update,
        ):
            create_or_update_case_by_work_area(work_area)

        mock_fetch.assert_called_once()
        call_args, _ = mock_create_or_update.call_args
        assert call_args[2]["owner_id"] == fetched_uuid
        link.refresh_from_db()
        assert link.hq_user_uuid == fetched_uuid

    def test_raises_when_link_does_not_exist(self):
        work_area = self._make_work_area()

        with pytest.raises(ConnectIDUserLink.DoesNotExist):
            create_or_update_case_by_work_area(work_area)

    def test_raises_when_user_not_found_on_hq(self):
        work_area = self._make_work_area()
        self._make_link(work_area)

        with patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=None):
            with pytest.raises(CommCareHQAPIException, match="Failed to find HQ user"):
                create_or_update_case_by_work_area(work_area)

    def test_no_opportunity_access(self):
        work_area = WorkAreaFactory()

        with pytest.raises(ValueError, match="Work Area must have an assigned Opportunity Access"):
            create_or_update_case_by_work_area(work_area)


@pytest.mark.django_db
class TestBulkCreateOrUpdateCasesByWorkAreas:
    def _make_opportunity_with_work_area(self):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        opp_access = OpportunityAccessFactory(opportunity__api_key=api_key)
        wa_group = WorkAreaGroupFactory(opportunity=opp_access.opportunity)
        work_area = WorkAreaFactory(
            opportunity=opp_access.opportunity,
            work_area_group=wa_group,
            opportunity_access=opp_access,
        )
        return opp_access.opportunity, work_area

    def _make_link(self, opportunity, user, hq_user_uuid=None):
        domain = opportunity.deliver_app.cc_domain
        return ConnectIdUserLinkFactory(
            user=user,
            commcare_username=f"{user.username.lower()}@{domain}.commcarehq.org",
            domain=domain,
            hq_server=opportunity.api_key.hq_server,
            hq_user_uuid=hq_user_uuid,
        )

    def test_uses_stored_uuid_and_fetches_missing(self):
        opportunity, wa_with_stored = self._make_opportunity_with_work_area()
        wa_group = wa_with_stored.work_area_group
        access_to_fetch = OpportunityAccessFactory(opportunity=opportunity)
        wa_to_fetch = WorkAreaFactory(
            opportunity=opportunity, work_area_group=wa_group, opportunity_access=access_to_fetch
        )

        stored_uuid = "stored-uuid"
        fetched_uuid = "fetched-uuid"
        self._make_link(opportunity, wa_with_stored.opportunity_access.user, hq_user_uuid=stored_uuid)
        link_to_backfill = self._make_link(opportunity, access_to_fetch.user)

        returned_cases = [make_commcare_case(), make_commcare_case()]
        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=fetched_uuid) as mock_fetch,
            patch(
                "commcare_connect.commcarehq.api.bulk_create_or_update_cases", return_value=returned_cases
            ) as mock_bulk,
        ):
            bulk_create_or_update_cases_by_work_areas([wa_with_stored, wa_to_fetch], opportunity)

        mock_fetch.assert_called_once()
        sent_cases = mock_bulk.call_args[0][2]
        owners_by_external_id = {c["external_id"]: c["owner_id"] for c in sent_cases}
        assert owners_by_external_id[str(wa_with_stored.id)] == stored_uuid
        assert owners_by_external_id[str(wa_to_fetch.id)] == fetched_uuid
        link_to_backfill.refresh_from_db()
        assert link_to_backfill.hq_user_uuid == fetched_uuid

    def test_raises_when_user_not_found_on_hq(self):
        opportunity, work_area = self._make_opportunity_with_work_area()
        self._make_link(opportunity, work_area.opportunity_access.user)

        with patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=None):
            with pytest.raises(CommCareHQAPIException, match="Failed to find HQ user"):
                bulk_create_or_update_cases_by_work_areas([work_area], opportunity)

    def test_returns_empty_for_no_work_areas(self):
        opportunity, _ = self._make_opportunity_with_work_area()

        assert bulk_create_or_update_cases_by_work_areas([], opportunity) == []

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
        for wa in work_areas:
            self._make_link(opportunity, wa.opportunity_access.user, hq_user_uuid="stored-uuid")

        # Build HQ response in reversed order to prove ordering is not assumed.
        expected_case_ids = {str(wa.pk): str(uuid.uuid4()) for wa in work_areas}
        returned_cases = [
            make_commcare_case(case_id=expected_case_ids[str(wa.pk)], external_id=str(wa.pk))
            for wa in reversed(work_areas)
        ]

        with patch(
            "commcare_connect.commcarehq.api.bulk_create_or_update_cases",
            return_value=returned_cases,
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
