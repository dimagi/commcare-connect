import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F, OuterRef, Subquery


def backfill_supervising_organization(apps, schema_editor):
    Opportunity = apps.get_model("opportunity", "Opportunity")
    Program = apps.get_model("program", "Program")
    # Program-linked (managed) opportunities inherit the program's owning organization.
    Opportunity.objects.filter(program__isnull=False, supervising_organization__isnull=True).update(
        supervising_organization_id=Subquery(
            Program.objects.filter(pk=OuterRef("program_id")).values("organization_id")[:1]
        )
    )
    # Any remaining opportunities (no program) supervise themselves.
    Opportunity.objects.filter(supervising_organization__isnull=True).update(
        supervising_organization_id=F("organization_id")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0010_organizationinvite_and_more"),
        ("opportunity", "0139_uservisit_status_modified_date_and_review_status"),
        ("program", "0015_rename_managedopportunity_program_to_program_old"),
    ]

    operations = [
        # Step 1: add the column as nullable so it can be populated on existing rows.
        migrations.AddField(
            model_name="opportunity",
            name="supervising_organization",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="supervised_opportunities",
                to="organization.organization",
            ),
        ),
        # Step 2: backfill from the program's owning organization.
        migrations.RunPython(
            backfill_supervising_organization,
            migrations.RunPython.noop,
            hints={"run_on_secondary": False},
        ),
        # Step 3: enforce non-null now that every row has a value.
        migrations.AlterField(
            model_name="opportunity",
            name="supervising_organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="supervised_opportunities",
                to="organization.organization",
            ),
        ),
    ]
