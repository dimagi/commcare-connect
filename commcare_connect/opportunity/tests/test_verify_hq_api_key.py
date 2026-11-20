import re
from io import StringIO

import httpx
import pytest
from django.core.management import CommandError, call_command


@pytest.fixture
def hq_opportunity(opportunity):
    for app, domain in ((opportunity.learn_app, "learn-domain"), (opportunity.deliver_app, "deliver-domain")):
        app.cc_domain = domain
        app.save()
    return opportunity


def _mock_hq(httpx_mock, opportunity, failing=None):
    hq_url = opportunity.api_key.hq_server.url
    urls = {
        "key": f"{hq_url}/api/v0.5/user_domains/?limit=100",
        "learn": f"{hq_url}/a/learn-domain/api/v0.5/application/{opportunity.learn_app.cc_app_id}/",
        "deliver": f"{hq_url}/a/deliver-domain/api/v0.5/application/{opportunity.deliver_app.cc_app_id}/",
    }
    for check, url in urls.items():
        if check == failing:
            httpx_mock.add_response(url=url, status_code=401, text="Invalid API key")
        else:
            httpx_mock.add_response(url=url, json={"objects": []})


@pytest.mark.django_db
def test_verify_hq_api_key_passes(httpx_mock, hq_opportunity):
    _mock_hq(httpx_mock, hq_opportunity)
    out = StringIO()

    call_command("verify_hq_api_key", hq_opportunity.id, stdout=out)

    assert "The API key works for this opportunity." in out.getvalue()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "failing,expected_failure",
    [
        ("key", "FAIL  HQ accepts the key"),
        ("learn", "FAIL  key can read the learn app"),
        ("deliver", "FAIL  key can read the deliver app"),
    ],
)
def test_verify_hq_api_key_reports_failed_check(httpx_mock, hq_opportunity, failing, expected_failure):
    _mock_hq(httpx_mock, hq_opportunity, failing=failing)
    out = StringIO()

    with pytest.raises(CommandError, match="1 check"):
        call_command("verify_hq_api_key", hq_opportunity.id, stdout=out)

    assert expected_failure in out.getvalue()
    assert "Invalid API key" in out.getvalue()


@pytest.mark.django_db
def test_verify_hq_api_key_unreachable_hq(httpx_mock, hq_opportunity):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=re.compile(".*"), is_reusable=True)
    out = StringIO()

    with pytest.raises(CommandError, match="3 check"):
        call_command("verify_hq_api_key", hq_opportunity.id, stdout=out)

    assert "connection refused" in out.getvalue()


@pytest.mark.django_db
def test_verify_hq_api_key_without_key(opportunity):
    opportunity.api_key = None
    opportunity.save()

    with pytest.raises(CommandError, match="has no HQ API key"):
        call_command("verify_hq_api_key", opportunity.id)
