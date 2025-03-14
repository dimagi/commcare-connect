# Generated by Django 4.2.5 on 2023-09-29 07:40

from django.db import migrations, models
import django.db.models.deletion
from django.utils.text import slugify


def migrate_deliver_form_to_deliver_unit(apps, schema_editor):
    DeliverForm = apps.get_model("opportunity.DeliverForm")
    DeliverUnit = apps.get_model("opportunity.DeliverUnit")
    UserVisit = apps.get_model("opportunity.UserVisit")
    forms_to_units = {}
    for form in DeliverForm.objects.all():
        unit = DeliverUnit.objects.create(
            app=form.app,
            slug=slugify(form.xmlns),
            name=form.name,
        )
        forms_to_units[form.id] = unit.id

    for form_id, unit_id in forms_to_units.items():
        UserVisit.objects.filter(deliver_form_id=form_id).update(deliver_unit_id=unit_id)


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0020_payment"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeliverUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=100)),
                ("name", models.CharField(max_length=255)),
                (
                    "app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliver_units",
                        to="opportunity.commcareapp",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="uservisit",
            name="deliver_unit",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to="opportunity.deliverunit"),
        ),
        migrations.RunPython(migrate_deliver_form_to_deliver_unit, reverse_code=migrations.RunPython.noop, hints={"run_on_secondary": False}),
        migrations.AddField(
            model_name="uservisit",
            name="entity_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="uservisit",
            name="entity_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        # drop null=True from deliver_unit
        migrations.AlterField(
            model_name="uservisit",
            name="deliver_unit",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="opportunity.deliverunit"),
        ),
        migrations.RemoveField(
            model_name="uservisit",
            name="deliver_form",
        ),
        migrations.DeleteModel(
            name="DeliverForm",
        ),
    ]
