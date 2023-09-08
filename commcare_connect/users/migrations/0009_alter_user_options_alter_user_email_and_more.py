# Generated by Django 4.2.3 on 2023-09-06 06:45

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0008_connectiduserlink"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="user",
            options={},
        ),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(blank=True, max_length=254, null=True, verbose_name="email address"),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                condition=models.Q(("email__isnull", False)), fields=("email",), name="unique_user_email"
            ),
        ),
    ]