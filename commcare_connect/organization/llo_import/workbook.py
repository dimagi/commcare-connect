from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import openpyxl

if TYPE_CHECKING:
    from commcare_connect.organization.llo_import.planning import ImportPlan

CONTACTS_TAB = "Contacts"
ORGANIZATIONS_TAB = "Organizations"
CONNECT_ID_COLUMN = "Connect ID"
IMPORT_STATUS_COLUMN = "Connect Import Status"
_STATUS_TEXT = {"created": "Created", "already_existed": "Already existed"}
_WRITTEN_STATUSES = frozenset(_STATUS_TEXT)

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

    duplicates = sorted(header for header, count in Counter(headers.values()).items() if count > 1)
    if duplicates:
        raise WorkbookError(f"Duplicate column(s) on the {tab_name} tab: {', '.join(duplicates)}")
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


def output_path_for(path: Path, now: datetime) -> Path:
    """Names the workbook copy next to the input, stamped with the given time."""
    path = Path(path)
    return path.with_name(f"{path.stem} - imported {now:%Y-%m-%d %H%M}{path.suffix}")


def write_results(workbook: LLOWorkbook, plan: "ImportPlan", output_path: Path) -> None:
    """Writes the Connect ID and import status of each planned row into the workbook and saves it to output_path."""
    contacts_sheet = workbook.book[CONTACTS_TAB]
    organizations_sheet = workbook.book[ORGANIZATIONS_TAB]
    contact_columns = _result_columns(contacts_sheet)
    organization_columns = _result_columns(organizations_sheet)

    for contact_plan in plan.contacts:
        if contact_plan.status in _WRITTEN_STATUSES and contact_plan.contact is not None:
            _write_row(
                contacts_sheet,
                contact_columns,
                contact_plan.source.row.number,
                str(contact_plan.contact.contact_id),
                contact_plan.status,
            )

    for organization_plan in plan.organizations:
        if organization_plan.status not in _WRITTEN_STATUSES or organization_plan.source is None:
            continue
        _write_row(
            organizations_sheet,
            organization_columns,
            organization_plan.source.row.number,
            organization_plan.organization.slug,
            organization_plan.status,
            keep_existing_id=True,
        )

    workbook.book.save(output_path)


def _result_columns(sheet) -> tuple[int, int]:
    """Returns the column numbers of the ID and status columns, appending whichever header is missing."""
    existing = {
        cell.value.strip(): cell.column for cell in sheet[1] if isinstance(cell.value, str) and cell.value.strip()
    }
    last_column = _last_column_in_use(sheet)
    columns = []
    for header in (CONNECT_ID_COLUMN, IMPORT_STATUS_COLUMN):
        column = existing.get(header)
        if column is None:
            last_column += 1
            column = last_column
            sheet.cell(row=1, column=column, value=header)
        columns.append(column)
    return tuple(columns)


def _last_column_in_use(sheet) -> int:
    """Returns the rightmost column holding a value, ignoring columns that only carry formatting."""
    return max((cell.column for row in sheet.iter_rows() for cell in row if cell.value is not None), default=0)


def _write_row(sheet, columns, row_number, connect_id, status, keep_existing_id=False):
    id_cell = sheet.cell(row=row_number, column=columns[0])
    if keep_existing_id and _stripped(id_cell.value):
        return
    id_cell.value = connect_id
    sheet.cell(row=row_number, column=columns[1], value=_STATUS_TEXT[status])


def _stripped(value):
    return value.strip() if isinstance(value, str) else value
