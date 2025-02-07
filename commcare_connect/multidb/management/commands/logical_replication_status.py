from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections

from .setup_logical_replication import PUBLICATION_NAME, REPLICATION_ALLOWED_MODELS, SUBSCRIPTION_NAME


class Command(BaseCommand):
    help = "."

    def handle(self, *args, **options):
        secondary_db_alias = settings.SECONDARY_DB_ALIAS
        if not secondary_db_alias:
            raise CommandError(
                "'secondary' database needs to be configured and " "logical replication needs to be setup"
            )

        self.stdout.write(self.style.WARNING("This will run some queries to get logical replication status"))
        confirm = input("Proceed? (yes/no): ").strip().lower()

        if confirm != "yes":
            self.stdout.write(self.style.ERROR("Aborting: Please run 'migrate --database=secondary' and try again."))
            return
        self.stdout.write(self.style.SUCCESS("Checking logical replication status...\n"))

        default_conn = connections[DEFAULT_DB_ALIAS]
        secondary_conn = connections[secondary_db_alias]

        # Check publication details
        self.stdout.write(self.style.SUCCESS("Publication Status (Primary Database):"))
        with default_conn.cursor() as cursor:
            cursor.execute("SELECT * FROM pg_publication WHERE pubname = %s;", [PUBLICATION_NAME])
            rows = cursor.fetchall()
            if rows:
                for row in rows:
                    self.stdout.write(str(row))
            else:
                self.stdout.write(self.style.ERROR("Publication not found."))

        # Check active replication slots
        self.stdout.write(self.style.SUCCESS("\nReplication Slots (Primary Database):"))
        with default_conn.cursor() as cursor:
            cursor.execute("SELECT slot_name, active, confirmed_flush_lsn FROM pg_replication_slots;")
            rows = cursor.fetchall()
            if rows:
                for row in rows:
                    self.stdout.write(str(row))
            else:
                self.stdout.write(self.style.WARNING("No replication slots found."))

        # Check subscription details
        self.stdout.write(self.style.SUCCESS("\nSubscription Status (Secondary Database):"))
        with secondary_conn.cursor() as cursor:
            cursor.execute("SELECT * FROM pg_subscription WHERE subname = %s;", [SUBSCRIPTION_NAME])
            rows = cursor.fetchall()
            if rows:
                for row in rows:
                    self.stdout.write(str(row))
            else:
                self.stdout.write(self.style.ERROR("Subscription not found."))

        # Check replication statistics
        self.stdout.write(self.style.SUCCESS("\nReplication Statistics (Primary Database):"))
        with default_conn.cursor() as cursor:
            cursor.execute("SELECT * FROM pg_stat_replication;")
            rows = cursor.fetchall()
            if rows:
                for row in rows:
                    self.stdout.write(str(row))
            else:
                self.stdout.write(self.style.WARNING("No active replication connections found."))
        # Fetch replication delay
        self.stdout.write("\nChecking replication delay...")

        with default_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    client_addr, sent_lsn, write_lsn, flush_lsn, replay_lsn,
                    pg_wal_lsn_diff(sent_lsn, replay_lsn) AS replication_delay_bytes
                FROM pg_stat_replication;
            """
            )
            replication_status = cursor.fetchall()

        if replication_status:
            for row in replication_status:
                client_addr, sent_lsn, write_lsn, flush_lsn, replay_lsn, replication_delay = row
                self.stdout.write(
                    f"Client: {client_addr}, Sent LSN: {sent_lsn}, "
                    f"Write LSN: {write_lsn}, Flush LSN: {flush_lsn}, "
                    f"Replay LSN: {replay_lsn}, Replication Delay (bytes): {replication_delay}"
                )
        else:
            self.stdout.write(self.style.WARNING("No active replication found."))

        self.stdout.write(self.style.SUCCESS("Replication delay check completed."))

        self.stdout.write(self.style.SUCCESS("\nFetching table counts from both databases..."))
        self.stdout.write(f"{'Table':<30}{'Primary DB Count':<20}{'Secondary DB Count'}")
        self.stdout.write("-" * 70)

        for model in REPLICATION_ALLOWED_MODELS:
            table_name = model._meta.db_table
            with default_conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
                primary_count = cursor.fetchone()[0]

            with secondary_conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
                secondary_count = cursor.fetchone()[0]

            self.stdout.write(f"{table_name:<30}{primary_count:<20}{secondary_count}")

        self.stdout.write(self.style.SUCCESS("Table counts fetched successfully."))
