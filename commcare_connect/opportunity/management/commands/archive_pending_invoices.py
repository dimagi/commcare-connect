from datetime import datetime

from django.core.management import BaseCommand
from django.utils.timezone import now

from commcare_connect.opportunity.models import InvoiceStatus, PaymentInvoice


class Command(BaseCommand):
    help = "Archive Pending Invoices in Opportunities."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Dry Run. Returns the Number of invoice that will be updated."
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run")
        cutoff_date = datetime(2025, 11, 1)
        invoices = PaymentInvoice.objects.filter(opportunity__end_date__lt=cutoff_date, status=InvoiceStatus.PENDING)

        if dry_run:
            print(f"Found {invoices.count()} Pending invoices.")
        else:
            print(f"Marking {invoices.count()} Pending invoices as Archived.")
            invoices.update(status=InvoiceStatus.ARCHIVED, archived_date=now())
