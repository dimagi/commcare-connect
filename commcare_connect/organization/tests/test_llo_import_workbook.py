import openpyxl
import pytest

from commcare_connect.organization.llo_import.workbook import (
    CONTACT_COLUMNS,
    CONTACTS_TAB,
    ORGANIZATIONS_TAB,
    WorkbookError,
    load_workbook,
)
from commcare_connect.organization.tests.llo_import_helpers import make_workbook

EOI_COLUMN = "EOIs Applied for (Add Link to EOI)"


def test_reads_rows_by_header(tmp_path):
    path = make_workbook(tmp_path, [{"Email Address": "a@x.org"}], [])
    book = openpyxl.load_workbook(path)
    sheet = book[CONTACTS_TAB]
    sheet.delete_rows(1, 2)
    shuffled = [header + "  " if header == "Email Address" else header for header in reversed(CONTACT_COLUMNS)]
    sheet.append(shuffled)
    sheet.append([{"Email Address  ": "a@x.org", "Role / Title": "Lead"}.get(header) for header in shuffled])
    book.save(path)

    row = load_workbook(path).contacts[0]

    assert row.values["Email Address"] == "a@x.org"
    assert row.values["Role / Title"] == "Lead"
    assert row.number == 2


def test_skips_rows_with_no_values(tmp_path):
    path = make_workbook(
        tmp_path,
        [{"Email Address": "a@x.org"}, {}, {"Email Address": "b@x.org"}],
        [],
    )

    rows = load_workbook(path).contacts

    assert [(row.number, row.values["Email Address"]) for row in rows] == [(2, "a@x.org"), (4, "b@x.org")]


def test_keeps_cell_hyperlinks(tmp_path):
    path = make_workbook(tmp_path, [], [{EOI_COLUMN: ("Title", "https://eoi.example/1")}])

    row = load_workbook(path).organizations[0]

    assert row.hyperlinks[EOI_COLUMN] == "https://eoi.example/1"


def test_keeps_formula_text(tmp_path):
    formula = '=HYPERLINK("https://a.example","A")'
    path = make_workbook(tmp_path, [], [{EOI_COLUMN: formula}])

    assert load_workbook(path).organizations[0].values[EOI_COLUMN] == formula


@pytest.mark.parametrize("missing", [CONTACTS_TAB, "Email Address"])
def test_missing_tab_or_column_raises(tmp_path, missing):
    path = make_workbook(tmp_path, [{"Email Address": "a@x.org"}], [])
    book = openpyxl.load_workbook(path)
    if missing == CONTACTS_TAB:
        del book[CONTACTS_TAB]
    else:
        column = CONTACT_COLUMNS.index(missing) + 1
        book[CONTACTS_TAB].delete_cols(column)
    book.save(path)

    with pytest.raises(WorkbookError, match=missing):
        load_workbook(path)


def test_result_columns_are_included_when_present(tmp_path):
    path = make_workbook(
        tmp_path,
        [],
        [{"Connect ID": "abc", "Organization Name": "Org"}],
        extra_org_headers=("Connect ID",),
    )

    assert load_workbook(path).organizations[0].values["Connect ID"] == "abc"
    assert ORGANIZATIONS_TAB in load_workbook(path).book.sheetnames
