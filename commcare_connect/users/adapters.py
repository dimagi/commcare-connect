import ipaddress
import logging
from typing import Any

from allauth.account import app_settings as account_settings
from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.utils import filter_users_by_email, user_email
from allauth.core import context as allauth_context
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.core.internal import ratelimit
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.base import AuthProcess
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.http import HttpRequest
from django.shortcuts import redirect
from django.utils.translation import gettext as _

from commcare_connect.commcarehq_provider.provider import CommcareHQProvider
from commcare_connect.utils.middleware import is_safe_redirect_url

logger = logging.getLogger(__name__)


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest):
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def get_client_ip(self, request: HttpRequest) -> str:
        """Keeps login and signup working when ALLAUTH_TRUSTED_PROXY_COUNT overstates the proxy depth.

        allauth raises ImproperlyConfigured when X-Forwarded-For has fewer hops than the setting trusts.
        That is a deployment fact we can't verify from code, so fall back to REMOTE_ADDR (the nearest
        proxy, i.e. one shared rate-limit bucket) and report it to Sentry rather than 500.
        """
        try:
            return super().get_client_ip(request)
        except ImproperlyConfigured:
            remote_addr = request.META.get("REMOTE_ADDR")
            forwarded_for = request.headers.get("x-forwarded-for")
            # The address kinds go in the message itself: Sentry and the log formatters hide the addresses,
            # but "public, private" is enough to work out the real proxy depth and holds no personal data.
            hop_kinds = describe_forwarded_hops(forwarded_for)
            logger.error(
                "X-Forwarded-For has %d hop(s) (%s), fewer than ALLAUTH_TRUSTED_PROXY_COUNT; "
                "rate limiting on REMOTE_ADDR (%s)",
                len(hop_kinds),
                ", ".join(hop_kinds) or "empty",
                address_kind(remote_addr),
                extra={"x_forwarded_for": forwarded_for, "remote_addr": remote_addr, "path": request.path},
            )
            if not remote_addr:
                raise PermissionDenied("Unable to determine client IP address")
            return remote_addr

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        """Caps how much account mail one address can receive, whatever the trigger.

        allauth's own limits are per action, so confirmation resends, "account already exists" mails and
        password resets each get their own allowance. The ``send_mail`` entry in ACCOUNT_RATE_LIMITS is
        one budget for all of them, per address. Skipping is silent to the requester, like allauth's own
        handling of the confirmation cooldown: links already sent stay valid.
        """
        usage = self._consume_mail_budget(email)
        if usage is None:
            logger.info("Not sending %s to %s: ACCOUNT_RATE_LIMITS['send_mail'] reached", template_prefix, email)
            return
        try:
            super().send_mail(template_prefix, email, context)
        except Exception:
            if usage:
                usage.rollback()  # a failed delivery must not eat into the address's allowance
            raise

    @staticmethod
    def _consume_mail_budget(email: str) -> ratelimit.RateLimitUsage | None:
        """Takes one unit of the address's ``send_mail`` budget; None when the budget is spent.

        Outside a request (commands, tasks) there is nothing to throttle, so an empty usage is returned.
        """
        request = allauth_context.request
        if request is None:
            return ratelimit.RateLimitUsage(usage=[])
        # allauth's public consume() exempts GET requests, but the social-login callback that completes
        # a signup is a GET and sends a confirmation mail, so count those too.
        return ratelimit.consume(
            request,
            app="allauth.account",
            config=account_settings.RATE_LIMITS,
            action="send_mail",
            key=mail_budget_key(email),
            limit_get=True,
        )

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
        return next_url if is_safe_redirect_url(request, next_url) else None

    def pre_social_login(self, request: HttpRequest, sociallogin: SocialLogin):
        if sociallogin.account.pk:
            # This exact SocialAccount has already signed in before; allow reauthentication.
            # (sociallogin.is_existing checks the resolved *user*, which can also become
            # true for a brand-new social account matched to an existing user by email --
            # exactly the case _reject_if_email_already_registered below needs to catch.)
            return
        if sociallogin.state.get("process") == AuthProcess.CONNECT:
            return  # linking a new provider account to an already-authenticated user is always allowed

        if sociallogin.account.provider != CommcareHQProvider.id:
            self._reject_non_hq_login(request)
        else:
            self._reject_if_email_already_registered(request, sociallogin)

    def _reject_non_hq_login(self, request: HttpRequest):
        messages.error(request, _("This account can only be connected, not used to sign in."))
        raise ImmediateHttpResponse(redirect("account_login"))

    def _reject_if_email_already_registered(self, request: HttpRequest, sociallogin: SocialLogin):
        email = user_email(sociallogin.user)
        if email and filter_users_by_email(email):
            messages.error(request, _("Unable to sign in with SSO. Please sign in with your email and password."))
            raise ImmediateHttpResponse(redirect("account_login"))

    def validate_disconnect(self, account: SocialAccount, accounts: list[SocialAccount]):
        if account.provider != CommcareHQProvider.id:
            return  # non-HQ providers (e.g. OCS) are never used to sign in, so always safe to disconnect

        # A user with no usable password almost certainly signed up via HQ, so HQ must stay
        # connected even if another provider (e.g. OCS) is also connected as a fallback.
        # Checked before super() so this message takes precedence over allauth's generic one.
        if not account.user.has_usable_password():
            raise ValidationError(_("You can't disconnect your CommCare HQ account because it's your sign-in method."))
        super().validate_disconnect(account, accounts)


def describe_forwarded_hops(forwarded_for: str | None) -> list[str]:
    """The kind of each X-Forwarded-For entry, in order, without the addresses themselves."""
    if not forwarded_for:
        return []
    return [address_kind(hop) for hop in forwarded_for.split(",")]


def address_kind(value: str | None) -> str:
    """ "public", "private" (RFC 1918, loopback, link-local) or "invalid" for something that isn't an IP."""
    try:
        address = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return "invalid"
    return "public" if address.is_global else "private"


GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def mail_budget_key(email: str) -> str:
    """The inbox an address reaches, so aliases of one mailbox share one mail budget.

    Gmail ignores dots and anything after a "+" in the local part, so a bomber could otherwise mint an
    unlimited number of distinct addresses for one victim. Other providers can route those variants to
    different mailboxes, so they are only lower-cased.
    """
    local, _, domain = email.strip().lower().rpartition("@")
    if domain in GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"
