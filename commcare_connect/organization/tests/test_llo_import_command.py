import csv
import re

import openpyxl
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from commcare_connect.organization.llo_import.results import results_path_for
from commcare_connect.organization.llo_import.workbook import CONTACT_COLUMNS, CONTACTS_TAB
from commcare_connect.organization.management.commands import import_llo_contacts
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


def test_commit_writes_results_file(tmp_path, user, countries, sectors, capsys):
    path, bytes_before = _commit_directory(tmp_path, user)

    results = _only_results_file(tmp_path)
    assert results.name in capsys.readouterr().out
    with open(results, newline="", encoding="utf-8-sig") as results_file:
        lines = list(csv.reader(results_file))
    contact_id = str(Contact.objects.get().contact_id)
    assert ["Contacts", "2", "Ada Obi", "ada@x.org", contact_id, "Created"] in lines
    assert path.read_bytes() == bytes_before


def test_rerun_creates_nothing(tmp_path, user, countries, sectors, capsys):
    path, _ = _commit_directory(tmp_path, user)
    counts_before = (Organization.objects.count(), Contact.objects.count())
    _only_results_file(tmp_path).unlink()
    capsys.readouterr()

    call_command("import_llo_contacts", path, user_email=user.email, commit=True)

    assert (Organization.objects.count(), Contact.objects.count()) == counts_before
    assert "Skipped: email already in Connect" in capsys.readouterr().out


@pytest.mark.parametrize("problem", ["exists", "read-only folder"])
def test_commit_fails_before_writing_when_results_file_cannot_be_saved(
    tmp_path, user, countries, sectors, monkeypatch, problem
):
    now = timezone.localtime()
    monkeypatch.setattr(timezone, "localtime", lambda *args, **kwargs: now)
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])
    results = results_path_for(path, now)
    if problem == "exists":
        results.write_text("earlier results")
    else:
        tmp_path.chmod(0o555)

    try:
        with pytest.raises(CommandError, match=re.escape(str(results.parent))):
            call_command("import_llo_contacts", path, user_email=user.email, commit=True)
    finally:
        tmp_path.chmod(0o755)

    assert not Organization.objects.exists()
    assert not Contact.objects.exists()


def test_failed_results_save_still_prints_report(tmp_path, user, countries, sectors, capsys, monkeypatch):
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])

    def fail_write(plan, output_path):
        raise OSError("disk full")

    monkeypatch.setattr(import_llo_contacts, "write_results", fail_write)

    with pytest.raises(CommandError, match="records were saved, but the results file .* wasn't written: disk full"):
        call_command("import_llo_contacts", path, user_email=user.email, commit=True)

    assert "Results file: not written" in capsys.readouterr().out
    assert Contact.objects.get().email == "ada@x.org"


def _commit_directory(tmp_path, user):
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])
    bytes_before = path.read_bytes()
    call_command("import_llo_contacts", path, user_email=user.email, commit=True)
    return path, bytes_before


def _only_results_file(tmp_path):
    (results,) = tmp_path.glob("directory - imported *.csv")
    return results
