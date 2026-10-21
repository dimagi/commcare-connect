from allauth.utils import build_absolute_uri
from django.template.loader import render_to_string
from django.urls import reverse

from commcare_connect.organization.models import OrganizationInvite, UserOrganizationMembership
from commcare_connect.utils.tasks import send_mail_async


def send_org_invite(invite_id):
    invite = OrganizationInvite.objects.select_related("organization", "invited_by").get(pk=invite_id)

    if invite.invited_by:
        inviter = invite.invited_by.name or invite.invited_by.email
    else:
        inviter = invite.organization.name

    location = reverse("organization:accept_invite", args=(invite.organization.slug, invite.token))
    context = {
        "invite": invite,
        "inviter": inviter,
        "invite_url": build_absolute_uri(None, location),
    }

    send_mail_async.delay(
        subject=f"{inviter} has invited you to join '{invite.organization.name}' on Connect",
        message=render_to_string("organization/email/invite.txt", context),
        recipient_list=[invite.email],
        html_message=render_to_string("organization/email/invite.html", context),
    )


def send_invite_accepted_notification(membership_id):
    membership = UserOrganizationMembership.objects.select_related("organization", "user").get(pk=membership_id)
    organization = membership.organization

    admin_emails = [
        email
        for email in organization.get_member_emails(role=UserOrganizationMembership.Role.ADMIN)
        if email != membership.user.email
    ]
    if not admin_emails:
        return

    member_name = membership.user.name or membership.user.email
    context = {"membership": membership, "member_name": member_name}
    send_mail_async.delay(
        subject=f"{member_name} joined '{organization.name}' on Connect",
        message=render_to_string("organization/email/invite_accepted.txt", context),
        recipient_list=admin_emails,
        html_message=render_to_string("organization/email/invite_accepted.html", context),
    )
