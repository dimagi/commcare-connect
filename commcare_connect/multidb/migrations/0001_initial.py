from django.db import migrations, models

INITIAL_MODEL_LABELS = [
    "opportunity.assessment",
    "opportunity.commcareapp",
    "opportunity.completedmodule",
    "opportunity.completedwork",
    "opportunity.country",
    "opportunity.currency",
    "opportunity.deliverunit",
    "opportunity.deliverunitflagrules",
    "opportunity.deliverytype",
    "opportunity.learnmodule",
    "opportunity.opportunity",
    "opportunity.opportunityaccess",
    "opportunity.opportunityclaim",
    "opportunity.opportunityclaimlimit",
    "opportunity.opportunityverificationflags",
    "opportunity.payment",
    "opportunity.paymentinvoice",
    "opportunity.paymentunit",
    "opportunity.uservisit",
    "organization.lloentity",
    "organization.organization",
    "program.program",
    "reports.useranalyticsdata",
    "users.connectiduserlink",
    "users.user",
    "users.usercredential",
]


def populate_initial_tables(apps, schema_editor):
    SupersetReplicatedTable = apps.get_model("multidb", "SupersetReplicatedTable")
    SupersetReplicatedTable.objects.bulk_create(
        [SupersetReplicatedTable(model_label=label) for label in INITIAL_MODEL_LABELS],
        ignore_conflicts=True,
    )


def clear_initial_tables(apps, schema_editor):
    SupersetReplicatedTable = apps.get_model("multidb", "SupersetReplicatedTable")
    SupersetReplicatedTable.objects.filter(model_label__in=INITIAL_MODEL_LABELS).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SupersetReplicatedTable",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_by", models.CharField(max_length=255)),
                ("modified_by", models.CharField(max_length=255)),
                ("date_created", models.DateTimeField(auto_now_add=True)),
                ("date_modified", models.DateTimeField(auto_now=True)),
                (
                    "model_label",
                    models.CharField(
                        help_text="The Django model identifier in the form 'app_label.model_name'.",
                        max_length=255,
                        unique=True,
                    ),
                ),
                ("config_synced_at", models.DateTimeField(blank=True, null=True)),
                ("config_sync_error", models.TextField(blank=True)),
            ],
            options={"ordering": ["model_label"]},
        ),
        migrations.RunPython(
            populate_initial_tables,
            reverse_code=clear_initial_tables,
            hints={"run_on_secondary": False},
        ),
    ]
