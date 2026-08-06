from importlib import import_module

import pytest
from django.apps import apps

from commcare_connect.users.tests.factories import LLOEntityFactory, OrganizationFactory

# Migration module names start with a digit, so they can only be imported dynamically.
backfill_migration = import_module(
    "commcare_connect.organization.migrations.0013_backfill_organization_profile_from_entity"
)


@pytest.mark.django_db
class TestBackfillOrganizationProfileFromEntity:
    def test_org_receives_entity_profile(self):
        entity = LLOEntityFactory(short_name="WHO")
        org = OrganizationFactory(llo_entity=entity)

        backfill_migration.copy_entity_profile_to_organizations(apps, None)

        org.refresh_from_db()
        assert org.short_name == "WHO"

    def test_orgs_sharing_an_entity_each_get_a_copy(self):
        entity = LLOEntityFactory(short_name="SHARED")
        orgs = [OrganizationFactory(llo_entity=entity) for _ in range(3)]

        backfill_migration.copy_entity_profile_to_organizations(apps, None)

        for org in orgs:
            org.refresh_from_db()
            assert org.short_name == "SHARED"

    def test_org_without_entity_is_untouched(self):
        org = OrganizationFactory(llo_entity=None, short_name="KEEP")

        backfill_migration.copy_entity_profile_to_organizations(apps, None)

        org.refresh_from_db()
        assert org.short_name == "KEEP"
