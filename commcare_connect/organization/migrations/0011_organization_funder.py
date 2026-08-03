from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0010_organizationinvite_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="funder",
            field=models.BooleanField(default=False),
        ),
    ]
