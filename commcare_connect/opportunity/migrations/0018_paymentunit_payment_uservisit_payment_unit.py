# Generated by Django 4.2.5 on 2023-09-25 16:36

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0017_alter_opportunityaccess_user_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.PositiveIntegerField()),
                ("name", models.TextField()),
                ("description", models.TextField()),
            ],
        ),
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
        migrations.AddField(
            model_name="uservisit",
            name="payment_unit",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.DO_NOTHING, to="opportunity.paymentunit"
            ),
        ),
    ]