# Generated by Django 4.2.5 on 2025-01-07 04:24

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0062_opportunityaccess_invited_date"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="uservisit",
            constraint=models.UniqueConstraint(
                fields=("xform_id", "entity_id", "deliver_unit"), name="unique_xform_entity_deliver_unit"
            ),
        ),
    ]