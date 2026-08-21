from types import SimpleNamespace

from django.contrib.auth.models import Permission
from django.test import Client

from commcare_connect.organization.models import UserOrganizationMembership


class StubRequest:
    """A stand-in for an HttpRequest carrying only what the access gates read.

    Passing `program` pre-seeds `_cached_program`, which short-circuits program
    resolution so the access matrix can be tested without URL routing.
    """

    def __init__(
        self,
        user,
        org=None,
        membership=None,
        program=None,
        opportunity=None,
        resolver_kwargs=None,
        app_names=("program",),
    ):
        self.user = user
        self.org = org
        self.org_membership = membership
        if program is not None:
            self._cached_program = program
        if opportunity is not None:
            self.opportunity = opportunity
        if resolver_kwargs is not None:
            self.resolver_match = SimpleNamespace(kwargs=resolver_kwargs, app_names=list(app_names))


def make_membership(organization, user, role):
    return UserOrganizationMembership.objects.create(organization=organization, user=user, role=role)


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
