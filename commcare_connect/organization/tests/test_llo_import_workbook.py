from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from commcare_connect.organization.llo_import.execution import execute_import_plan
from commcare_connect.organization.llo_import.planning import Status, build_import_plan
from commcare_connect.organization.llo_import.workbook import (
    CONNECT_ID_COLUMN,
    CONTACT_COLUMNS,
    CONTACTS_TAB,
    IMPORT_STATUS_COLUMN,
    ORGANIZATIONS_TAB,
    WorkbookError,
    load_workbook,
    output_path_for,
    write_results,
)
from commcare_connect.organization.tests.llo_import_helpers import (
    contact_values,
    make_workbook,
    organization_values,
)
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory

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


def test_output_path_for():
    path = output_path_for(Path("/data/Connect LLO Directory.xlsx"), datetime(2026, 11, 11, 14, 30))

    assert path == Path("/data/Connect LLO Directory - imported 2026-11-11 1430.xlsx")


def _results(path, tab):
    sheet = openpyxl.load_workbook(path)[tab]
    headers = {cell.value: cell.column for cell in sheet[1] if cell.value}
    return {
        row[0].row: (row[headers[CONNECT_ID_COLUMN] - 1].value, row[headers[IMPORT_STATUS_COLUMN] - 1].value)
        for row in sheet.iter_rows(min_row=2)
    }


def _import_and_write(tmp_path, user, contacts, organizations):
    path = make_workbook(tmp_path, contacts, organizations)
    return _execute_and_write(path, user, return_plan=True)


def _single_row_workbook(tmp_path):
    path = make_workbook(tmp_path, [contact_values()], [organization_values()])
    return path, openpyxl.load_workbook(path)


def _execute_and_write(path, user, return_plan=False):
    workbook = load_workbook(path)
    plan = build_import_plan(workbook)
    execute_import_plan(plan, user)
    output_path = path.with_name(f"{path.stem}-copy.xlsx")
    write_results(workbook, plan, output_path)
    return (plan, output_path) if return_plan else output_path


def test_writes_ids_and_statuses(tmp_path, user, countries, sectors):
    existing = ContactFactory(email="old@x.org", organization=OrganizationFactory(name="Old Org"))
    plan, output_path = _import_and_write(
        tmp_path,
        user,
        [
            contact_values(),
            contact_values(**{"Email Address": "old@x.org", "Organization Name": "Old Org"}),
            contact_values(**{"Email Address": "bad", "Organization Name": "Old Org"}),
        ],
        [organization_values()],
    )

    created = plan.contacts[0].contact
    assert _results(output_path, CONTACTS_TAB) == {
        2: (str(created.contact_id), "Created"),
        3: (str(existing.contact_id), "Already existed"),
        4: (None, None),
    }
    assert _results(output_path, ORGANIZATIONS_TAB) == {2: (plan.organizations[0].organization.slug, "Created")}


def test_writes_organization_matched_by_name_onto_its_row(tmp_path, user):
    organization = OrganizationFactory(name="Acme")
    _, output_path = _import_and_write(
        tmp_path,
        user,
        [contact_values(), contact_values(**{"Organization Name": "Zed", "Email Address": "z@x.org"})],
        [organization_values(**{"Organization Name": " Acme "}), organization_values(**{"Organization Name": "Zed"})],
    )

    assert _results(output_path, ORGANIZATIONS_TAB)[2] == (organization.slug, "Already existed")
    assert _results(output_path, ORGANIZATIONS_TAB)[3] == (None, None)


def test_does_not_write_name_match_when_several_rows_share_the_name(tmp_path, user):
    OrganizationFactory(name="Acme")
    _, output_path = _import_and_write(
        tmp_path, user, [contact_values()], [organization_values(), organization_values()]
    )

    assert _results(output_path, ORGANIZATIONS_TAB) == {2: (None, None), 3: (None, None)}


def test_keeps_existing_organization_id(tmp_path, user):
    organization = OrganizationFactory(name="Acme")
    path = make_workbook(
        tmp_path,
        [contact_values()],
        [organization_values(**{CONNECT_ID_COLUMN: "kept"})],
        extra_org_headers=(CONNECT_ID_COLUMN,),
    )
    workbook = load_workbook(path)
    plan = build_import_plan(workbook)
    plan.organizations[0].organization = organization
    plan.organizations[0].source = None
    plan.organizations[0].status = Status.ALREADY_EXISTED
    output_path = tmp_path / "copy.xlsx"

    write_results(workbook, plan, output_path)

    assert _results(output_path, ORGANIZATIONS_TAB)[2][0] == "kept"


def test_reuses_existing_result_columns(tmp_path, user, countries, sectors):
    _, first_copy = _import_and_write(tmp_path, user, [contact_values()], [organization_values()])
    second_copy = _execute_and_write(first_copy, user)

    for tab in (CONTACTS_TAB, ORGANIZATIONS_TAB):
        headers = [cell.value for cell in openpyxl.load_workbook(second_copy)[tab][1]]
        assert headers.count(CONNECT_ID_COLUMN) == 1
        assert headers.count(IMPORT_STATUS_COLUMN) == 1
    assert second_copy != first_copy
    assert _results(second_copy, CONTACTS_TAB) == _results(first_copy, CONTACTS_TAB)


def test_appends_after_trailing_unnamed_columns(tmp_path, user, countries, sectors):
    path, book = _single_row_workbook(tmp_path)
    book[ORGANIZATIONS_TAB].cell(row=2, column=26, value="scratch")
    book.save(path)
    output_path = _execute_and_write(path, user)

    sheet = openpyxl.load_workbook(output_path)[ORGANIZATIONS_TAB]
    assert sheet.cell(row=2, column=26).value == "scratch"
    assert (sheet.cell(row=1, column=27).value, sheet.cell(row=1, column=28).value) == (
        CONNECT_ID_COLUMN,
        IMPORT_STATUS_COLUMN,
    )
    assert sheet.cell(row=2, column=28).value == "Created"


def test_does_not_modify_input_and_keeps_other_tabs(tmp_path, user, countries, sectors):
    path, book = _single_row_workbook(tmp_path)
    book.create_sheet("Notes")["A1"] = "keep me"
    organizations = book[ORGANIZATIONS_TAB]
    organizations["B2"].hyperlink = "https://example.org/acme"
    organizations["Z2"] = '=HYPERLINK("https://example.org","link")'
    book.save(path)
    bytes_before = path.read_bytes()
    output_path = _execute_and_write(path, user)

    assert path.read_bytes() == bytes_before
    copy = openpyxl.load_workbook(output_path)
    assert copy["Notes"]["A1"].value == "keep me"
    assert copy[ORGANIZATIONS_TAB]["B2"].hyperlink.target == "https://example.org/acme"
    assert copy[ORGANIZATIONS_TAB]["Z2"].value == '=HYPERLINK("https://example.org","link")'
