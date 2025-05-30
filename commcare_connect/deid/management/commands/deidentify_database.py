import argparse

from django.core.management.base import BaseCommand

from commcare_connect.deid.config import get_fields_to_anonymize
from commcare_connect.deid.sql_generator import generate_anonymization_sql


class Command(BaseCommand):
    help = (
        "Generates a SQL script to de-identify the database by dropping specified columns. "
        "The generated script should ONLY be run on a non-production, copied database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "output_file",
            type=argparse.FileType("w", encoding="UTF-8"),
            help="The file path where the generated SQL script will be saved.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("WARNING: This command generates a SQL script for de-identification."))
        self.stdout.write(
            self.style.WARNING(
                "Ensure the generated script is reviewed and run ONLY on a non-production, copied database."
            )
        )

        anonymization_configs = get_fields_to_anonymize()
        if not anonymization_configs:
            self.stdout.write(self.style.NOTICE("No anonymization configurations found. Exiting."))
            return

        sql_statements = generate_anonymization_sql(anonymization_configs)

        output_file = options["output_file"]
        try:
            for statement in sql_statements:
                output_file.write(statement + "\n")
            self.stdout.write(
                self.style.SUCCESS(f"Successfully generated de-identification SQL script at: {output_file.name}")
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error writing to output file: {e}"))
        finally:
            if output_file:
                output_file.close()

        self.stdout.write(self.style.NOTICE("Please review the generated script carefully before execution."))
