# Generated by Django 4.2.5 on 2024-11-28 07:22

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0061_opportunityclaimlimit_end_date_paymentunit_end_date_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunityaccess",
            name="invited_date",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
    ]