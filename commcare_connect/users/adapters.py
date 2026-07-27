from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.utils import user_email
from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.base import AuthProcess
from allauth.utils import email_address_exists
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from commcare_connect.commcarehq_provider.provider import CommcareHQProvider


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest):
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest, sociallogin: Any):
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin):
        if sociallogin.is_existing:
            return
        if sociallogin.state.get("process") == AuthProcess.CONNECT:
            return  # linking a new provider account to an already-authenticated user
        email = user_email(sociallogin.user)
        if not email:
            return
        if email_address_exists(email):
            messages.error(request, _("Unable to sign in with SSO. Please sign in with your email and password."))
            raise ImmediateHttpResponse(redirect("account_login"))

    def validate_disconnect(self, account: SocialAccount, accounts: list[SocialAccount]):
        super().validate_disconnect(account, accounts)
        # A user with no usable password almost certainly signed up via HQ, so HQ must stay
        # connected even if another provider (e.g. OCS) is also connected as a fallback.
        if account.provider == CommcareHQProvider.id and not account.user.has_usable_password():
            raise ValidationError(_("You can't disconnect your CommCare HQ account because it's your sign-in method."))
