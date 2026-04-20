from django.core.management.base import BaseCommand

from commcare_connect.opportunity.models import CompletedWork, PaymentInvoice
from commcare_connect.opportunity.utils.completed_work import create_invoice_line_items, get_invoice_items


class Command(BaseCommand):
    help = (
        "Snapshot line items for existing service-delivery invoices that don't have them. "
        "Backfill captures current saved_payment_accrued values - pre-drift amounts are not recoverable."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the invoices that would be backfilled without writing anything.",
        )
        parser.add_argument(
            "--opportunity-id",
            type=int,
            help="Restrict backfill to a single opportunity.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        qs = PaymentInvoice.objects.filter(service_delivery=True, line_items__isnull=True).distinct()
        if options["opportunity_id"]:
            qs = qs.filter(opportunity_id=options["opportunity_id"])

        total = qs.count()
        self.stdout.write(f"Found {total} invoice(s) without line-item snapshot.")

        written = 0
        skipped = []
        for invoice in qs.iterator():
            items = get_invoice_items(CompletedWork.objects.filter(invoice=invoice))
            if not items:
                skipped.append(invoice.invoice_number)
                continue
            if dry_run:
                self.stdout.write(f"  [dry-run] {invoice.invoice_number}: would write {len(items)} row(s)")
                continue
            create_invoice_line_items(invoice, items)
            written += 1

        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {len(skipped)} invoice(s) with no linked completed-work records: "
                    f"{', '.join(skipped)}"
                )
            )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Backfilled {written} invoice(s)."))
