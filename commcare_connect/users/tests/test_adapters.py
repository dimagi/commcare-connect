import pytest
from allauth.account.models import EmailAddress
from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.base import AuthProcess
from django.core.exceptions import ValidationError
from django.test import RequestFactory, override_settings

from commcare_connect.users.adapters import AccountAdapter, SocialAccountAdapter
from commcare_connect.users.models import User


class TestAccountAdapter:
    @override_settings(ACCOUNT_ALLOW_REGISTRATION=False)
    def test_respects_registration_setting(self, rf: RequestFactory):
        assert not AccountAdapter().is_open_for_signup(rf.get("/"))


class TestSocialAccountAdapter:
    @override_settings(ACCOUNT_ALLOW_REGISTRATION=False)
    def test_respects_registration_setting(self, rf: RequestFactory):
        assert not SocialAccountAdapter().is_open_for_signup(rf.get("/"), sociallogin=None)


def _sociallogin(user, *, provider, process):
    sociallogin = SocialLogin(user=user, account=SocialAccount(provider=provider, uid="x"))
    sociallogin.state = {"process": process}
    return sociallogin


def _attach_messages(request):
    from django.contrib.messages.middleware import MessageMiddleware
    from django.contrib.sessions.middleware import SessionMiddleware

    SessionMiddleware(lambda r: None).process_request(request)
    MessageMiddleware(lambda r: None).process_request(request)


@pytest.mark.parametrize("provider", ["ocs", "commcarehq"])
@pytest.mark.django_db
def test_existing_user_can_link_new_provider_account(user, rf, provider):
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    request = rf.get("/")
    sociallogin = _sociallogin(user, provider=provider, process=AuthProcess.CONNECT)

    # Should NOT raise.
    SocialAccountAdapter().pre_social_login(request, sociallogin)


@pytest.mark.django_db
def test_new_user_can_signup_via_hq(rf):
    new_user = User(email="brand-new-hq-user@example.com")
    request = rf.get("/")
    sociallogin = _sociallogin(new_user, provider="commcarehq", process=AuthProcess.LOGIN)

    SocialAccountAdapter().pre_social_login(request, sociallogin)


@pytest.mark.parametrize("provider", ["ocs", "commcarehq"])
@pytest.mark.django_db
def test_login_process_still_raises_on_existing_email(user, rf, provider):

    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    request = rf.get("/")
    _attach_messages(request)
    sociallogin = _sociallogin(user, provider=provider, process=AuthProcess.LOGIN)

    with pytest.raises(ImmediateHttpResponse):
        SocialAccountAdapter().pre_social_login(request, sociallogin)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provider, has_usable_password, other_provider, expect_raises",
    [
        pytest.param("ocs", False, None, False, id="sole_ocs_account_no_password_can_still_disconnect"),
        pytest.param("commcarehq", False, None, True, id="hq_only_signup_cannot_disconnect"),
        pytest.param("commcarehq", False, "ocs", True, id="hq_cannot_disconnect_even_with_ocs_fallback_connected"),
        pytest.param("commcarehq", True, None, False, id="hq_can_disconnect_when_password_is_set"),
        pytest.param("ocs", False, "commcarehq", False, id="ocs_can_disconnect_when_hq_is_connected"),
    ],
)
def test_validate_disconnect(user, provider, has_usable_password, other_provider, expect_raises):
    if has_usable_password:
        user.set_password("a-usable-password")
        user.save()
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    else:
        user.set_unusable_password()
        user.save()

    account = SocialAccount.objects.create(user=user, provider=provider, uid=f"uid-{provider}")
    accounts = [account]
    if other_provider:
        accounts.append(SocialAccount.objects.create(user=user, provider=other_provider, uid=f"uid-{other_provider}"))

    if expect_raises:
        with pytest.raises(ValidationError):
            SocialAccountAdapter().validate_disconnect(account, accounts)
    else:
        SocialAccountAdapter().validate_disconnect(account, accounts)
