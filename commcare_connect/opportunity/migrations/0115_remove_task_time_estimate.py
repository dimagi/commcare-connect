from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("opportunity", "0114_paymentinvoice_invoice_ticket_link"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="task",
            name="time_estimate",
        ),
    ]
