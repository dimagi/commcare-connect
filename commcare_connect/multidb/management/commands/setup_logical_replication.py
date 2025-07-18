import getpass

import psycopg2
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections

from commcare_connect.multidb.constants import REPLICATION_ALLOWED_MODELS

PUBLICATION_NAME = "tables_for_superset_pub"
SUBSCRIPTION_NAME = "tables_for_superset_sub"


class Command(BaseCommand):
    help = "Create a publication for the default database and a subscription for the secondary database alias."

    def get_table_list(self):
        table_list = []
        for model in REPLICATION_ALLOWED_MODELS:
            try:
                table_list.append(model._meta.db_table)
            except Exception as e:
                raise CommandError(f"Error resolving model {model}: {e}")
        return table_list

    def handle(self, *args, **options):
        secondary_db_alias = settings.SECONDARY_DB_ALIAS
        if not secondary_db_alias:
            raise CommandError("'secondary' database needs to be configured")

        # Ensure secondary database has table schemas
        self.stdout.write(
            self.style.WARNING(
                "Ensure that default database has logical replication enabled and "
                "that the secondary database has django migration run."
            )
        )
        confirm_migrate = input("Proceed? (yes/no): ").strip().lower()
        if confirm_migrate != "yes":
            self.stdout.write(self.style.ERROR("Aborting: Please run 'migrate --database=secondary' and try again."))
            return

        default_conn = connections[DEFAULT_DB_ALIAS]
        self.stdout.write("Creating publication in the default database...")

        table_list = self.get_table_list()
        if not table_list:
            raise CommandError("No valid tables found for publication.")

        # Create publication
        with default_conn.cursor() as cursor:
            # Check if publication exists
            cursor.execute("SELECT pubname FROM pg_publication WHERE pubname = %s;", [PUBLICATION_NAME])
            publication_exists = cursor.fetchone()

            tables = ", ".join([f'"{table}"' for table in table_list])
            if publication_exists:
                self.stdout.write(f"Publication '{PUBLICATION_NAME}' already exists, refreshing it.")
                cursor.execute(f"ALTER PUBLICATION {PUBLICATION_NAME} SET TABLE {tables};")
                self.stdout.write(self.style.SUCCESS(f"Publication '{PUBLICATION_NAME}' altered successfully."))
            else:
                self.stdout.write(f"Creating new publication '{PUBLICATION_NAME}'.")
                cursor.execute(f"CREATE PUBLICATION {PUBLICATION_NAME} FOR TABLE {tables};")
                self.stdout.write(self.style.SUCCESS(f"Publication '{PUBLICATION_NAME}' created successfully."))

        self.stdout.write("Setting up subscription in the secondary database...")

        secondary_db_settings = connections[secondary_db_alias].settings_dict
        self.stdout.write(self.style.SUCCESS("Enter superuser credentials for the secondary (subscriber) database:"))
        secondary_user = input("Enter username: ")
        secondary_password = getpass.getpass("Enter password: ")

        try:
            secondary_conn = psycopg2.connect(
                host=secondary_db_settings["HOST"],
                port=secondary_db_settings["PORT"],
                dbname=secondary_db_settings["NAME"],
                user=secondary_user,
                password=secondary_password,
            )
            secondary_conn.autocommit = True
        except Exception as e:
            raise CommandError(f"Could not connect to the secondary database: {e}")

        with secondary_conn.cursor() as cursor:
            cursor.execute("SELECT subname FROM pg_subscription WHERE subname = %s;", [SUBSCRIPTION_NAME])
            if cursor.fetchone():
                self.stdout.write(
                    self.style.WARNING(f"Subscription '{SUBSCRIPTION_NAME}' already exists. Refreshing it.")
                )
                cursor.execute(f"ALTER SUBSCRIPTION {SUBSCRIPTION_NAME} REFRESH PUBLICATION;")
                self.stdout.write(self.style.SUCCESS(f"Subscription '{SUBSCRIPTION_NAME}' refreshed successfully."))
            else:
                # Create new subscription
                default_db_settings = default_conn.settings_dict
                self.stdout.write("Provide user credentials on primary with only replication privilege")
                username = input("Enter username: ")
                password = getpass.getpass("Enter password: ")
                primary_conn_info = (
                    f"host={default_db_settings['HOST']} "
                    f"port={default_db_settings['PORT']} "
                    f"dbname={default_db_settings['NAME']} "
                    f"user={username} "
                    f"password={password}"
                )
                cursor.execute(
                    f"""
                    CREATE SUBSCRIPTION {SUBSCRIPTION_NAME}
                    CONNECTION '{primary_conn_info}'
                    PUBLICATION {PUBLICATION_NAME};
                    """
                )
                self.stdout.write(self.style.SUCCESS(f"Subscription '{SUBSCRIPTION_NAME}' created successfully."))

        # Close the manually created connection
        secondary_conn.close()
