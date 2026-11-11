from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

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
        plan = build_import_plan(workbook)
        output_path = None
        if commit:
            execute_import_plan(plan, user)
            output_path = output_path_for(workbook.path, timezone.localtime())
            write_results(workbook, plan, output_path)
        self.stdout.write(render_report(plan, committed=commit, output_path=output_path), ending="")

    def _get_user(self, user_email):
        users = list(User.objects.filter(email__iexact=user_email)[:2])
        if len(users) != 1:
            raise CommandError(f"Expected exactly one user with email {user_email}, found {len(users)}.")
        return users[0]
