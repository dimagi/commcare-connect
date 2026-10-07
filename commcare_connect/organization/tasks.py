import uuid

from allauth.utils import build_absolute_uri
from django.core.files.base import ContentFile
from django.http import QueryDict
from django.template.loader import render_to_string
from django.urls import reverse
from django_tables2.export import TableExport

from commcare_connect.organization.filters import ContactFilterSet
from commcare_connect.organization.models import Contact, OrganizationInvite, UserOrganizationMembership
from commcare_connect.organization.tables import ContactExportTable
from commcare_connect.utils.celery import get_export_storage
from commcare_connect.utils.tasks import send_mail_async
from config import celery_app


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


@celery_app.task()
def export_contacts(query_string, user_id, export_format):
    """Saves the unarchived contacts matching the listing filters in `query_string`, returning the file name.

    `user_id` is not used here: it is kept in the task's stored arguments so only the requester can download the file.
    """
    contacts = Contact.objects.filter(is_archived=False).select_related("organization").order_by("name")
    filterset = ContactFilterSet(QueryDict(query_string), queryset=contacts)
    content = TableExport(export_format, ContactExportTable(filterset.qs)).export()
    return get_export_storage().save(f"contacts-{uuid.uuid4()}.{export_format}", ContentFile(content))
