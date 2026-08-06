from django.db import migrations

# Profile fields that LLOEntity currently carries. The other Organization profile
# fields (countries, verified, website, ...) have no LLOEntity counterpart yet, so
# they keep their model defaults. Extend this tuple — and the countries copy in
# copy_entity_profile_to_organizations — when the upstream LLOEntity profile fields land.
ENTITY_PROFILE_FIELDS = ("short_name",)


def copy_entity_profile_to_organizations(apps, schema_editor):
    """Copy every linked LLOEntity's profile fields onto its organizations.

    Organizations sharing an entity each get their own copy of its values; the
    duplicates are deduplicated later by the organization-merge tooling.
    """
    Organization = apps.get_model("organization", "Organization")
    organizations = list(Organization.objects.filter(llo_entity__isnull=False).select_related("llo_entity"))
    for org in organizations:
        for field in ENTITY_PROFILE_FIELDS:
            setattr(org, field, getattr(org.llo_entity, field))
    Organization.objects.bulk_update(organizations, ENTITY_PROFILE_FIELDS, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0012_organization_profile_fields"),
    ]

    operations = [
        # Organization is logically replicated, so the secondary picks up the backfilled
        # values without re-running the data migration there.
        migrations.RunPython(
            copy_entity_profile_to_organizations, migrations.RunPython.noop, hints={"run_on_secondary": False}
        ),
    ]
