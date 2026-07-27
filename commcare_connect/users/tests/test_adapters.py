import pytest
from allauth.account.models import EmailAddress
from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.base import AuthProcess
from django.core.exceptions import ValidationError
from django.test import RequestFactory, override_settings

from commcare_connect.users.adapters import AccountAdapter, SocialAccountAdapter


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
def test_connect_bypasses_email_exists_guard_for_any_provider(user, rf, provider):
    # `user` fixture already exists with an email, so email_address_exists() would be True.
    # Connecting a new provider account to an already-authenticated user should never be
    # blocked by that check — it's only meant to guard against SSO *login* hijacking an
    # existing account.
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    request = rf.get("/")
    sociallogin = _sociallogin(user, provider=provider, process=AuthProcess.CONNECT)

    # Should NOT raise.
    SocialAccountAdapter().pre_social_login(request, sociallogin)


@pytest.mark.parametrize("provider", ["ocs", "commcarehq"])
@pytest.mark.django_db
def test_login_process_still_raises_on_existing_email(user, rf, provider):
    # The `user` fixture does not create an allauth EmailAddress row, which is what
    # email_address_exists() checks. Create one explicitly so the guard is genuinely exercised.
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
        ("ocs", False, None, True),  # default allauth guard: sole account, no password
        ("commcarehq", False, None, True),  # same default guard applies to HQ too
        ("commcarehq", False, "ocs", True),  # HQ-specific carve-out: OCS as fallback isn't enough
        ("commcarehq", True, None, False),  # has a password -> allowed
        ("ocs", False, "commcarehq", False),  # carve-out must not leak onto other providers
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
