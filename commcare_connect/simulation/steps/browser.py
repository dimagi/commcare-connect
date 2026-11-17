"""A logged-in browser session, so web steps send the same requests the pages do."""

import datetime
import re
from html import unescape

from django.conf import settings
from django.contrib.messages import get_messages
from django.test import Client

SESSION_LIFETIME = datetime.timedelta(days=365)
HTMX_DONE_HEADERS = ("HX-Redirect", "HX-Location", "HX-Refresh")
ERROR_PATTERNS = (
    re.compile(r'<ul class="errorlist[^"]*">(.*?)</ul>', re.S),
    # crispy-tailwind renders each field error as its own paragraph.
    re.compile(r'<p id="error_\d+_id_[^"]*"[^>]*>(.*?)</p>', re.S),
    re.compile(r'<p[^>]*class="[^"]*\berror\b[^"]*"[^>]*>(.*?)</p>', re.S),
)


class StepRequestError(RuntimeError):
    """A page refused a step's request, the way it would have refused the user."""


class Browser:
    """``user``'s session in the web app.

    A request succeeds when the page ends it the way a successful submit does: a redirect, or for htmx requests
    one of the headers that send the browser on. Anything else raises ``StepRequestError`` with the form errors
    and messages the user would have seen, so a step never carries on after the app said no.
    """

    def __init__(self, user):
        self.user = user
        # The client's default host, "testserver", is only allowed while tests run; local settings allow localhost.
        self.client = Client(SERVER_NAME=settings.ALLOWED_HOSTS[0])
        # Stands in for logging in on the login page; no password is needed or set.
        self.client.force_login(user)
        # Steps move the clock across weeks of history (Sim.at), so a session that expires on the usual schedule
        # would log the admin out part way through a scenario.
        session = self.client.session
        session.set_expiry(SESSION_LIFETIME)
        session.save()

    def get(self, url, **params):
        response = self._send(self.client.get, url, params)
        if response.status_code != 200:
            raise StepRequestError(self._describe("GET", url, response))
        return response

    def post(self, url, data=None, *, htmx=False, succeeded=None):
        """POST ``data`` to ``url`` as the page's form does; ``htmx`` sends the headers htmx adds.

        ``succeeded(response)`` replaces the redirect check, for the few views that answer a successful submit
        with a plain page or fragment.
        """
        headers = {"HX-Request": "true"} if htmx else {}
        response = self._send(self.client.post, url, data or {}, headers=headers)
        if not (succeeded or _redirected)(response):
            raise StepRequestError(self._describe("POST", url, response))
        return response

    def _send(self, method, url, *args, **kwargs):
        response = method(url, *args, **kwargs)
        response.step_messages = [str(message) for message in get_messages(response.wsgi_request)]
        # A step never loads the page a redirect points at, which is where a user would see these messages, so drop
        # them as that page would; otherwise they pile up and show in a later request's errors.
        self.client.cookies.pop("messages", None)
        session = self.client.session
        if session.pop("_messages", None) is not None:
            session.save()
        return response

    def _describe(self, method, url, response):
        details = _errors(response) or _text(response)[:300]
        return f"{method} {url} as {self.user.email}: {response.status_code}. {details}"


def _redirected(response):
    return 300 <= response.status_code < 400 or any(header in response.headers for header in HTMX_DONE_HEADERS)


def _errors(response):
    html = response.content.decode(errors="replace")
    errors = [_strip_tags(match) for pattern in ERROR_PATTERNS for match in pattern.findall(html)]
    return " ".join(response.step_messages + errors)


def _text(response):
    return _strip_tags(response.content.decode(errors="replace"))


def _strip_tags(html):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())
