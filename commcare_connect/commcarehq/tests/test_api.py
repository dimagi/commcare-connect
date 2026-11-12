import json
import uuid
from unittest.mock import patch

import pytest

from commcare_connect.commcarehq.api import (
    bulk_create_or_update_cases,
    bulk_update_usercases,
)
from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.commcarehq.tests.utils import DOMAIN, make_commcare_case
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory, OpportunityAccessFactory


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

    def test_raises_when_updates_span_multiple_opportunities(self):
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        access = OpportunityAccessFactory(opportunity__api_key=api_key)
        other_access = OpportunityAccessFactory(opportunity__api_key=api_key)

        with patch("commcare_connect.commcarehq.api.bulk_create_or_update_cases") as mock_bulk:
            with pytest.raises(ValueError, match="same opportunity"):
                bulk_update_usercases({access: {"properties": {}}, other_access: {"properties": {}}})

        mock_bulk.assert_not_called()


@pytest.mark.django_db
class TestBulkCreateOrUpdateCases:
    def send(self, httpx_mock, cases_data, create=False):
        """Send cases_data to a fake HQ, and return the requests it got."""
        httpx_mock.add_response(json={"cases": []}, is_reusable=True)
        api_key = HQApiKeyFactory(hq_server=HQServerFactory())
        bulk_create_or_update_cases(api_key=api_key, domain=DOMAIN, cases_data=cases_data, create=create)
        return httpx_mock.get_requests()

    @pytest.mark.parametrize("create", [False, None, True])
    def test_injects_create_flag_into_every_row(self, httpx_mock, create):
        requests = self.send(httpx_mock, cases_data=[{"case_id": "case-1"}, {"case_id": "case-2"}], create=create)

        assert json.loads(requests[0].content) == [
            {"case_id": "case-1", "create": create},
            {"case_id": "case-2", "create": create},
        ]

    def test_sends_100_cases_per_request(self, httpx_mock):
        requests = self.send(httpx_mock, cases_data=[{"case_id": f"case-{n}"} for n in range(201)])

        assert [len(json.loads(request.content)) for request in requests] == [100, 100, 1]

    def test_gives_hq_30_seconds_per_request(self, httpx_mock):
        requests = self.send(httpx_mock, cases_data=[{"case_id": "case-1"}])

        assert requests[0].extensions["timeout"] == {"connect": 30, "read": 30, "write": 30, "pool": 30}
