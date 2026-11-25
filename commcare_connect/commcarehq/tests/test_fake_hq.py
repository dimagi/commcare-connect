"""The fake HQ must answer in shapes Connect's own HQ and ConnectID clients can read."""

import pytest

from commcare_connect.commcarehq.api import bulk_create_or_update_cases, create_or_update_case
from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.commcarehq.tests.fake_hq import serve_fake_hq
from commcare_connect.connect_id_client import send_message
from commcare_connect.connect_id_client.models import Message
from commcare_connect.opportunity.tests.factories import HQApiKeyFactory
from commcare_connect.utils.commcarehq_api import CommCareHQAPIException

DOMAIN = "demo-domain"
NEW_WORK_AREA_CASE = {
    "case_name": "area-17",
    "case_type": "work-area",
    "external_id": "17",
    "owner_id": "worker-uuid",
    "properties": {"ward": "Ward A"},
}


@pytest.fixture
def fake_hq():
    with serve_fake_hq() as hq:
        yield hq


@pytest.fixture
def api_key(db, fake_hq):
    return HQApiKeyFactory(hq_server=HQServerFactory(url=fake_hq.url))


def test_bulk_update_answers_with_one_case_per_item(api_key):
    cases = bulk_create_or_update_cases(
        api_key=api_key,
        domain=DOMAIN,
        cases_data=[{"case_id": "case-a", "properties": {"wag_centroid": "25.1 75.8"}}, NEW_WORK_AREA_CASE],
        create=None,
    )

    assert cases[0].case_id == "case-a"
    assert cases[1].external_id == "17"
    assert cases[1].case_id
    assert cases[1].domain == DOMAIN


def test_upserting_a_work_area_again_keeps_its_case_id(api_key):
    first = bulk_create_or_update_cases(api_key=api_key, domain=DOMAIN, cases_data=[NEW_WORK_AREA_CASE], create=None)
    second = bulk_create_or_update_cases(api_key=api_key, domain=DOMAIN, cases_data=[NEW_WORK_AREA_CASE], create=None)

    assert second[0].case_id == first[0].case_id


def test_single_case_update_keeps_its_case_id(api_key):
    case = create_or_update_case(api_key=api_key, domain=DOMAIN, case_data=NEW_WORK_AREA_CASE, case_id="case-a")

    assert case.case_id == "case-a"


def test_prints_each_request_with_its_body(api_key, fake_hq):
    bulk_create_or_update_cases(
        api_key=api_key, domain=DOMAIN, cases_data=[{"case_id": "case-a", "owner_id": "-"}], create=False
    )

    output = fake_hq.output.getvalue()
    assert f"POST /a/{DOMAIN}/api/case/v2/ -> 200 (1 items)" in output
    assert '"owner_id": "-"' in output


def test_fail_mode_answers_case_updates_with_an_error(db):
    with serve_fake_hq(fail_case_updates=True) as hq:
        api_key = HQApiKeyFactory(hq_server=HQServerFactory(url=hq.url))
        with pytest.raises(CommCareHQAPIException):
            bulk_create_or_update_cases(api_key=api_key, domain=DOMAIN, cases_data=[NEW_WORK_AREA_CASE])


def test_answers_connectid_push_notifications(fake_hq, settings):
    settings.CONNECTID_URL = f"{fake_hq.url}/connectid"

    response = send_message(Message(usernames=["mp_worker_0"], data={"key": "work_area_assignment"}))

    assert response.all_success
