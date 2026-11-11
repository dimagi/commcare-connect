import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from commcare_connect.organization.llo_import.workbook import CONTACT_COLUMNS, CONTACTS_TAB
from commcare_connect.organization.models import Contact, Organization
from commcare_connect.organization.tests.llo_import_helpers import contact_values, make_workbook, organization_values
from commcare_connect.users.tests.factories import OrganizationFactory


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


def test_dry_run_writes_nothing(tmp_path, user, capsys):
    OrganizationFactory(name="Acme")
    path = make_workbook(tmp_path, [contact_values()], [organization_values(**{"Organization Name": "New Org"})])
    files_before = set(tmp_path.iterdir())
    counts_before = (Organization.objects.count(), Contact.objects.count())

    call_command("import_llo_contacts", path, user_email=user.email)

    assert "DRY RUN" in capsys.readouterr().out
    assert (Organization.objects.count(), Contact.objects.count()) == counts_before
    assert set(tmp_path.iterdir()) == files_before


def test_commit_creates_records(tmp_path, user, countries, sectors, capsys):
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])

    call_command("import_llo_contacts", path, user_email=user.email, commit=True)

    assert "REAL RUN" in capsys.readouterr().out
    organization = Organization.objects.get(name="Acme")
    assert Contact.objects.get(email="ada@x.org").organization == organization
