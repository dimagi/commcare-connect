# Generated by Django 4.2.5 on 2023-10-03 19:24

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0019_alter_opportunityclaim_date_claimed"),
    ]

    operations = [
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.PositiveIntegerField()),
                ("date_paid", models.DateTimeField(auto_now_add=True)),
                (
                    "opportunity_access",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        to="opportunity.opportunityaccess",
                    ),
                ),
            ],
        ),
    ]