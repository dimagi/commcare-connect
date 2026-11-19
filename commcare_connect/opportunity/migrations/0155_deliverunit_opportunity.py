import django.db.models.deletion
from django.db import migrations, models

# Give each deliver unit the opportunity that uses it today (CCCT-3057). Where several opportunities
# have used its app, that is, in order:
#   1. the opportunity its payment unit belongs to, if that opportunity still uses the app
#   2. the running opportunity on the app (active and not ended), whose forms resolve to the unit
#   3. the latest opportunity on the app
# Units on apps no opportunity uses stay NULL.
#
# The old schema allowed several units with the same app and slug. Where those would end up with
# the same opportunity, only one gets it: the one on the opportunity's own payment unit, then the
# one with most of its visits, then the oldest. The rest stay NULL, so the unique constraint can be
# added without deleting or merging anything.
BACKFILL_SQL = """
WITH owners AS (
    SELECT
        du.id,
        du.app_id,
        du.slug,
        COALESCE(
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
                    AND o.active
                    AND (o.end_date IS NULL OR o.end_date >= CURRENT_DATE)
                ORDER BY o.id DESC
                LIMIT 1
            ),
            (
                SELECT o.id
                FROM opportunity_opportunity o
                WHERE o.deliver_app_id = du.app_id
                ORDER BY o.id DESC
                LIMIT 1
            )
        ) AS opportunity_id,
        COALESCE(
            (SELECT pu.opportunity_id FROM opportunity_paymentunit pu WHERE pu.id = du.payment_unit_id), 0
        ) AS payment_unit_opportunity_id
    FROM opportunity_deliverunit du
    WHERE du.opportunity_id IS NULL
),
ranked AS (
    SELECT
        owners.id,
        owners.opportunity_id,
        ROW_NUMBER() OVER (
            PARTITION BY owners.opportunity_id, owners.app_id, owners.slug
            ORDER BY
                (owners.payment_unit_opportunity_id = owners.opportunity_id) DESC,
                (
                    SELECT COUNT(*)
                    FROM opportunity_uservisit uv
                    WHERE uv.deliver_unit_id = owners.id AND uv.opportunity_id = owners.opportunity_id
                ) DESC,
                owners.id
        ) AS rank
    FROM owners
    WHERE owners.opportunity_id IS NOT NULL
)
UPDATE opportunity_deliverunit du
SET opportunity_id = ranked.opportunity_id
FROM ranked
WHERE du.id = ranked.id AND ranked.rank = 1
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
