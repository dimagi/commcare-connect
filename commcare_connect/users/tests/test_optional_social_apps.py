import pytest
from django.urls import reverse

from commcare_connect.users.templatetags.socialaccount_extras import configured_provider
from commcare_connect.users.tests.test_connections import _create_social_app


@pytest.mark.django_db
@pytest.mark.parametrize("configured", [True, False])
def test_configured_provider_only_returns_providers_with_a_social_app(rf, configured):
    if configured:
        _create_social_app("commcarehq")
    context = {"request": rf.get("/accounts/login/")}

    provider = configured_provider(context, "commcarehq")

    assert (provider.id if provider else None) == ("commcarehq" if configured else None)


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["account_login", "account_signup"])
def test_auth_pages_render_without_a_social_app(client, url_name):
    response = client.get(reverse(url_name))

    assert response.status_code == 200
    assert b"CommCareHQ" not in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["account_login", "account_signup"])
def test_auth_pages_offer_commcarehq_when_it_is_configured(client, url_name):
    _create_social_app("commcarehq")

    response = client.get(reverse(url_name))

    assert response.status_code == 200
    assert b"CommCareHQ" in response.content
    assert f'action="{reverse("commcarehq_login")}?process=login"'.encode() in response.content


@pytest.mark.django_db
def test_connections_page_renders_without_any_social_apps(client, user):
    client.force_login(user)

    response = client.get(reverse("socialaccount_connections"))

    assert response.status_code == 200
    assert b"No third-party accounts can be linked" in response.content
