# Generated by Django 4.2.5 on 2024-03-05 07:23

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0032_blobmeta"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompletedWork",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
                        default="pending",
                        max_length=50,
                    ),
                ),
                ("last_modified", models.DateTimeField(auto_now=True)),
                ("entity_id", models.CharField(blank=True, max_length=255, null=True)),
                ("entity_name", models.CharField(blank=True, max_length=255, null=True)),
                ("reason", models.CharField(blank=True, max_length=300, null=True)),
                (
                    "opportunity_access",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="opportunity.opportunityaccess"),
                ),
                (
                    "payment_unit",
                    models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to="opportunity.paymentunit"),
                ),
            ],
        ),
        migrations.AddField(
            model_name="uservisit",
            name="completed_work",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, to="opportunity.completedwork"
            ),
        ),
    ]