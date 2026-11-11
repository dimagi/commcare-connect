from dataclasses import dataclass
from pathlib import Path

import openpyxl

CONTACTS_TAB = "Contacts"
ORGANIZATIONS_TAB = "Organizations"
CONNECT_ID_COLUMN = "Connect ID"
IMPORT_STATUS_COLUMN = "Connect Import Status"

CONTACT_COLUMNS = (
    "Contact Full Name",
    "Organization Name",
    "Email Address",
    "Role / Title",
    "Main POC?",
    "Phone Number",
    "Contact Notes (e.g. best way to contact)",
)

ORGANIZATION_COLUMNS = (
    "Organization Name",
    "Short Name",
    "Countries of Operation",
    "Regions/States of Operation",
    "Primary Sector(s)",
    "Has Used Connect",
    "Year of Establishment",
    "Org Team Size",
    "No. of FLWs Managed",
    "Website",
    "Office Address",
    "Email addresses from Contacts sheet",
    "EOIs Applied for (Add Link to EOI)",
    "Organization Notes",
    "Latest MSA Link",
    "Latest Work Order Link",
)


class WorkbookError(Exception):
    pass


@dataclass(frozen=True)
class SheetRow:
    number: int
    values: dict[str, object]
    hyperlinks: dict[str, str]


@dataclass
class LLOWorkbook:
    path: Path
    book: openpyxl.Workbook
    contacts: list[SheetRow]
    organizations: list[SheetRow]


def load_workbook(path: Path) -> LLOWorkbook:
    book = openpyxl.load_workbook(path, data_only=False)
    missing_tabs = [tab for tab in (CONTACTS_TAB, ORGANIZATIONS_TAB) if tab not in book.sheetnames]
    if missing_tabs:
        raise WorkbookError(f"Missing tab(s): {', '.join(missing_tabs)}")
    return LLOWorkbook(
        path=Path(path),
        book=book,
        contacts=_read_sheet(book[CONTACTS_TAB], CONTACTS_TAB, CONTACT_COLUMNS),
        organizations=_read_sheet(book[ORGANIZATIONS_TAB], ORGANIZATIONS_TAB, ORGANIZATION_COLUMNS),
    )


def _read_sheet(sheet, tab_name, required_columns) -> list[SheetRow]:
    headers = {}
    for cell in sheet[1]:
        if isinstance(cell.value, str) and cell.value.strip():
            headers[cell.column] = cell.value.strip()

    missing = [column for column in required_columns if column not in headers.values()]
    if missing:
        raise WorkbookError(f"Missing column(s) on the {tab_name} tab: {', '.join(missing)}")

    rows = []
    for sheet_row in sheet.iter_rows(min_row=2):
        values = {}
        hyperlinks = {}
        for cell in sheet_row:
            header = headers.get(cell.column)
            if header is None:
                continue
            values[header] = _blank_to_none(cell.value)
            if cell.hyperlink is not None and cell.hyperlink.target:
                hyperlinks[header] = cell.hyperlink.target
        if any(value is not None for value in values.values()):
            rows.append(SheetRow(number=sheet_row[0].row, values=values, hyperlinks=hyperlinks))
    return rows


def _blank_to_none(value):
    if isinstance(value, str) and not value.strip():
        return None
    return value
