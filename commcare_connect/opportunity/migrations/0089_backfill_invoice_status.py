from django.db import migrations
from commcare_connect.opportunity.models import InvoiceStatus


def backfill_invoice_status(apps, schema_editor):
    PaymentInvoice = apps.get_model("opportunity", "PaymentInvoice")
    PaymentInvoice.objects.filter(payment__isnull=False).update(status=InvoiceStatus.APPROVED)
    PaymentInvoice.objects.filter(payment__isnull=True).update(status=InvoiceStatus.SUBMITTED)


class Migration(migrations.Migration):

    dependencies = [
        ("opportunity", "0088_paymentinvoice_status"),
    ]

    operations = [
        migrations.RunPython(backfill_invoice_status, migrations.RunPython.noop,  hints={"run_on_secondary": False}),
    ]
