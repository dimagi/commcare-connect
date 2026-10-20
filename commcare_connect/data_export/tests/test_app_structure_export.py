import pytest

from commcare_connect.data_export.const import DELIVER_APP_KEY, LEARN_APP_KEY
from commcare_connect.data_export.tests.helpers import add_export_credentials, export_url
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


def _hq_app_url(opportunity, app):
    return f"{opportunity.api_key.hq_server.url}/a/{app.cc_domain}/api/v0.5/application/{app.cc_app_id}/"


@pytest.mark.django_db
class TestAppStructureView:
    @pytest.mark.parametrize(
        "app_type,fetched_apps",
        [
            pytest.param(None, ["learn", "deliver"], id="both_by_default"),
            pytest.param("learn", ["learn"], id="learn_only"),
            pytest.param("deliver", ["deliver"], id="deliver_only"),
        ],
    )
    def test_returns_requested_apps(
        self, api_client, opportunity, org_user_member, httpx_mock, app_type, fetched_apps
    ):
        apps = {
            "learn": (LEARN_APP_KEY, opportunity.learn_app, "Learn"),
            "deliver": (DELIVER_APP_KEY, opportunity.deliver_app, "Deliver"),
        }
        expected = {LEARN_APP_KEY: None, DELIVER_APP_KEY: None}
        for fetched_app in fetched_apps:
            key, app, name = apps[fetched_app]
            app_json = {"id": app.cc_app_id, "name": name}
            httpx_mock.add_response(url=_hq_app_url(opportunity, app), json=app_json)
            expected[key] = app_json
        add_export_credentials(api_client, org_user_member)

        params = {"app_type": app_type} if app_type else {}
        response = api_client.get(export_url(url_name="app_structure", opp_id=opportunity.id, **params))
        assert response.status_code == 200
        data = response.json()
        assert data[LEARN_APP_KEY] == expected[LEARN_APP_KEY]
        assert data[DELIVER_APP_KEY] == expected[DELIVER_APP_KEY]

    def test_invalid_app_type_returns_400(self, api_client, opportunity, org_user_member):
        add_export_credentials(api_client, org_user_member)

        response = api_client.get(export_url(url_name="app_structure", opp_id=opportunity.id, app_type="invalid"))
        assert response.status_code == 400

    def test_missing_api_key_returns_404(self, api_client, organization, org_user_member):
        from commcare_connect.opportunity.tests.factories import OpportunityFactory

        opp = OpportunityFactory(organization=organization, api_key=None)
        add_export_credentials(api_client, org_user_member)

        response = api_client.get(export_url(url_name="app_structure", opp_id=opp.id))
        assert response.status_code == 404

    def test_hq_error_returns_502(self, api_client, opportunity, org_user_member, httpx_mock):
        httpx_mock.add_response(
            url=_hq_app_url(opportunity, opportunity.learn_app),
            status_code=500,
        )
        add_export_credentials(api_client, org_user_member)

        response = api_client.get(export_url(url_name="app_structure", opp_id=opportunity.id, app_type="learn"))
        assert response.status_code == 502

    def test_unauthenticated_returns_401(self, api_client, opportunity):
        response = api_client.get(export_url(url_name="app_structure", opp_id=opportunity.id))
        assert response.status_code in (401, 403)
