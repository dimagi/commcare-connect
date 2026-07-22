from django.core.management import BaseCommand
from django.db import transaction

from commcare_connect.opportunity.models import CompletedWork, CompletedWorkInvoice
from commcare_connect.opportunity.visit_import import get_exchange_rate

BATCH_SIZE = 1000


class Command(BaseCommand):
    help = (
        "Backfill CompletedWorkInvoice snapshot rows and the invoiced_approved_count watermark "
        "for existing invoiced works, frozen from their current saved_* values (no drift correction)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE,
            help="Number of works processed per atomic transaction.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        works_qs = (
            CompletedWork.objects.filter(invoice__isnull=False, saved_approved_count__gt=0)
            .select_related("invoice", "opportunity_access__opportunity__currency")
            .order_by("id")
        )
        total = works_qs.count()
        self.stdout.write(f"Backfilling snapshot rows for {total} invoiced work(s)...")

        created = skipped = 0
        batch = []
        for work in works_qs.iterator(chunk_size=batch_size):
            batch.append(work)
            if len(batch) >= batch_size:
                c, s = self._process_batch(batch)
                created += c
                skipped += s
                batch = []
        if batch:
            c, s = self._process_batch(batch)
            created += c
            skipped += s

        self.stdout.write(self.style.SUCCESS(f"Done. Created {created} row(s); {skipped} already present."))

    def _process_batch(self, works):
        created = skipped = 0
        rate_cache = {}
        with transaction.atomic():
            newly_billed = []
            for work in works:
                _, was_created = CompletedWorkInvoice.objects.get_or_create(
                    invoice=work.invoice,
                    completed_work=work,
                    defaults=self._row_values(work, rate_cache),
                )
                if was_created:
                    newly_billed.append(self._set_watermark(work))
                    created += 1
                else:
                    skipped += 1
            if newly_billed:
                CompletedWork.objects.bulk_update(newly_billed, fields=["invoiced_approved_count"])
        return created, skipped

    @staticmethod
    def _set_watermark(work):
        work.invoiced_approved_count = work.saved_approved_count
        return work

    def _row_values(self, work, rate_cache):
        # Attribute the billed units to the month of approval (status_modified_date), matching
        # get_invoice_items' TruncMonth grouping. The exact day is not used in invoicing and still
        # lives on CompletedWork.status_modified_date. status_modified_date is expected on every
        # invoiced work (verified 0 nulls in prod); a null would raise here (fail loud) rather
        # than silently guessing a date.
        billed_month = work.status_modified_date.date().replace(day=1)
        return {
            "month": billed_month,
            "billed_count": work.saved_approved_count,
            "flw_amount_local": work.saved_payment_accrued,
            "flw_amount_usd": work.saved_payment_accrued_usd,
            "org_amount_local": work.saved_org_payment_accrued,
            "org_amount_usd": work.saved_org_payment_accrued_usd,
            "exchange_rate": self._exchange_rate(work, billed_month, rate_cache),
        }

    @staticmethod
    def _exchange_rate(work, billed_month, rate_cache):
        # get_exchange_rate returns the numeric rate for the month (1 for USD) — the same value
        # get_invoice_items displays today.
        currency_code = work.opportunity_access.opportunity.currency_code
        key = (currency_code, billed_month)
        if key not in rate_cache:
            rate_cache[key] = get_exchange_rate(currency_code, billed_month)
        return rate_cache[key]
