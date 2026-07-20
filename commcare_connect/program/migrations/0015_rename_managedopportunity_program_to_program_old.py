from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("program", "0014_create_pm_invoice_review_reminder_periodic_task"),
    ]

    operations = [
        migrations.RenameField(
            model_name="managedopportunity",
            old_name="program",
            new_name="program_old",
        ),
    ]
