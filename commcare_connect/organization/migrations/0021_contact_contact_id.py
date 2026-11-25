import uuid

from django.db import migrations, models


def populate_contact_id(apps, schema_editor):
    Contact = apps.get_model("organization", "Contact")
    for contact in Contact.objects.filter(contact_id__isnull=True):
        contact.contact_id = uuid.uuid4()
        contact.save(update_fields=["contact_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("organization", "0020_contact"),
    ]

    operations = [
        # Added as nullable with no default first: a default would be computed once and shared by
        # every existing contact, so each one gets its own UUID before the unique constraint applies.
        migrations.AddField(
            model_name="contact",
            name="contact_id",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_contact_id, migrations.RunPython.noop, hints={"run_on_secondary": False}),
        migrations.AlterField(
            model_name="contact",
            name="contact_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
