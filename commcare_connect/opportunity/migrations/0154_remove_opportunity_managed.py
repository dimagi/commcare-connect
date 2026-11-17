from django.db import migrations, models


class Migration(migrations.Migration):
    """Remove Opportunity.managed from the model but keep the column for one release.

    Each new container runs migrations on boot while the previous release keeps serving, and that
    release still selects and inserts `managed`. So the column stays: it gets a database default,
    because the new code no longer supplies a value on insert, and only Django's state forgets it.
    Drop the column in a later release, once no running code knows about it.
    """

    dependencies = [
        ("opportunity", "0153_deliverunit_display_name_paymentunit_display_name"),
    ]

    operations = [
        migrations.AlterField(
            model_name="opportunity",
            name="managed",
            field=models.BooleanField(default=False, db_default=False),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="opportunity", name="managed"),
            ],
        ),
    ]
