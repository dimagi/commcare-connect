import datetime

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery
from django.utils.text import slugify


def backfill_program_from_managed(apps, schema_editor):
    ManagedOpportunity = apps.get_model("program", "ManagedOpportunity")
    Opportunity = apps.get_model("opportunity", "Opportunity")
    Opportunity.objects.filter(managed=True).update(
        program_id=Subquery(
            ManagedOpportunity.objects.filter(pk=OuterRef("pk")).values("program_old_id")[:1]
        )
    )


def create_legacy_programs(apps, schema_editor):
    LLOEntity = apps.get_model("organization", "LLOEntity")
    Organization = apps.get_model("organization", "Organization")
    Program = apps.get_model("program", "Program")
    Opportunity = apps.get_model("opportunity", "Opportunity")
    DeliveryType = apps.get_model("opportunity", "DeliveryType")

    if not Opportunity.objects.filter(managed=False).exists():
        return

    today = datetime.date.today()
    start_date = datetime.date(2021, 1, 1)

    delivery_type = DeliveryType.objects.create(
        name="Non Managed Opportunity",
        slug=slugify("Non Managed Opportunity"),
        description="Auto-created delivery type for legacy non-managed opportunities by system",
    )

    llo_entity = LLOEntity.objects.create(name="Legacy Connect llo")

    test_org = Organization.objects.create(
        name="Legacy Test Opportunities",
        slug=slugify("Legacy Test Opportunities"),
        program_manager=True,
        llo_entity=llo_entity,
        created_by="system",
        modified_by="system",
    )
    real_org = Organization.objects.create(
        name="Legacy Real Opportunities",
        slug=slugify("Legacy Real Opportunities"),
        program_manager=True,
        llo_entity=llo_entity,
        created_by="system",
        modified_by="system",
    )

    test_program = Program.objects.create(
        name="Legacy Test Program",
        slug=slugify("Legacy Test Program"),
        description="Auto-created program for legacy test opportunities by system",
        delivery_type=delivery_type,
        budget=0,
        start_date=start_date,
        end_date=today,
        organization=test_org,
        created_by="system",
        modified_by="system",
    )
    real_program = Program.objects.create(
        name="Legacy Real Program",
        slug=slugify("Legacy Real Program"),
        description="Auto-created program for legacy non-test opportunities by system",
        delivery_type=delivery_type,
        budget=0,
        start_date=start_date,
        end_date=today,
        organization=real_org,
        created_by="system",
        modified_by="system",
    )

    Opportunity.objects.filter(managed=False, is_test=True).update(program=test_program)
    Opportunity.objects.filter(managed=False, is_test=False).update(program=real_program)



class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0133_alter_opportunityverificationflags_location"),
        ("program", "0015_rename_managedopportunity_program_to_program_old"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunity",
            name="program",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                to="program.program",
            ),
        ),
        migrations.RunPython(
            backfill_program_from_managed,
            migrations.RunPython.noop,
            hints={"run_on_secondary": False},
        ),
        migrations.RunPython(
            create_legacy_programs,
            migrations.RunPython.noop,
            hints={"run_on_secondary": False},
        ),
    ]
