import datetime

import pytest
from django.utils.timezone import now

from commcare_connect.utils.commcarehq_api import CommCareHQAPIException, get_app_structure


@pytest.mark.django_db
class TestGetAppStructure:
    def test_returns_app_json(self, opportunity, httpx_mock):
        app = opportunity.learn_app
        api_key = opportunity.api_key
        expected = {"id": app.cc_app_id, "name": "Test App", "modules": []}
        httpx_mock.add_response(
            url=f"{api_key.hq_server.url}/a/{app.cc_domain}/api/v0.5/application/{app.cc_app_id}/",
            json=expected,
        )

        result = get_app_structure(api_key, app)
        assert result == expected

    def test_raises_on_hq_error(self, opportunity, httpx_mock):
        app = opportunity.learn_app
        api_key = opportunity.api_key
        httpx_mock.add_response(
            url=f"{api_key.hq_server.url}/a/{app.cc_domain}/api/v0.5/application/{app.cc_app_id}/",
            status_code=403,
        )

        with pytest.raises(CommCareHQAPIException):
            get_app_structure(api_key, app)


def _add_export_credentials(api_client, user):
    token, _ = user.oauth2_provider_accesstoken.get_or_create(
        token="export-token",
        scope="read write export",
        defaults={"expires": now() + datetime.timedelta(hours=1)},
    )
    api_client.credentials(Authorization=f"Bearer {token}")


def _hq_app_url(opportunity, app):
    return f"{opportunity.api_key.hq_server.url}/a/{app.cc_domain}" f"/api/v0.5/application/{app.cc_app_id}/"


@pytest.mark.django_db
class TestAppStructureView:
    def _get_url(self, opp_id, **params):
        from django.urls import reverse

        url = reverse("data_export:app_structure", kwargs={"opp_id": opp_id})
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        return url

    def test_returns_both_apps_by_default(self, api_client, opportunity, org_user_member, httpx_mock):
        learn_json = {"id": opportunity.learn_app.cc_app_id, "name": "Learn"}
        deliver_json = {"id": opportunity.deliver_app.cc_app_id, "name": "Deliver"}
        httpx_mock.add_response(
            url=_hq_app_url(opportunity, opportunity.learn_app),
            json=learn_json,
        )
        httpx_mock.add_response(
            url=_hq_app_url(opportunity, opportunity.deliver_app),
            json=deliver_json,
        )
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id))
        assert response.status_code == 200
        data = response.json()
        assert data["learn_app"] == learn_json
        assert data["deliver_app"] == deliver_json

    def test_learn_only(self, api_client, opportunity, org_user_member, httpx_mock):
        learn_json = {"id": opportunity.learn_app.cc_app_id, "name": "Learn"}
        httpx_mock.add_response(
            url=_hq_app_url(opportunity, opportunity.learn_app),
            json=learn_json,
        )
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, app_type="learn"))
        assert response.status_code == 200
        data = response.json()
        assert data["learn_app"] == learn_json
        assert data["deliver_app"] is None

    def test_deliver_only(self, api_client, opportunity, org_user_member, httpx_mock):
        deliver_json = {"id": opportunity.deliver_app.cc_app_id, "name": "Deliver"}
        httpx_mock.add_response(
            url=_hq_app_url(opportunity, opportunity.deliver_app),
            json=deliver_json,
        )
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, app_type="deliver"))
        assert response.status_code == 200
        data = response.json()
        assert data["learn_app"] is None
        assert data["deliver_app"] == deliver_json

    def test_invalid_app_type_returns_400(self, api_client, opportunity, org_user_member):
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, app_type="invalid"))
        assert response.status_code == 400

    def test_missing_api_key_returns_404(self, api_client, organization, org_user_member):
        from commcare_connect.opportunity.tests.factories import OpportunityFactory

        opp = OpportunityFactory(organization=organization, api_key=None)
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opp.id))
        assert response.status_code == 404

    def test_hq_error_returns_502(self, api_client, opportunity, org_user_member, httpx_mock):
        httpx_mock.add_response(
            url=_hq_app_url(opportunity, opportunity.learn_app),
            status_code=500,
        )
        _add_export_credentials(api_client, org_user_member)

        response = api_client.get(self._get_url(opportunity.id, app_type="learn"))
        assert response.status_code == 502

    def test_unauthenticated_returns_401(self, api_client, opportunity):
        response = api_client.get(self._get_url(opportunity.id))
        assert response.status_code in (401, 403)
