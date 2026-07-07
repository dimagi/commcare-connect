from allauth.utils import build_absolute_uri
from django.urls import reverse

from commcare_connect.organization.models import OrganizationInvite
from commcare_connect.users.models import User
from commcare_connect.utils.tasks import send_mail_async


def send_org_invite(invite_id, host_user_id):
    invite = OrganizationInvite.objects.get(pk=invite_id)
    host_user = User.objects.get(pk=host_user_id)
    if not invite.email:
        return
    location = reverse("organization:accept_invite", args=(invite.organization.slug, invite.token))
    invite_url = build_absolute_uri(None, location)
    message = f"""Hi,

You have been invited to join {invite.organization.name} on Connect by {host_user.name}.
Visit the link below to accept. This invitation expires in {OrganizationInvite.EXPIRY_DAYS} days.

{invite_url}

Thank You,
Connect

This inbox is not monitored. Please do not respond to this email."""

    send_mail_async.delay(
        subject=f"{host_user.name} has invited you to join '{invite.organization.name}' on Connect",
        message=message,
        recipient_list=[invite.email],
    )
