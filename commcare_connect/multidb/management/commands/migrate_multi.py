import sys
import traceback
from copy import copy

import gevent
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


def get_traceback_string():
    from io import StringIO

    f = StringIO()
    traceback.print_exc(file=f)
    return f.getvalue()


class Command(BaseCommand):
    help = "Call 'migrate' for each configured database"

    def add_arguments(self, parser):
        parser.add_argument("app_label", nargs="?", help="App label of an application to synchronize the state.")
        parser.add_argument(
            "migration_name",
            nargs="?",
            help=(
                "Database state will be brought to the state after that "
                'migration. Use the name "zero" to unapply all migrations.'
            ),
        )
        parser.add_argument(
            "--noinput",
            action="store_false",
            dest="interactive",
            default=True,
            help="Tells Django to NOT prompt the user for input of any kind.",
        )
        parser.add_argument(
            "--fake",
            action="store_true",
            dest="fake",
            default=False,
            help="Mark migrations as run without actually running them.",
        )

    def handle(self, app_label, migration_name, **options):
        args = []
        if app_label is not None:
            args.append(app_label)
        if migration_name is not None:
            args.append(migration_name)

        def migrate_db(db_alias, options=options):
            call_options = copy(options)
            call_options["database"] = db_alias
            call_command("migrate", *args, **call_options)

        dbs_to_migrate = settings.DATABASES.keys()
        print("\nThe following databases will be migrated:\n * {}\n".format("\n * ".join(dbs_to_migrate)))

        jobs = [gevent.spawn(migrate_db, db_alias) for db_alias in dbs_to_migrate]

        gevent.joinall(jobs)

        migration_error_occured = False
        for job in jobs:
            try:
                job.get()
            except Exception:
                print("\n======================= Error During Migration =======================")
                print(repr(job))
                print(get_traceback_string())
                migration_error_occured = True

        if migration_error_occured:
            sys.exit(1)
