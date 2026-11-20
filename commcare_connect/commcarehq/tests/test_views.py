from unittest.mock import patch

import pytest
from django.urls import reverse

from commcare_connect.opportunity.tests.factories import HQApiKeyFactory


@pytest.mark.django_db
class TestPlaceholderOptions:
    def test_get_domains_placeholder_has_empty_value(self, client, user):
        api_key = HQApiKeyFactory(user=user)
        client.force_login(user)
        with patch("commcare_connect.commcarehq.views.get_domains_for_user", return_value=["demo"]):
            response = client.get(
                reverse("commcarehq:get_domains"),
                {"hq_server": api_key.hq_server.id, "api_key": api_key.id},
            )
        content = response.content.decode()
        assert "<option value=''>Select a Domain.</option>" in content
        assert "value='None'" not in content

    def test_get_application_with_empty_domain_does_not_call_hq(self, client, user):
        api_key = HQApiKeyFactory(user=user)
        client.force_login(user)
        with patch("commcare_connect.commcarehq.views.get_applications_for_user_by_domain") as mock_get_apps:
            response = client.get(
                reverse("commcarehq:get_applications_by_domain"),
                {"hq_server": api_key.hq_server.id, "api_key": api_key.id, "learn_app_domain": ""},
            )
        assert response.status_code == 200
        assert "<option value=''>Select a Domain to load applications.</option>" in response.content.decode()
        mock_get_apps.assert_not_called()
