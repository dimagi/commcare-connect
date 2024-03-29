# Generated by Django 4.2.3 on 2023-08-18 22:15

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("opportunity", "0012_uservisit_opportunity_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="HQApiKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("api_key", models.CharField(max_length=50, unique=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name="opportunity",
            name="api_key",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.DO_NOTHING, to="opportunity.hqapikey"
            ),
        ),
    ]
