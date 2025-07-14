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

You have been invited to join {membership.organization.name} on Commcare Connect by {host_user.name}.
The invite can be accepted by visiting the link.

{invite_url}

Thank You,
Commcare Connect"""
    send_mail(
        subject=f"{host_user.name} has invite you to join '{membership.organization.name}' on CommCare Connect",
        message=message,
        recipient_list=[membership.user.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
    )


@celery_app.task()
def add_credential_task(org_pk: int, credential: str, users: list[str]):
    # Disable temporarily until the new credentials system is ready
    # org = Organization.objects.get(pk=org_pk)
    # connect_id_client.add_credential(org, credential, users)
    pass
