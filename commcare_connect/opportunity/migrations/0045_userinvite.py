# Generated by Django 4.2.5 on 2024-05-30 06:37

from django.db import migrations, models
import django.db.models.deletion
from commcare_connect.opportunity.models import UserInviteStatus


def populate_user_invite(apps, schema_editor):
    OpportunityAccess = apps.get_model("opportunity.OpportunityAccess")
    UserInvite = apps.get_model("opportunity.UserInvite")
    print("Running UserInvite data migration")
    access_objects = OpportunityAccess.objects.all().select_related("user")
    for access in access_objects:
        if not access.user.phone_number:
            continue
        UserInvite.objects.get_or_create(
            opportunity_id=access.opportunity_id,
            phone_number=access.user.phone_number,
            opportunity_access=access,
            status=UserInviteStatus.accepted if access.accepted else UserInviteStatus.invited,
        )
    print(f"Created {len(access_objects)} user invites")


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0044_opportunityverificationflags"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserInvite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone_number", models.CharField(max_length=15)),
                ("message_sid", models.CharField(blank=True, max_length=50, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("sms_delivered", "SMS Delivered"),
                            ("sms_not_delivered", "SMS Not Delivered"),
                            ("accepted", "Accepted"),
                            ("invited", "Invited"),
                            ("not_found", "ConnectID Not Found"),
                        ],
                        default="invited",
                        max_length=50,
                    ),
                ),
                (
                    "opportunity",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="opportunity.opportunity"),
                ),
                (
                    "opportunity_access",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="opportunity.opportunityaccess",
                    ),
                ),
            ],
        ),
        migrations.RunPython(
            populate_user_invite,
            migrations.RunPython.noop,
            hints={"run_on_secondary": False}
        ),
    ]
