from django.core.management import BaseCommand
from django.db import transaction

from commcare_connect.opportunity.models import InvoiceStatus, PaymentInvoice
from commcare_connect.opportunity.utils.invoice_line_items import (
    Money,
    get_invoice_delivery_rows_for_export,
    group_line_items,
)

CHUNK_SIZE = 500


# TODO One time run command (CI-927). Remove this once it has run.
class Command(BaseCommand):
    help = (
        "Recompute PaymentInvoice.amount_usd for unpaid service-delivery invoices (CI-927). These "
        "were frozen by summing each delivery's already-rounded USD instead of dividing the exact "
        "local total once, so the stored total drifts below the correct amount. Local amounts "
        "(invoice.amount) were not affected and are left alone. Paid invoices are skipped: their "
        "Payment record already reflects the old total, so this would only make it inconsistent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would change without saving.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        invoices = (
            PaymentInvoice.objects.filter(service_delivery=True, payment__isnull=True)
            .exclude(status__in=[InvoiceStatus.CANCELLED_BY_NM, InvoiceStatus.REJECTED_BY_PM])
            .order_by("id")
        )
        total = invoices.count()
        self.stdout.write(f"Checking {total} unpaid service-delivery invoice(s)...")

        changed = 0
        for invoice in invoices.iterator(chunk_size=CHUNK_SIZE):
            if self._recompute(invoice, dry_run):
                changed += 1

        prefix = "Dry run. Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{prefix} {changed}/{total} invoice(s)."))

    def _recompute(self, invoice, dry_run):
        rows = get_invoice_delivery_rows_for_export(invoice)
        if not rows:
            return False

        correct_usd = sum((item.total_pay for item in group_line_items(rows)), Money.zero()).usd
        if correct_usd == invoice.amount_usd:
            return False

        old_usd = invoice.amount_usd
        if dry_run:
            self._log_change(invoice, old_usd, correct_usd)
            return True

        with transaction.atomic():
            # Re-check under lock: a concurrent payment or cancellation since the unlocked read
            # above would make updating amount_usd here wrong or pointless.
            locked = (
                PaymentInvoice.objects.select_for_update(of=("self",))
                .filter(pk=invoice.pk, payment__isnull=True)
                .exclude(status__in=[InvoiceStatus.CANCELLED_BY_NM, InvoiceStatus.REJECTED_BY_PM])
                .first()
            )
            if locked is None:
                return False
            locked.amount_usd = correct_usd
            locked.save(update_fields=["amount_usd"])

        self._log_change(invoice, old_usd, correct_usd)
        return True

    def _log_change(self, invoice, old_usd, correct_usd):
        self.stdout.write(
            f"  invoice pk={invoice.pk} {invoice.invoice_number} status={invoice.status}: {old_usd} -> {correct_usd}"
        )
