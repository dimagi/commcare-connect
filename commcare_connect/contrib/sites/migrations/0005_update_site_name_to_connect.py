from django.conf import settings
from django.db import migrations


def update_site_name_forward(apps, schema_editor):
    """Update site name from 'CommCare Connect' to 'Connect'."""
    Site = apps.get_model("sites", "Site")
    Site.objects.filter(id=settings.SITE_ID).update(name="Connect")


def update_site_name_backward(apps, schema_editor):
    """Revert site name to 'CommCare Connect'."""
    Site = apps.get_model("sites", "Site")
    Site.objects.filter(id=settings.SITE_ID).update(name="CommCare Connect")


class Migration(migrations.Migration):

    dependencies = [("sites", "0004_alter_options_ordering_domain")]

    operations = [
        migrations.RunPython(
            update_site_name_forward,
            update_site_name_backward,
            hints={"run_on_secondary": False},
        )
    ]
