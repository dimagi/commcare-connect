# Generated by Django 4.2.5 on 2023-09-28 19:47

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0010_user_phone_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="connectiduserlink",
            name="domain",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
