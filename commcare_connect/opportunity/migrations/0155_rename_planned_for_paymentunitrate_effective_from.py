from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0154_paymentunitrate"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="paymentunitrate",
            name="unique_live_pay_rate_per_date",
        ),
        migrations.RenameField(
            model_name="paymentunitrate",
            old_name="planned_for",
            new_name="effective_from",
        ),
        migrations.AddConstraint(
            model_name="paymentunitrate",
            constraint=models.UniqueConstraint(
                condition=models.Q(("cancelled_on__isnull", True)),
                fields=("payment_unit", "effective_from"),
                name="unique_live_pay_rate_per_date",
            ),
        ),
    ]
