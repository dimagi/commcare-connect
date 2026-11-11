import re

import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from commcare_connect.organization.llo_import.workbook import CONTACT_COLUMNS, CONTACTS_TAB, output_path_for
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


@pytest.mark.parametrize(
    "file_name, content",
    [("missing.xlsx", None), ("directory.txt", b"not a workbook"), ("corrupt.xlsx", b"not a zip file")],
)
def test_unreadable_file_fails_cleanly(tmp_path, user, file_name, content):
    path = tmp_path / file_name
    if content is not None:
        path.write_bytes(content)

    with pytest.raises(CommandError, match=f"Can't read the workbook {re.escape(str(path))}"):
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


def test_commit_writes_workbook_copy(tmp_path, user, countries, sectors, capsys):
    path, bytes_before = _commit_directory(tmp_path, user)

    copy = _only_copy(tmp_path)
    assert copy.name in capsys.readouterr().out
    sheet = openpyxl.load_workbook(copy)[CONTACTS_TAB]
    assert [cell.value for cell in sheet[2]][-2:] == [str(Contact.objects.get().contact_id), "Created"]
    assert path.read_bytes() == bytes_before


def test_rerun_on_output_creates_nothing(tmp_path, user, countries, sectors, capsys):
    _commit_directory(tmp_path, user)
    counts_before = (Organization.objects.count(), Contact.objects.count())
    capsys.readouterr()

    call_command("import_llo_contacts", _only_copy(tmp_path), user_email=user.email, commit=True)

    assert (Organization.objects.count(), Contact.objects.count()) == counts_before
    assert "already imported" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("problem", ["exists", "read-only folder"])
def test_commit_fails_before_writing_when_copy_cannot_be_saved(
    tmp_path, user, countries, sectors, monkeypatch, problem
):
    now = timezone.localtime()
    monkeypatch.setattr(timezone, "localtime", lambda *args, **kwargs: now)
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])
    copy = output_path_for(path, now)
    if problem == "exists":
        copy.write_bytes(b"earlier copy")
    else:
        tmp_path.chmod(0o555)

    try:
        with pytest.raises(CommandError, match=re.escape(str(copy.parent))):
            call_command("import_llo_contacts", path, user_email=user.email, commit=True)
    finally:
        tmp_path.chmod(0o755)

    assert not Organization.objects.exists()
    assert not Contact.objects.exists()


def test_failed_copy_save_still_prints_report(tmp_path, user, countries, sectors, capsys, monkeypatch):
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])

    def fail_save(book, filename):
        raise OSError("disk full")

    monkeypatch.setattr(openpyxl.Workbook, "save", fail_save)

    with pytest.raises(CommandError, match="records were saved, but the workbook copy .* wasn't written: disk full"):
        call_command("import_llo_contacts", path, user_email=user.email, commit=True)

    assert "Output copy: not written" in capsys.readouterr().out
    assert Contact.objects.get().email == "ada@x.org"


def _commit_directory(tmp_path, user):
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])
    bytes_before = path.read_bytes()
    call_command("import_llo_contacts", path, user_email=user.email, commit=True)
    return path, bytes_before


def _only_copy(tmp_path):
    (copy,) = tmp_path.glob("directory - imported *.xlsx")
    return copy
