from django.db import migrations

from commcare_connect.opportunity.utils.pay_rates import backfill_pay_rates


def backfill(apps, schema_editor):
    backfill_pay_rates(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0154_paymentunitrate"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop, hints={"run_on_secondary": False}),
    ]
