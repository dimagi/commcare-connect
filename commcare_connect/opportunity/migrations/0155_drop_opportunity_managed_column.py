from django.db import migrations


class Migration(migrations.Migration):
    """Drop the `managed` column that 0154 kept for one release.

    0154 removed `Opportunity.managed` from Django's state but left the column in place, because the
    previous release still selected it while the new containers migrated. That release is gone, so
    no running code reads the column any more. Django's state has no such field, so this is SQL only.
    """

    dependencies = [
        ("opportunity", "0154_remove_opportunity_managed"),
    ]

    operations = [
        migrations.RunSQL(
            'ALTER TABLE "opportunity_opportunity" DROP COLUMN IF EXISTS "managed"',
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
