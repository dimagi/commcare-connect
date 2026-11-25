import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0011_organization_funder"),
        ("program", "0015_rename_managedopportunity_program_to_program_old"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="funder",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="funded_programs",
                to="organization.organization",
            ),
        ),
        migrations.AddField(
            model_name="program",
            name="watchers",
            field=models.ManyToManyField(
                blank=True,
                related_name="watched_programs",
                to="organization.organization",
            ),
        ),
    ]
