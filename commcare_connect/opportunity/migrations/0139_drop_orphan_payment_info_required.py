from django.db import migrations


class Migration(migrations.Migration):
    """Drop the orphaned ``payment_info_required`` column.

    The field was introduced only on the staging lineage (old migration 0061) for
    the payment-phone-number review feature, which was later removed without ever
    merging to main. The column was intentionally left in place at removal time, but
    since the model no longer writes to it and it is ``NOT NULL`` with no DB-level
    default, every new Opportunity INSERT violates the not-null constraint.

    ``IF EXISTS`` keeps this safe on databases (main, fresh builds) where the column
    was never created.
    """

    dependencies = [
        ("opportunity", "0138_assignedtask_date_modified"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE opportunity_opportunity DROP COLUMN IF EXISTS payment_info_required;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
