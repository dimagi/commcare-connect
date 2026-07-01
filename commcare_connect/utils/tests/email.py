"""Helpers for end-to-end email testing.

The app sends mail through the ``send_mail_async`` Celery task, which is NOT
eager in the test settings, so ``.delay()`` would normally just enqueue and the
message would never reach ``django.core.mail.outbox``. Wrap the action that
triggers an email in :func:`tasks_run_eagerly` so the task executes inline and
the real message lands in the outbox, then assert on it / follow its links.

Example::

    from commcare_connect.utils.tests.email import (
        get_sole_email, get_email_link, tasks_run_eagerly,
    )

    with tasks_run_eagerly():
        client.post(invite_url, {"email": "x@example.com", "role": "member"})

    email = get_sole_email(to="x@example.com")
    link = get_email_link(email, must_contain="/organization/invite/")
    response = client.get(link, follow=True)   # actually walk the emailed link
"""

import contextlib
import re

from django.core import mail

from config.celery_app import app as celery_app

URL_RE = re.compile(r"https?://[^\s\"'<>)]+")


@contextlib.contextmanager
def tasks_run_eagerly():
    """Run Celery tasks synchronously (so emailed messages reach mail.outbox)."""
    prev_eager = celery_app.conf.task_always_eager
    prev_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = prev_eager
        celery_app.conf.task_eager_propagates = prev_propagate


def get_sole_email(to=None):
    """Return the single message in the outbox, optionally asserting its recipient."""
    assert len(mail.outbox) == 1, f"expected exactly 1 email, got {len(mail.outbox)}"
    email = mail.outbox[0]
    if to is not None:
        assert email.to == [to], f"expected recipient {to!r}, got {email.to!r}"
    return email


def extract_links(text):
    """Return all http(s) URLs found in an email body."""
    return URL_RE.findall(text)


def get_email_link(email, must_contain=None):
    """Return the single link in an email body, optionally filtered by a substring."""
    links = extract_links(email.body)
    if must_contain is not None:
        links = [link for link in links if must_contain in link]
    assert len(links) == 1, f"expected exactly 1 matching link, got {links!r}"
    return links[0]
