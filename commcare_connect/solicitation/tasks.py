from django.template.loader import render_to_string

from commcare_connect.solicitation.models import SolicitationInvitation
from commcare_connect.utils.tasks import send_mail_async


def send_solicitation_invite_email(invitation_id):
    invitation = SolicitationInvitation.objects.select_related("solicitation__organization", "organization").get(
        pk=invitation_id
    )
    recipient_emails = invitation.organization.get_member_emails()
    if not recipient_emails:
        return

    # TODO: link to the solicitation once invited organizations can view it (CCCT-2618).
    context = {"solicitation": invitation.solicitation}
    send_mail_async.delay(
        subject=f"Invitation to apply: {invitation.solicitation.title}",
        message=render_to_string("solicitation/email/invitation.txt", context),
        recipient_list=recipient_emails,
        html_message=render_to_string("solicitation/email/invitation.html", context),
    )
