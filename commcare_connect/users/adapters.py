from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.utils import user_email
from allauth.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialLogin
from allauth.utils import email_address_exists
from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest
from django.shortcuts import redirect
from django.utils.translation import gettext as _


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest):
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest, sociallogin: Any):
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin):
        if sociallogin.is_existing:
            return
        email = user_email(sociallogin.user)
        if not email:
            return
        if email_address_exists(email):
            messages.error(
                request,
                _("An account with the email %(email)s already exists. Please sign in with your email and password.")
                % {"email": email},
            )
            raise ImmediateHttpResponse(redirect("account_login"))
