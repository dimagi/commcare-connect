from unittest import mock

import pytest
from django.urls import reverse

from commcare_connect.opportunity.tests.factories import HQApiKeyFactory


@pytest.mark.django_db
class TestGetDomains:
    @pytest.mark.parametrize("api_key", ["", "None", "abc"])
    def test_invalid_api_key_returns_placeholder(self, client, user, api_key):
        client.force_login(user)
        response = client.get(reverse("commcarehq:get_domains"), {"hq_server": "1", "api_key": api_key})
        assert response.status_code == 200
        content = response.content.decode()
        assert "Select an API Key to load domains." in content
        assert "value='None'" not in content

    def test_valid_api_key_returns_domains(self, client, user):
        api_key = HQApiKeyFactory(user=user)
        client.force_login(user)
        with mock.patch(
            "commcare_connect.commcarehq.views.get_domains_for_user", return_value=["domain-a", "domain-b"]
        ):
            response = client.get(
                reverse("commcarehq:get_domains"),
                {"hq_server": str(api_key.hq_server.id), "api_key": str(api_key.id)},
            )
        assert response.status_code == 200
        content = response.content.decode()
        assert "<option value=''>Select a Domain.</option>" in content
        assert "<option value='domain-a'>domain-a</option>" in content
        assert "value='None'" not in content


@pytest.mark.django_db
class TestGetApplication:
    def test_none_api_key_returns_placeholder(self, client, user):
        client.force_login(user)
        response = client.get(
            reverse("commcarehq:get_applications_by_domain"),
            {"hq_server": "1", "api_key": "None", "learn_app_domain": "domain-a"},
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Select a Domain to load applications." in content
        assert "value='None'" not in content
