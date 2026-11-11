import csv
import re
from dataclasses import dataclass, field

from commcare_connect.organization.llo_import.workbook import CONNECT_ID_COLUMN, SheetRow
from commcare_connect.organization.models import TeamSizeRange

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
COUNTRIES_COLUMN = "Countries of Operation"
SECTORS_COLUMN = "Primary Sector(s)"
HAS_USED_CONNECT_COLUMN = "Has Used Connect"
YEAR_COLUMN = "Year of Establishment"
TEAM_SIZE_COLUMN = "Org Team Size"
FLWS_COLUMN = "No. of FLWs Managed"
CONTACT_EMAILS_COLUMN = "Email addresses from Contacts sheet"
EOI_COLUMN = "EOIs Applied for (Add Link to EOI)"

ORG_TEXT_FIELD_COLUMNS = {
    "name": ORGANIZATION_COLUMN,
    "short_name": "Short Name",
    "regions": "Regions/States of Operation",
    "website": "Website",
    "office_address": "Office Address",
    "notes": "Organization Notes",
    "latest_msa_link": "Latest MSA Link",
    "latest_work_order_link": "Latest Work Order Link",
}
ORG_WHOLE_NUMBER_FIELD_COLUMNS = {"year_of_establishment": YEAR_COLUMN, "flws_managed": FLWS_COLUMN}
UNKNOWN_NAME_REASONS = {
    COUNTRIES_COLUMN: "unknown country; names must match Connect's country list exactly",
    SECTORS_COLUMN: "unknown sector; it must match a primary sector in Connect exactly, case included",
}
FORMULA_REASON = "formulas aren't read; replace the formula with its value"
ADDRESS_SEPARATORS = re.compile(r"[,;\s]+")
HYPERLINK_FORMULA = re.compile(r'^=HYPERLINK\(\s*"([^"]+)"', re.IGNORECASE)
URL_IN_TEXT = re.compile(r"https?://\S+")


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


@dataclass
class OrganizationRow:
    row: SheetRow
    name: str
    connect_id: str
    form_data: dict[str, object]
    problems: list[Problem]


def map_organization_row(row: SheetRow, countries: dict[str, str], sectors: dict[str, int]) -> OrganizationRow:
    problems: list[Problem] = []
    form_data: dict[str, object] = {}
    for field_name, column in ORG_TEXT_FIELD_COLUMNS.items():
        form_data[field_name] = _text_without_formula(row, column, problems)
    for field_name, column in ORG_WHOLE_NUMBER_FIELD_COLUMNS.items():
        form_data[field_name] = _read_whole_number(row, column, problems)
    form_data["countries"] = _look_up_names(row, COUNTRIES_COLUMN, split_countries, countries, problems)
    form_data["primary_sectors"] = _look_up_names(row, SECTORS_COLUMN, split_sectors, sectors, problems)
    form_data["has_used_connect"] = _read_has_used_connect(
        _text_without_formula(row, HAS_USED_CONNECT_COLUMN, problems), problems
    )
    form_data["team_size"] = _read_team_size(_text_without_formula(row, TEAM_SIZE_COLUMN, problems), problems)
    emails = _text_without_formula(row, CONTACT_EMAILS_COLUMN, problems)
    form_data["contact_emails"] = "\n".join(address for address in ADDRESS_SEPARATORS.split(emails) if address)
    form_data["eoi_links"] = _read_eoi_links(row, problems)

    return OrganizationRow(row, form_data["name"], cell_text(row.values.get(CONNECT_ID_COLUMN)), form_data, problems)


def split_countries(value: str) -> list[str]:
    parsed = next(csv.reader([value], skipinitialspace=True), [])
    return [name.strip() for name in parsed if name.strip()]


def split_sectors(value: str) -> list[str]:
    names = []
    current = []
    depth = 0
    for character in value:
        if character == "," and depth == 0:
            names.append("".join(current))
            current = []
            continue
        if character == "(":
            depth += 1
        elif character == ")" and depth > 0:
            depth -= 1
        current.append(character)
    names.append("".join(current))
    return [name.strip() for name in names if name.strip()]


def eoi_links(text: str, hyperlink: str | None) -> tuple[list[str], list[str]]:
    formula = HYPERLINK_FORMULA.match(text.strip())
    if formula:
        return [formula.group(1)], []

    urls: list[str] = []
    unlinked: list[str] = []
    hyperlink_unused = hyperlink is not None
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        line_urls = URL_IN_TEXT.findall(line)
        if not line_urls and hyperlink_unused:
            line_urls = [hyperlink]
            hyperlink_unused = False
        if line_urls:
            urls.extend(url for url in line_urls if url not in urls)
        else:
            unlinked.append(line)
    return urls, unlinked


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


def _read_whole_number(row: SheetRow, column: str, problems: list[Problem]) -> int | str:
    text = _text_without_formula(row, column, problems)
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is None or not number.is_integer():
        if not text.startswith("="):
            problems.append(Problem(column, text, "must be a whole number"))
        return ""
    return int(number)


def _look_up_names(row: SheetRow, column: str, split, known: dict, problems: list[Problem]) -> list:
    reason = UNKNOWN_NAME_REASONS[column]
    keys = []
    for name in split(_text_without_formula(row, column, problems)):
        if name not in known:
            problems.append(Problem(column, name, reason))
        elif known[name] not in keys:
            keys.append(known[name])
    return keys


def _read_has_used_connect(text: str, problems: list[Problem]) -> str:
    if text in ("X", "x"):
        return "True"
    if text:
        problems.append(Problem(HAS_USED_CONNECT_COLUMN, text, "must be X or empty"))
    return "False"


def _read_team_size(text: str, problems: list[Problem]) -> str:
    if not text or text in TeamSizeRange.values:
        return text
    problems.append(Problem(TEAM_SIZE_COLUMN, text, f"must be one of {', '.join(TeamSizeRange.values)}"))
    return ""


def _read_eoi_links(row: SheetRow, problems: list[Problem]) -> str:
    text = cell_text(row.values.get(EOI_COLUMN))
    urls, unlinked = eoi_links(text, row.hyperlinks.get(EOI_COLUMN))
    problems.extend(Problem(EOI_COLUMN, line, "not a link; add the EOI's URL or remove the line") for line in unlinked)
    return "\n".join(urls)
