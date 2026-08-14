from django.db import migrations

from commcare_connect.utils.itertools import batched

# Profile fields that LLOEntity currently carries. The other Organization profile
# fields (countries, verified, website, ...) have no LLOEntity counterpart yet, so
# they keep their model defaults. Extend this tuple — and the countries copy in
# copy_entity_profile_to_organizations — when the upstream LLOEntity profile fields land.
ENTITY_PROFILE_FIELDS = ("short_name",)

BATCH_SIZE = 500


def copy_entity_profile_to_organizations(apps, schema_editor):
    """Copy every linked LLOEntity's profile fields onto its organizations.

    Organizations sharing an entity each get their own copy of its values; the
    duplicates are deduplicated later by the organization-merge tooling.

    Rows are streamed and updated a batch at a time so neither the fetched
    organizations nor the prepared UPDATE clauses grow with the table size.
    """
    Organization = apps.get_model("organization", "Organization")
    linked_organizations = (
        Organization.objects.filter(llo_entity__isnull=False)
        .select_related("llo_entity")
        .iterator(chunk_size=BATCH_SIZE)
    )
    for batch in batched(linked_organizations, BATCH_SIZE):
        for org in batch:
            for field in ENTITY_PROFILE_FIELDS:
                setattr(org, field, getattr(org.llo_entity, field))
        Organization.objects.bulk_update(batch, ENTITY_PROFILE_FIELDS)


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0012_primarysector_organization_contact_emails_and_more"),
    ]

    operations = [
        # Organization is logically replicated, so the secondary picks up the backfilled
        # values without re-running the data migration there.
        migrations.RunPython(
            copy_entity_profile_to_organizations, migrations.RunPython.noop, hints={"run_on_secondary": False}
        ),
    ]
