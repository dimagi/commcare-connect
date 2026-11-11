import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from commcare_connect.organization.llo_import.workbook import CONTACT_COLUMNS, CONTACTS_TAB
from commcare_connect.organization.tests.llo_import_helpers import make_workbook


def test_unknown_user_email_fails(tmp_path, db):
    path = make_workbook(tmp_path, [], [])

    with pytest.raises(CommandError, match="nobody@x.org"):
        call_command("import_llo_contacts", path, user_email="nobody@x.org")


def test_missing_column_fails_cleanly(tmp_path, user):
    path = make_workbook(tmp_path, [], [])
    book = openpyxl.load_workbook(path)
    book[CONTACTS_TAB].delete_cols(CONTACT_COLUMNS.index("Email Address") + 1)
    book.save(path)

    with pytest.raises(CommandError, match="Email Address"):
        call_command("import_llo_contacts", path, user_email=user.email)


def test_reports_row_counts(tmp_path, user, capsys):
    path = make_workbook(tmp_path, [{"Email Address": "a@x.org"}], [])

    call_command("import_llo_contacts", path, user_email=user.email)

    assert "Read 1 contact rows and 0 organization rows." in capsys.readouterr().out
