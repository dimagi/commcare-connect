import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialApp
from django.contrib.sites.models import Site
from django.urls import reverse

from commcare_connect.users.templatetags.socialaccount_extras import account_for_provider


def _create_social_app(provider):
    app = SocialApp.objects.create(
        provider=provider, name=provider, client_id=f"{provider}-client", secret=f"{provider}-secret"
    )
    app.sites.add(Site.objects.get_current())
    return app


@pytest.mark.parametrize(
    "accounts, provider_id, expect_match",
    [
        ([], "ocs", False),
        ([SocialAccount(provider="ocs", uid="uid-ocs")], "ocs", True),
        ([SocialAccount(provider="ocs", uid="uid-ocs")], "commcarehq", False),
        (
            [SocialAccount(provider="ocs", uid="uid-ocs"), SocialAccount(provider="commcarehq", uid="uid-hq")],
            "commcarehq",
            True,
        ),
    ],
)
def test_account_for_provider(accounts, provider_id, expect_match):
    match = account_for_provider(accounts, provider_id)

    assert (match is not None and match.provider == provider_id) is expect_match


@pytest.mark.django_db
def test_connections_page_offers_to_connect_unconnected_providers(client, user):
    client.force_login(user)

    response = client.get(reverse("socialaccount_connections"))

    assert response.status_code == 200
    assert b"Open Chat Studio" in response.content
    assert b"Connect" in response.content
    assert b"Disconnect" not in response.content


@pytest.mark.django_db
def test_connections_page_shows_connected_account_with_disconnect_option(client, user):
    _create_social_app("ocs")
    SocialAccount.objects.create(user=user, provider="ocs", uid="uid-ocs")
    client.force_login(user)

    response = client.get(reverse("socialaccount_connections"))

    assert response.status_code == 200
    assert b"Connected" in response.content
    assert b"Disconnect" in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["ocs", "commcarehq"])
def test_connect_button_posts_instead_of_linking(client, user, provider):
    client.force_login(user)
    login_url = reverse(f"{provider}_login")

    response = client.get(reverse("socialaccount_connections"))

    assert f'action="{login_url}?process=connect"'.encode() in response.content
    assert f'href="{login_url}'.encode() not in response.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provider, authorize_url_prefix",
    [
        ("ocs", "https://www.openchatstudio.com/o/authorize/"),
        ("commcarehq", "https://staging.commcarehq.org/oauth/authorize/"),
    ],
)
def test_connect_post_redirects_straight_to_provider_instead_of_confirmation_page(
    client, user, provider, authorize_url_prefix
):
    _create_social_app(provider)
    client.force_login(user)

    response = client.post(f"{reverse(f'{provider}_login')}?process=connect")

    assert response.status_code == 302
    assert response.url.startswith(authorize_url_prefix)


@pytest.mark.django_db
def test_disconnecting_removes_the_social_account(client, user):
    _create_social_app("ocs")
    account = SocialAccount.objects.create(user=user, provider="ocs", uid="uid-ocs")
    user.set_password("a-usable-password")
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    client.force_login(user)

    response = client.post(reverse("socialaccount_connections"), {"account": account.pk})

    assert response.status_code == 302
    assert not SocialAccount.objects.filter(pk=account.pk).exists()
