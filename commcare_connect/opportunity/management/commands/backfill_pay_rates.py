from django.apps import apps
from django.core.management import BaseCommand
from django.db import transaction

from commcare_connect.opportunity.utils.pay_rates import backfill_pay_rates


class Command(BaseCommand):
    help = (
        "Adds a pay rate row for payment units that have none. The data migration does this on deploy; "
        "run it again for units created during the deploy, before the new code was serving."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Show how many rows would be created, without writing them."
        )

    def handle(self, *args, dry_run=False, **options):
        with transaction.atomic():
            created = backfill_pay_rates(apps, dry_run=dry_run)
        if dry_run:
            self.stdout.write(f"Would create {created} pay rate rows.")
        else:
            self.stdout.write(f"Created {created} pay rate rows.")
