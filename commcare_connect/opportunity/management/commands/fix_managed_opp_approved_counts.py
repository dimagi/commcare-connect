import argparse

from django.core.cache import cache
from django.core.management import BaseCommand
from django.db import transaction
from django.db.models import Prefetch

from commcare_connect.opportunity.models import CompletedWork, OpportunityAccess
from commcare_connect.opportunity.utils.completed_work import update_status


class Command(BaseCommand):
    # One-time backfill for CCCT-2505; safe to delete after running in prod.
    help = "Recalculate saved_approved_count for unbilled managed opportunity completed works."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            required=True,
            action=argparse.BooleanOptionalAction,
            help="Preview potentially affected works (--dry-run) or apply the corrections (--no-dry-run).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        affected_works = CompletedWork.objects.filter(
            invoice__isnull=True,
            saved_approved_count__gt=1,
        ).select_related("payment_unit")
        accesses = (
            OpportunityAccess.objects.filter(
                opportunity__managed=True,
                opportunity__active=True,
            )
            .select_related("opportunity")
            .prefetch_related(Prefetch("completedwork_set", queryset=affected_works))
        )

        affected = 0
        for access in accesses.iterator(chunk_size=200):
            works = list(access.completedwork_set.all())
            if not works:
                continue
            affected += self._preview(access, works) if dry_run else self._recalculate(access, works)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry-run: {affected} completed work(s) potentially affected. No changes saved.")
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"{affected} completed work(s) corrected."))

    def _preview(self, access, works):
        """Read-only: list the candidate works and their current saved values."""
        for work in works:
            self.stdout.write(
                f"  [dry-run] CW {work.id} (opp {access.opportunity_id}): "
                f"saved_approved_count={work.saved_approved_count}, accrued={work.saved_payment_accrued}"
            )
        return len(works)

    def _recalculate(self, access, works):
        """Recompute saved counts, logging each before/after delta.

        Wrapped in a transaction so a crash can't leave a single access half-corrected.
        Re-fetches the works under a row lock and re-checks invoice__isnull to avoid racing
        with a concurrent invoicing that could attach one of these works to an invoice between
        the initial query and this update (which would leave the invoice billed on a stale count).
        Held under the same per-access cache lock as the live payment recompute path
        (update_payment_accrued_for_user) so the access's payment_accrued isn't clobbered by a
        concurrent recompute reading a stale sum.
        """
        before = {work.id: (work.saved_approved_count, work.saved_payment_accrued) for work in works}
        work_ids = before.keys()

        with cache.lock(f"update_payment_accrued_lock_{access.id}", timeout=900), transaction.atomic():
            still_uninvoiced = (
                CompletedWork.objects.filter(id__in=work_ids, invoice__isnull=True)
                .select_for_update(of=("self",))
                .select_related("payment_unit")
            )
            update_status(still_uninvoiced, access, compute_payment=True)

            changed = 0
            for work in CompletedWork.objects.filter(id__in=work_ids, invoice__isnull=True):
                old = before[work.id]
                new = (work.saved_approved_count, work.saved_payment_accrued)
                if old != new:
                    changed += 1
                    self.stdout.write(
                        f"  CW {work.id} (opp {access.opportunity.name}): "
                        f"approved_count {old[0]} -> {new[0]}, accrued {old[1]} -> {new[1]}"
                    )
                else:
                    self.stdout.write(f"  CW {work.id} (opp {access.opportunity.name}): no change")

        return changed
