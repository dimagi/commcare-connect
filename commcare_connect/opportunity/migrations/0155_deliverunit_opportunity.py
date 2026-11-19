import django.db.models.deletion
from django.db import migrations, models

# Give each deliver unit the opportunity that uses it today. When several opportunities have used
# its app, that is the opportunity its payment unit belongs to, or failing that the latest one.
# Units on apps no opportunity uses stay NULL. See CCCT-3057.
BACKFILL_SQL = """
UPDATE opportunity_deliverunit du
SET opportunity_id = COALESCE(
    (
        SELECT pu.opportunity_id
        FROM opportunity_paymentunit pu
        JOIN opportunity_opportunity o ON o.id = pu.opportunity_id
        WHERE pu.id = du.payment_unit_id AND o.deliver_app_id = du.app_id
    ),
    (
        SELECT o.id
        FROM opportunity_opportunity o
        WHERE o.deliver_app_id = du.app_id
        ORDER BY o.id DESC
        LIMIT 1
    )
)
WHERE du.opportunity_id IS NULL
"""


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0154_remove_opportunity_managed"),
    ]

    operations = [
        migrations.AddField(
            model_name="deliverunit",
            name="opportunity",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deliver_units",
                to="opportunity.opportunity",
            ),
        ),
        # The secondary database receives the backfilled rows by replication.
        migrations.RunSQL(BACKFILL_SQL, migrations.RunSQL.noop, hints={"run_on_secondary": False}),
        migrations.AddConstraint(
            model_name="deliverunit",
            constraint=models.UniqueConstraint(
                fields=("opportunity", "app", "slug"), name="unique_deliver_unit_per_opportunity"
            ),
        ),
    ]
