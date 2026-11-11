from pathlib import Path

import openpyxl

from commcare_connect.organization.llo_import.workbook import (
    CONTACT_COLUMNS,
    CONTACTS_TAB,
    ORGANIZATION_COLUMNS,
    ORGANIZATIONS_TAB,
)


def make_workbook(tmp_path, contacts, organizations, extra_org_headers=()) -> Path:
    book = openpyxl.Workbook()
    book.remove(book.active)
    _write_sheet(book.create_sheet(CONTACTS_TAB), CONTACT_COLUMNS, contacts)
    _write_sheet(book.create_sheet(ORGANIZATIONS_TAB), (*ORGANIZATION_COLUMNS, *extra_org_headers), organizations)
    path = Path(tmp_path) / "directory.xlsx"
    book.save(path)
    return path


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
