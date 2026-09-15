from django.contrib.auth.models import Permission
from django.test import Client

from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.users.models import User


def make_membership(organization, user, role):
    return UserOrganizationMembership.objects.create(organization=organization, user=user, role=role)


def grant_all_org_access(user):
    user.user_permissions.add(Permission.objects.get(codename="all_org_access"))
    return User.objects.get(pk=user.pk)  # re-fetch to clear the cached perm set


def check_basic_permissions(user, url, permission_codename, status_code=403):
    client = Client()

    # Anonymous → redirect
    response = client.get(url)
    assert response.status_code == 302
    assert "/accounts/login/" in response.url

    # Logged-in without permission → forbidden
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == status_code
    client.logout()

    # With permission → allowed
    perm = Permission.objects.get(codename=permission_codename)
    user.user_permissions.add(perm)

    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200
    client.logout()

    # Superuser → allowed
    user.user_permissions.remove(perm)
    user.is_superuser = True
    user.is_staff = True
    user.save()
    client.force_login(user)
    response = client.get(url)
    assert response.status_code == 200
    client.logout()
