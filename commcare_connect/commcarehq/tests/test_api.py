import json
import uuid
from unittest.mock import patch

import pytest

from commcare_connect.commcarehq.api import (
    CommCareCase,
    bulk_create_or_update_cases_by_work_areas,
    bulk_update_cases,
    create_or_update_case_by_work_area,
)
from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.microplanning.const import WORK_AREA_CASE_TYPE
from commcare_connect.microplanning.tests.factories import WorkAreaFactory, WorkAreaGroupFactory
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityAccessFactory
from commcare_connect.users.models import ConnectIDUserLink
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

    @pytest.mark.parametrize("has_case_id", [False, True])
    def test_uses_stored_hq_user_uuid_when_available(self, has_case_id):
        existing_case_id = uuid.uuid4() if has_case_id else None
        work_area = self._make_work_area(case_id=existing_case_id)
        user = work_area.opportunity_access.user

        hq_user_uuid = "stored-uuid"
        ConnectIDUserLink.objects.create(user=user, commcare_username=user.username.lower(), hq_user_uuid=hq_user_uuid)
        work_area_case = make_commcare_case(case_id=str(existing_case_id or uuid.uuid4()))

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
        user = work_area.opportunity_access.user
        link = ConnectIDUserLink.objects.create(user=user, commcare_username=user.username.lower())

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

    def test_fetches_uuid_when_no_link_exists(self):
        work_area = self._make_work_area()
        user = work_area.opportunity_access.user
        assert not ConnectIDUserLink.objects.filter(user=user).exists()

        fetched_uuid = "fetched-uuid"
        work_area_case = make_commcare_case()

        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=fetched_uuid),
            patch(
                "commcare_connect.commcarehq.api.create_or_update_case", return_value=work_area_case
            ) as mock_create_or_update,
        ):
            create_or_update_case_by_work_area(work_area)

        call_args, _ = mock_create_or_update.call_args
        assert call_args[2]["owner_id"] == fetched_uuid
        assert not ConnectIDUserLink.objects.filter(user=user).exists()

    def test_raises_when_user_not_found_on_hq(self):
        work_area = self._make_work_area()

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

    def test_uses_stored_uuid_and_fetches_missing(self):
        opportunity, wa_with_stored = self._make_opportunity_with_work_area()
        wa_group = wa_with_stored.work_area_group
        access_to_fetch = OpportunityAccessFactory(opportunity=opportunity)
        wa_to_fetch = WorkAreaFactory(
            opportunity=opportunity, work_area_group=wa_group, opportunity_access=access_to_fetch
        )

        stored_uuid = "stored-uuid"
        fetched_uuid = "fetched-uuid"
        ConnectIDUserLink.objects.create(
            user=wa_with_stored.opportunity_access.user,
            commcare_username=wa_with_stored.opportunity_access.user.username.lower(),
            hq_user_uuid=stored_uuid,
        )
        link_to_backfill = ConnectIDUserLink.objects.create(
            user=access_to_fetch.user,
            commcare_username=access_to_fetch.user.username.lower(),
        )

        returned_cases = [make_commcare_case(), make_commcare_case()]
        with (
            patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=fetched_uuid) as mock_fetch,
            patch(
                "commcare_connect.commcarehq.api.bulk_create_or_update_cases", return_value=returned_cases
            ) as mock_bulk,
        ):
            bulk_create_or_update_cases_by_work_areas([wa_with_stored, wa_to_fetch], opportunity)

        mock_fetch.assert_called_once()
        _, kwargs = mock_fetch.call_args
        sent_cases = mock_bulk.call_args[0][2]
        owners_by_external_id = {c["external_id"]: c["owner_id"] for c in sent_cases}
        assert owners_by_external_id[str(wa_with_stored.id)] == stored_uuid
        assert owners_by_external_id[str(wa_to_fetch.id)] == fetched_uuid
        link_to_backfill.refresh_from_db()
        assert link_to_backfill.hq_user_uuid == fetched_uuid

    def test_raises_when_user_not_found_on_hq(self):
        opportunity, work_area = self._make_opportunity_with_work_area()

        with patch("commcare_connect.commcarehq.api.fetch_hq_user_uuid", return_value=None):
            with pytest.raises(CommCareHQAPIException, match="Failed to find HQ user"):
                bulk_create_or_update_cases_by_work_areas([work_area], opportunity)

    def test_returns_empty_for_no_work_areas(self):
        opportunity, _ = self._make_opportunity_with_work_area()

        assert bulk_create_or_update_cases_by_work_areas([], opportunity) == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status_code, expect_exception",
    [(200, False), (500, True)],
)
def test_bulk_update_cases(httpx_mock, status_code, expect_exception):
    api_key = HQApiKeyFactory(hq_server=HQServerFactory())
    updates = [
        {"case_id": "case-1", "owner_id": ""},
        {"case_id": "case-2", "owner_id": ""},
    ]
    httpx_mock.add_response(
        method="POST",
        url=f"{api_key.hq_server.url}/a/{DOMAIN}/api/case/v2/",
        status_code=status_code,
        json={} if not expect_exception else None,
    )

    if expect_exception:
        with pytest.raises(CommCareHQAPIException):
            bulk_update_cases(api_key, DOMAIN, updates)
    else:
        bulk_update_cases(api_key, DOMAIN, updates)
        request = httpx_mock.get_request()
        assert request.method == "POST"
        expected_payload = [{**update, "create": False} for update in updates]
        assert json.loads(request.content) == expected_payload
        assert request.headers["Authorization"] == f"ApiKey {api_key.user.email}:{api_key.api_key}"
