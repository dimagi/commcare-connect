# Generated by Django 4.2.3 on 2023-08-16 09:21

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("organization", "0002_alter_organization_options_and_more"),
    ]

    operations = [
        # this models got out of sync when they were moved to the new app
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.AddField(
                model_name="organization",
                name="members",
                field=models.ManyToManyField(
                    related_name="organizations",
                    through="organization.UserOrganizationMembership",
                    to=settings.AUTH_USER_MODEL,
                ),
            ),
            migrations.AddField(
                model_name="userorganizationmembership",
                name="organization",
                field=models.ForeignKey(
                    default=None,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="memberships",
                    to="organization.organization",
                ),
                preserve_default=False,
            ),
            migrations.AddField(
                model_name="userorganizationmembership",
                name="user",
                field=models.ForeignKey(
                    default=None,
                    on_delete=django.db.models.deletion.DO_NOTHING,
                    related_name="memberships",
                    to=settings.AUTH_USER_MODEL,
                ),
                preserve_default=False,
            ),
        ])
    ]