import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    # A separate migration from 0157's data changes: Postgres won't ALTER a table with deferred
    # foreign key checks still pending in the same transaction.
    dependencies = [
        ("opportunity", "0157_billed_entities"),
    ]

    operations = [
        migrations.AlterField(
            model_name="deliverunit",
            name="opportunity",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="deliver_units",
                to="opportunity.opportunity",
            ),
        ),
    ]
