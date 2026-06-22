from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS, connections

from commcare_connect.multidb import replication


class Command(BaseCommand):
    help = (
        "Reconcile the logical-replication publication/subscription with the "
        "models in REPLICATION_INCLUDED_MODELS. Non-interactive; safe to run on "
        "every deploy after migrate_multi. Requires a one-time bootstrap "
        "(setup_logical_replication) to have run for this environment."
    )

    def handle(self, *args, **options):
        if not replication.replication_is_active():
            self.stdout.write("Replication not enabled for this environment; skipping.")
            return

        primary = connections[DEFAULT_DB_ALIAS]

        if not replication.publication_exists(primary):
            self.stdout.write(
                self.style.WARNING(
                    "Publication does not exist — run the one-time bootstrap "
                    "(setup_logical_replication) first. Skipping refresh."
                )
            )
            return

        if replication.is_publication_in_sync(primary):
            self.stdout.write("Publication already in sync; nothing to do.")
            return

        desired = replication.get_replicated_tables()
        self.stdout.write("Publication out of sync; reconciling...")
        replication.refresh_publication(
            primary,
            desired_tables=desired,
            repl_user=settings.REPLICATION_PRIMARY_REPL_USER,
        )
        self.stdout.write(self.style.SUCCESS("Primary publication updated."))

        secondary = connections[settings.SECONDARY_DB_ALIAS]
        replication.refresh_subscription(
            secondary,
            superset_user=settings.REPLICATION_SUPERSET_USER,
        )
        self.stdout.write(self.style.SUCCESS("Secondary subscription refreshed."))
