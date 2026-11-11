import re
from dataclasses import dataclass, field

from commcare_connect.organization.llo_import.workbook import CONNECT_ID_COLUMN, SheetRow

NAME_COLUMN = "Contact Full Name"
ORGANIZATION_COLUMN = "Organization Name"
EMAIL_COLUMN = "Email Address"
TITLE_COLUMN = "Role / Title"
MAIN_POC_COLUMN = "Main POC?"
PHONE_COLUMN = "Phone Number"
NOTES_COLUMN = "Contact Notes (e.g. best way to contact)"

FORM_FIELD_COLUMNS = {
    "name": NAME_COLUMN,
    "title": TITLE_COLUMN,
    "email": EMAIL_COLUMN,
    "phone": PHONE_COLUMN,
    "notes": NOTES_COLUMN,
}
FORMULA_REASON = "formulas aren't read; replace the formula with its value"
ADDRESS_SEPARATORS = re.compile(r"[,;\s]+")


@dataclass(frozen=True)
class Problem:
    column: str | None
    value: str | None
    reason: str


@dataclass
class ContactRow:
    row: SheetRow
    name: str
    email: str
    organization_name: str
    is_main_poc: bool
    connect_id: str
    form_data: dict[str, str]
    problems: list[Problem] = field(default_factory=list)


def map_contact_row(row: SheetRow) -> ContactRow:
    problems: list[Problem] = []
    texts = {}
    for column in (NAME_COLUMN, ORGANIZATION_COLUMN, EMAIL_COLUMN, TITLE_COLUMN, PHONE_COLUMN, NOTES_COLUMN):
        texts[column] = _text_without_formula(row, column, problems)
    main_poc_text = _text_without_formula(row, MAIN_POC_COLUMN, problems)

    _require(row, NAME_COLUMN, texts, problems)
    _require(row, ORGANIZATION_COLUMN, texts, problems)
    _require(row, EMAIL_COLUMN, texts, problems)
    _check_single_address(texts[EMAIL_COLUMN], problems)
    is_main_poc = _read_main_poc(main_poc_text, problems)

    contact = ContactRow(
        row,
        texts[NAME_COLUMN],
        texts[EMAIL_COLUMN],
        texts[ORGANIZATION_COLUMN],
        is_main_poc,
        cell_text(row.values.get(CONNECT_ID_COLUMN)),
        _form_data(texts, is_main_poc),
    )
    contact.problems = problems
    return contact


def cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _form_data(texts: dict[str, str], is_main_poc: bool) -> dict[str, str]:
    form_data = {field_name: texts[column] for field_name, column in FORM_FIELD_COLUMNS.items()}
    form_data["is_main_poc"] = str(is_main_poc)
    return form_data


def _text_without_formula(row: SheetRow, column: str, problems: list[Problem]) -> str:
    text = cell_text(row.values.get(column))
    if text.startswith("="):
        problems.append(Problem(column, text, FORMULA_REASON))
        return ""
    return text


def _require(row: SheetRow, column: str, texts: dict[str, str], problems: list[Problem]) -> None:
    if texts[column]:
        return
    if cell_text(row.values.get(column)).startswith("="):
        return  # already reported as a formula
    problems.append(Problem(column, None, "required value is missing"))


def _check_single_address(email: str, problems: list[Problem]) -> None:
    addresses = [address for address in ADDRESS_SEPARATORS.split(email) if address]
    if len(addresses) > 1:
        problems.append(Problem(EMAIL_COLUMN, email, "must hold exactly one email address"))


def _read_main_poc(text: str, problems: list[Problem]) -> bool:
    if text in ("", "No"):
        return False
    if text == "Yes":
        return True
    problems.append(Problem(MAIN_POC_COLUMN, text, "must be Yes, No or empty"))
    return False
