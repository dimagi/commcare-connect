# Generated by Django 4.2.3 on 2023-08-10 14:00

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_organization_members"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="organization",
            table="organization_organization",
        ),
        migrations.AlterModelTable(
            name="userorganizationmembership",
            table="organization_membership",
        ),
    ]
