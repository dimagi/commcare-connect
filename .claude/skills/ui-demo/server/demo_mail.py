"""Email for demo recordings: every email the server sends shows up in a webmail-style inbox, so a demo can show it
arriving the way the recipient would see it.

EmailBackend prints each email to the console (output/server.log) like Django's console backend, and also saves it
as JSON in $UI_DEMO_MAIL_DIR (set by serve.sh). InboxView, served at /demo-inbox/ by demo_urls.py, reads those files
back. runner.mjs opens it after every step that sent an email. Django's file backend is no help here: it writes raw
MIME, several messages to a file.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from django.core.mail.backends.console import EmailBackend as ConsoleEmailBackend
from django.utils import timezone
from django.views.generic import TemplateView

from commcare_connect.users.models import User


class EmailBackend(ConsoleEmailBackend):
    def send_messages(self, email_messages):
        mail_dir = os.environ.get("UI_DEMO_MAIL_DIR")  # unset under reseed.sh, so seeding saves nothing
        if mail_dir:
            Path(mail_dir).mkdir(parents=True, exist_ok=True)
            for i, message in enumerate(email_messages):
                Path(mail_dir, f"{time.time_ns()}-{i}.json").write_text(json.dumps(as_dict(message)))
        return super().send_messages(email_messages)


class InboxView(TemplateView):
    """`?to=` picks whose inbox it is; with a message id in the URL, that email is open."""

    template_name = "ui_demo/inbox.html"

    def get_context_data(self, message_id=None, **kwargs):
        owner = self.request.GET.get("to", "")
        inbox = [m for m in saved_emails() if owner in m["to"]]
        user = User.objects.filter(email=owner).first()
        return super().get_context_data(
            owner=owner,
            owner_name=(user and user.name) or owner,
            inbox=inbox,
            opened=next((m for m in inbox if m["id"] == message_id), None),
            **kwargs,
        )


def as_dict(message):
    html = message.body if message.content_subtype == "html" else None
    for content, mimetype in getattr(message, "alternatives", []):
        if mimetype == "text/html":
            html = content
    return {
        "to": message.to,
        "subject": message.subject,
        "text": message.body,
        "html": html,
        "sent_at": timezone.now().isoformat(),
    }


def saved_emails():
    """Newest first. The HTML part goes into the inbox's iframe as sent, plus a <base target="_top"> so its links
    open in the whole window: the app refuses to load inside a frame (X_FRAME_OPTIONS = "DENY")."""
    mail_dir = os.environ.get("UI_DEMO_MAIL_DIR")
    emails = []
    for path in sorted(Path(mail_dir).glob("*.json"), reverse=True) if mail_dir else []:
        email = json.loads(path.read_text())
        emails.append(
            {
                **email,
                "id": path.stem,
                "sent_at": datetime.fromisoformat(email["sent_at"]),
                "html": email["html"] and with_top_links(email["html"]),
            }
        )
    return emails


def with_top_links(html):
    # inside <head>, not in front of the doctype, which would put the email in quirks mode
    head = re.search(r"<head[^>]*>", html, re.I)
    at = head.end() if head else 0
    return f'{html[:at]}<base target="_top">{html[at:]}'
