import os
from pathlib import Path
from zipfile import BadZipFile

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from openpyxl.utils.exceptions import InvalidFileException

from commcare_connect.organization.llo_import.execution import execute_import_plan
from commcare_connect.organization.llo_import.planning import build_import_plan
from commcare_connect.organization.llo_import.report import render_report
from commcare_connect.organization.llo_import.workbook import (
    WorkbookError,
    load_workbook,
    output_path_for,
    write_results,
)
from commcare_connect.users.models import User


class Command(BaseCommand):
    help = "Import contacts and organizations from the LLO Directory workbook."

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path, help="Path to the LLO Directory .xlsx workbook")
        parser.add_argument("--user-email", required=True, help="Email of the user the records are attributed to")
        parser.add_argument("--commit", action="store_true", help="Write to the database and save an output copy")

    def handle(self, path, user_email, commit=False, **options):
        user = self._get_user(user_email)
        try:
            workbook = load_workbook(path)
        except WorkbookError as error:
            raise CommandError(str(error))
        except (FileNotFoundError, InvalidFileException, BadZipFile) as error:
            raise CommandError(f"Can't read the workbook {path}: {error}")
        plan = build_import_plan(workbook)
        if commit:
            self._commit(workbook, plan, user)
        else:
            self.stdout.write(render_report(plan, committed=False, output_path=None), ending="")

    def _get_user(self, user_email):
        users = list(User.objects.filter(email__iexact=user_email)[:2])
        if len(users) != 1:
            raise CommandError(f"Expected exactly one user with email {user_email}, found {len(users)}.")
        return users[0]

    def _commit(self, workbook, plan, user):
        """Saves the plan's records and the workbook copy, then prints the report even if the copy fails."""
        output_path = output_path_for(workbook.path, timezone.localtime())
        self._check_output_path(output_path)
        execute_import_plan(plan, user)
        try:
            write_results(workbook, plan, output_path)
        except OSError as error:
            self.stdout.write(render_report(plan, committed=True, output_path=None), ending="")
            raise CommandError(f"The records were saved, but the workbook copy {output_path} wasn't written: {error}")
        self.stdout.write(render_report(plan, committed=True, output_path=output_path), ending="")

    def _check_output_path(self, output_path):
        if output_path.exists():
            raise CommandError(f"The output copy {output_path} already exists; wait a minute and run again.")
        if not os.access(output_path.parent, os.W_OK):
            raise CommandError(f"Can't write the output copy into {output_path.parent}.")
