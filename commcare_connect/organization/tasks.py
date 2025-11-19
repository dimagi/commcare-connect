from allauth.utils import build_absolute_uri
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.users.models import User
from config import celery_app


@celery_app.task()
def send_org_invite(membership_id, host_user_id):
    membership = UserOrganizationMembership.objects.get(pk=membership_id)
    host_user = User.objects.get(pk=host_user_id)
    if not membership.user.email:
        return
    location = reverse("organization:accept_invite", args=(membership.organization.slug, membership.invite_id))
    invite_url = build_absolute_uri(None, location)
    message = f"""Hi,

You have been invited to join {membership.organization.name} on Connect by {host_user.name}.
The invite can be accepted by visiting the link.

{invite_url}

Thank You,
Connect"""
    send_mail(
        subject=f"{host_user.name} has invite you to join '{membership.organization.name}' on Connect",
        message=message,
        recipient_list=[membership.user.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
    )
