from allauth.account.signals import user_logged_in, user_signed_up
from django.dispatch import receiver
from django.shortcuts import redirect


@receiver(user_signed_up)
@receiver(user_logged_in)
def create_org_for_user(request, user, **kwargs):
    if not user.memberships.exists():
        return redirect("organization_create")
