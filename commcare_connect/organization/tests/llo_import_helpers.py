from pathlib import Path

import openpyxl

from commcare_connect.organization.llo_import.planning import ImportPlan, build_import_plan
from commcare_connect.organization.llo_import.workbook import (
    CONNECT_ID_COLUMN,
    CONTACT_COLUMNS,
    CONTACTS_TAB,
    ORGANIZATION_COLUMNS,
    ORGANIZATIONS_TAB,
    load_workbook,
)


def make_workbook(tmp_path, contacts, organizations, extra_org_headers=(), extra_contact_headers=()) -> Path:
    book = openpyxl.Workbook()
    book.remove(book.active)
    _write_sheet(book.create_sheet(CONTACTS_TAB), (*CONTACT_COLUMNS, *extra_contact_headers), contacts)
    _write_sheet(book.create_sheet(ORGANIZATIONS_TAB), (*ORGANIZATION_COLUMNS, *extra_org_headers), organizations)
    path = Path(tmp_path) / "directory.xlsx"
    book.save(path)
    return path


def contact_values(**overrides):
    values = {"Contact Full Name": "Ada Obi", "Organization Name": "Acme", "Email Address": "ada@x.org"}
    return {**values, **overrides}


def organization_values(**overrides):
    values = {
        "Organization Name": "Acme",
        "Short Name": "AC",
        "Countries of Operation": "Kenya",
        "Regions/States of Operation": "Nairobi",
        "Primary Sector(s)": "Health",
    }
    return {**values, **overrides}


def plan_for(tmp_path, contacts, organizations=()) -> ImportPlan:
    path = make_workbook(
        tmp_path,
        contacts,
        organizations,
        extra_org_headers=(CONNECT_ID_COLUMN,),
        extra_contact_headers=(CONNECT_ID_COLUMN,),
    )
    return build_import_plan(load_workbook(path))


def _write_sheet(sheet, headers, rows):
    sheet.append(list(headers))
    for row_number, row in enumerate(rows, start=2):
        for column_number, header in enumerate(headers, start=1):
            value = row.get(header)
            if isinstance(value, tuple):
                text, target = value
                cell = sheet.cell(row=row_number, column=column_number, value=text)
                cell.hyperlink = target
            else:
                sheet.cell(row=row_number, column=column_number, value=value)
