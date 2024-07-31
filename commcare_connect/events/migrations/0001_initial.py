# Generated by Django 4.2.5 on 2024-07-13 14:45

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("opportunity", "0044_opportunityverificationflags"),
    ]

    operations = [
        migrations.CreateModel(
            name="Event",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date_created", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("invite_sent", "Invite Sent"),
                            ("records_approved", "Records Approved"),
                            ("records_flagged", "Records Flagged"),
                            ("records_rejected", "Records Rejected"),
                            ("payment_approved", "Payment Approved"),
                            ("payment_accrued", "Payment Accrued"),
                            ("payment_transferred", "Payment Transferred"),
                            ("notifications_sent", "Notifications Sent"),
                            ("additional_budget_added", "Additional Budget Added"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "opportunity",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.PROTECT, to="opportunity.opportunity"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
        ),
    ]