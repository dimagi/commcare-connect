# Generated by Django 4.2.3 on 2023-08-10 15:14

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("users", "0005_alter_organization_table_and_more"),
    ]

    run_before = [
        ("users", "0006_alter_organization_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Organization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_by", models.CharField(max_length=255)),
                ("modified_by", models.CharField(max_length=255)),
                ("date_created", models.DateTimeField(auto_now_add=True)),
                ("date_modified", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=255, unique=True)),
            ],
            options={
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="UserOrganizationMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[("admin", "Admin"), ("member", "Member")], default="member", max_length=20
                    ),
                ),
            ],
            options={
                "db_table": "organization_membership",
                "managed": False,
            },
        ),
    ]
