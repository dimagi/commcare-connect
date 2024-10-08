# Generated by Django 4.2.5 on 2024-09-18 07:07

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0055_opportunity_managed"),
    ]

    operations = [
        migrations.AddField(
            model_name="uservisit",
            name="justification",
            field=models.CharField(blank=True, max_length=300, null=True),
        ),
        migrations.AddField(
            model_name="uservisit",
            name="review_created_on",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="uservisit",
            name="review_status",
            field=models.CharField(
                choices=[("pending", "Pending Review"), ("agree", "Agree"), ("disagree", "Disagree")],
                default="pending",
                max_length=50,
            ),
        ),
    ]
