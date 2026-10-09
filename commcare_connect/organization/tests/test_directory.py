import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory


@pytest.fixture
def user_with_directory_access(db):
    user = UserFactory()
    user.user_permissions.add(Permission.objects.get(codename="workspace_entity_management_access"))
    return user


@pytest.mark.django_db
class TestOrganizationListAccess:
    def test_requires_permission(self, client, user):
        client.force_login(user)
        assert client.get(reverse("organization_directory:list")).status_code == 403

    def test_lists_for_permitted_user(self, client, user_with_directory_access):
        org = OrganizationFactory()
        client.force_login(user_with_directory_access)

        response = client.get(reverse("organization_directory:list"))

        assert response.status_code == 200
        assert list(response.context["table"].data) == [org]
