from django.template.loader import render_to_string

from commcare_connect.solicitation.models import SolicitationInvitation
from commcare_connect.utils.tasks import send_mail_async


def send_solicitation_invite_emails(invitation_ids):
    """Emails each invited organization's members; one email per organization."""
    invitations = SolicitationInvitation.objects.select_related("solicitation__organization", "organization").filter(
        pk__in=invitation_ids
    )
    for invitation in invitations:
        recipient_emails = invitation.organization.get_member_emails()
        if not recipient_emails:
            continue

        # TODO: link to the solicitation once invited organizations can view it (CCCT-2618).
        context = {"solicitation": invitation.solicitation}
        send_mail_async.delay(
            subject=f"Invitation to apply: {invitation.solicitation.title}",
            message=render_to_string("solicitation/email/invitation.txt", context),
            recipient_list=recipient_emails,
            html_message=render_to_string("solicitation/email/invitation.html", context),
        )
