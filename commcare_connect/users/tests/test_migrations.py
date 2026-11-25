from importlib import import_module

import pytest
from django.apps import apps
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import UserFactory

remove_migration = import_module("commcare_connect.users.migrations.0032_remove_all_org_profile_edit_access")


@pytest.mark.django_db
def test_retired_profile_edit_permission_is_deleted_with_its_grants():
    retired = Permission.objects.create(
        codename="all_org_profile_edit_access",
        name="Can view and edit the profile of any organization",
        content_type=ContentType.objects.get_for_model(User),
    )
    holder = UserFactory()
    holder.user_permissions.add(retired)

    remove_migration.delete_retired_permission(apps, schema_editor=None)

    assert not Permission.objects.filter(codename="all_org_profile_edit_access").exists()
    assert not User.objects.get(pk=holder.pk).has_perm("users.all_org_profile_edit_access")
