# Generated by Django 4.2.5 on 2025-07-14 11:21

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0080_create_prod_hq_server"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="opportunity",
            name="budget_per_visit",
        ),
        migrations.RemoveField(
            model_name="opportunity",
            name="daily_max_visits_per_user",
        ),
        migrations.RemoveField(
            model_name="opportunity",
            name="max_visits_per_user",
        ),
    ]
