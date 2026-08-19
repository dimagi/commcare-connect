from django.core.management import BaseCommand
from django.db import transaction
from django.db.models import F

from commcare_connect.opportunity.models import CompletedWork, CompletedWorkInvoice, ExchangeRate, InvoiceStatus
from commcare_connect.utils.itertools import batched

BATCH_SIZE = 1000


# TODO One time run command. Remove this once it has run.
class Command(BaseCommand):
    help = (
        "Backfill CompletedWorkInvoice snapshots and invoiced_approved_count "
        "for service-delivery works with unbilled approved units."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE,
            help="Number of works processed per atomic transaction.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute what would be created/updated without committing any changes.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        # invoiced_approved_count defaults to 0, so this single condition catches both a work
        # that's never been snapshotted and one that was snapshotted but has since accrued more
        # approved units than it was billed for — rerunning this command picks up both.
        works_qs = (
            CompletedWork.objects.filter(
                invoice__isnull=False,
                saved_approved_count__gt=F("invoiced_approved_count"),
                invoice__service_delivery=True,
            )
            .exclude(invoice__status__in=[InvoiceStatus.CANCELLED_BY_NM, InvoiceStatus.REJECTED_BY_PM])
            .select_related("invoice", "opportunity_access__opportunity__currency")
            .order_by("id")
        )
        total = works_qs.count()
        self.stdout.write(f"Backfilling {total} invoiced work(s) with an unbilled delta...")

        created = updated = 0
        for batch in batched(
            works_qs.iterator(chunk_size=batch_size),
            batch_size,
        ):
            batch_created, batch_updated = self._process_batch(batch, dry_run)
            created += batch_created
            updated += batch_updated
            self.stdout.write(f"  ...{created + updated}/{total}")

        prefix = "Dry run. Would" if dry_run else "Done."
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix} create {created} snapshot row(s), update {updated} existing snapshot row(s)."
            )
        )

    def _process_batch(self, works, dry_run):
        if dry_run:
            return self._simulate_batch(works)
        return self._write_batch(works)

    def _simulate_batch(self, works):
        # Read-only
        rate_cache = {}
        existing_work_invoice_rows = self._existing_work_invoice_rows(works)
        created = updated = 0
        for work in works:
            self._row_values(work, existing_work_invoice_rows.get(work.id), rate_cache)
            if work.id in existing_work_invoice_rows:
                updated += 1
            else:
                created += 1
        return created, updated

    def _write_batch(self, works):
        rate_cache = {}
        with transaction.atomic():
            # Lock the works so concurrent approve/invoice processing can't change saved_*
            # values between our read and the invoiced_approved_count update below.
            locked_works = list(
                CompletedWork.objects.filter(pk__in=[work.id for work in works])
                .select_related("invoice", "opportunity_access__opportunity__currency")
                .select_for_update(of=("self",))
            )
            # Re-derive under the lock: a concurrent write may have already caught a work up
            # (or changed its delta), or its invoice may have been cancelled/rejected, since the
            # unlocked read that selected `works`.
            to_process = [
                work
                for work in locked_works
                if work.saved_approved_count > work.invoiced_approved_count
                and work.invoice.status not in (InvoiceStatus.CANCELLED_BY_NM, InvoiceStatus.REJECTED_BY_PM)
            ]
            if not to_process:
                return 0, 0

            existing_work_invoice_rows = self._existing_work_invoice_rows(to_process)
            to_create = []
            to_update = []
            for work in to_process:
                work_invoice_row = existing_work_invoice_rows.get(work.id)
                values = self._row_values(work, work_invoice_row, rate_cache)
                if work_invoice_row is None:
                    to_create.append(CompletedWorkInvoice(invoice=work.invoice, completed_work=work, **values))
                else:
                    for field, value in values.items():
                        setattr(work_invoice_row, field, value)
                    to_update.append(work_invoice_row)
                work.invoiced_approved_count = work.saved_approved_count

            if to_create:
                CompletedWorkInvoice.objects.bulk_create(to_create)
            if to_update:
                CompletedWorkInvoice.objects.bulk_update(
                    to_update,
                    fields=[
                        "billed_count",
                        "flw_amount_local",
                        "flw_amount_usd",
                        "org_amount_local",
                        "org_amount_usd",
                    ],
                )
            CompletedWork.objects.bulk_update(to_process, fields=["invoiced_approved_count"])
        return len(to_create), len(to_update)

    @staticmethod
    def _existing_work_invoice_rows(works):
        rows = CompletedWorkInvoice.objects.filter(completed_work_id__in=[work.id for work in works])
        return {row.completed_work_id: row for row in rows}

    def _row_values(self, work, existing_row, rate_cache):
        values = {
            "billed_count": work.saved_approved_count,
            "flw_amount_local": work.saved_payment_accrued,
            "flw_amount_usd": work.saved_payment_accrued_usd,
            "org_amount_local": work.saved_org_payment_accrued,
            "org_amount_usd": work.saved_org_payment_accrued_usd,
        }
        if existing_row is None:
            # Attribute billed units to the approval month (status_modified_date), matching
            # get_invoice_items' TruncMonth grouping.
            month = work.status_modified_date.date().replace(day=1)
            values["month"] = month
            values["exchange_rate"] = self._exchange_rate(work, month, rate_cache)
        return values

    @staticmethod
    def _exchange_rate(work, billed_month, rate_cache):
        currency_code = work.opportunity_access.opportunity.currency_code
        key = (currency_code, billed_month)
        if key not in rate_cache:
            rate_cache[key] = ExchangeRate.latest_exchange_rate(currency_code, billed_month)
        return rate_cache[key]
