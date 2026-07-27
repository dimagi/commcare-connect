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
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from commcare_connect.commcarehq_provider.provider import CommcareHQProvider


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest):
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def add_message(self, request, level, message_template, message_context=None, extra_tags=""):
        if message_template == "socialaccount/messages/account_connected.txt":
            level = messages.SUCCESS
        super().add_message(request, level, message_template, message_context, extra_tags)


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest, sociallogin: Any):
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def authentication_error(self, request, *args, **kwargs):
        messages.error(request, _("Could not connect your account. Please try again."))
        next_url = self._next_url_from_state(request)
        if not next_url:
            return
        raise ImmediateHttpResponse(redirect(next_url))

    @staticmethod
    def _next_url_from_state(request):
        oauth_state = request.session.get("socialaccount_state")
        if not oauth_state:
            return None
        next_url = oauth_state[0].get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return next_url
        return None

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin):
        if sociallogin.is_existing:
            return
        if sociallogin.account.provider == CommcareHQProvider.id:
            email = user_email(sociallogin.user)
            if not email:
                return
            if email_address_exists(email):
                messages.error(request, _("Unable to sign in with SSO. Please sign in with your email and password."))
                raise ImmediateHttpResponse(redirect("account_login"))
