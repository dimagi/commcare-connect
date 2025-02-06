from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections

from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    DeliveryType,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    PaymentUnit,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.users.models import User

REPLICATION_ALLOWED_MODELS = [
    Assessment,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    DeliveryType,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Organization,
    Payment,
    PaymentUnit,
    Program,
    User,
    UserVisit,
]


class Command(BaseCommand):
    help = "Create a publication for the default database and a subscription for the secondary database alias."

    def handle(self, *args, **options):
        secondary_db_alias = settings.SECONDARY_DB_ALIAS
        if not secondary_db_alias:
            raise CommandError("'secondary' database needs to be configured")

        # Ensure secondary database has table schemas
        self.stdout.write(
            self.style.WARNING(
                "Ensure that default database has logical replication enabled and"
                "that the secondary database has django migration run."
            )
        )
        confirm_migrate = input("Proceed? (yes/no): ").strip().lower()

        if confirm_migrate != "yes":
            self.stdout.write(self.style.ERROR("Aborting: Please run 'migrate --database=secondary' and try again."))
            return

        # Create publication in the default database
        default_conn = connections[DEFAULT_DB_ALIAS]
        self.stdout.write("Creating publication in the default database...")
        publication_name = "tables_for_superset_pub"

        # Construct publication table list
        table_list = []
        for model in REPLICATION_ALLOWED_MODELS:
            try:
                table_list.append(model._meta.db_table)
            except Exception as e:
                raise CommandError(f"Error resolving model {model}: {e}")

        if not table_list:
            raise CommandError("No valid tables found for publication.")

        # Create publication
        with default_conn.cursor() as cursor:
            # Check if publication exists
            cursor.execute("SELECT pubname FROM pg_publication WHERE pubname = %s;", [publication_name])
            if cursor.fetchone():
                self.stdout.write(
                    self.style.WARNING(f"Publication '{publication_name}' already exists. Skipping creation.")
                )
            else:
                # Create new publication
                tables = ", ".join([f'"{table}"' for table in table_list])
                cursor.execute(f"CREATE PUBLICATION {publication_name} FOR TABLE {tables};")
                self.stdout.write(self.style.SUCCESS(f"Publication '{publication_name}' created successfully."))

        # Create subscription in the secondary database
        secondary_conn = connections[secondary_db_alias]
        self.stdout.write("Creating subscription in the secondary database...")
        subscription_name = "tables_for_superset_sub"

        with secondary_conn.cursor() as cursor:
            # Check if subscription exists
            cursor.execute("SELECT subname FROM pg_subscription WHERE subname = %s;", [subscription_name])
            if cursor.fetchone():
                self.stdout.write(
                    self.style.WARNING(f"Subscription '{subscription_name}' already exists. Skipping creation.")
                )
            else:
                # Create new subscription
                default_db_settings = default_conn.settings_dict
                primary_conn_info = (
                    f"host={default_db_settings['HOST']} "
                    f"port={default_db_settings['PORT']} "
                    f"dbname={default_db_settings['NAME']} "
                    f"user={default_db_settings['USER']} "
                    f"password={default_db_settings['PASSWORD']}"
                )
                cursor.execute(
                    f"""
                    CREATE SUBSCRIPTION {subscription_name}
                    CONNECTION '{primary_conn_info}'
                    PUBLICATION {publication_name};
                    """
                )
                self.stdout.write(self.style.SUCCESS(f"Subscription '{subscription_name}' created successfully."))

        self.stdout.write(self.style.SUCCESS("Publication and subscription setup completed."))
