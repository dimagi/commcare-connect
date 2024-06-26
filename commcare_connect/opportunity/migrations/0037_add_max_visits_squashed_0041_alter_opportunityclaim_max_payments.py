# Generated by Django 4.2.5 on 2024-04-16 11:48

import datetime
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("opportunity", "0036_payment_confirmation_date_payment_confirmed"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentunit",
            name="max_daily",
            field=models.IntegerField(null=True),
        ),
        migrations.AddField(
            model_name="paymentunit",
            name="max_total",
            field=models.IntegerField(null=True),
        ),
        migrations.CreateModel(
            name="OpportunityClaimLimit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("max_visits", models.IntegerField()),
                (
                    "opportunity_claim",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="opportunity.opportunityclaim"),
                ),
                (
                    "payment_unit",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="opportunity.paymentunit"),
                ),
            ],
        ),
        migrations.AddField(
            model_name="opportunity",
            name="start_date",
            field=models.DateField(default=datetime.date.today, null=True),
        ),
        migrations.AddField(
            model_name="uservisit",
            name="is_trial",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="opportunityclaim",
            name="max_payments",
            field=models.IntegerField(null=True),
        ),
    ]
