# Generated by Django 4.2.3 on 2023-09-08 07:18

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0015_remove_opportunityaccess_date_claimed"),
    ]

    operations = [
        migrations.CreateModel(
            name="OpportunityClaim",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date_claimed", models.DateField(auto_created=True)),
                ("max_payments", models.IntegerField()),
                ("end_date", models.DateField()),
                (
                    "opportunity_access",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, to="opportunity.opportunityaccess"
                    ),
                ),
            ],
        ),
    ]
