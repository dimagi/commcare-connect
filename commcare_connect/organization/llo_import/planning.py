import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from django.db.models.functions import Lower

from commcare_connect.opportunity.models import Country
from commcare_connect.organization.forms import ContactForm, OrganizationDirectoryForm
from commcare_connect.organization.llo_import.mapping import (
    CONTACT_EMAILS_COLUMN,
    COUNTRIES_COLUMN,
    EMAIL_COLUMN,
    EOI_COLUMN,
    FORM_FIELD_COLUMNS,
    HAS_USED_CONNECT_COLUMN,
    MAIN_POC_COLUMN,
    ORG_TEXT_FIELD_COLUMNS,
    ORG_WHOLE_NUMBER_FIELD_COLUMNS,
    ORGANIZATION_COLUMN,
    SECTORS_COLUMN,
    TEAM_SIZE_COLUMN,
    ContactRow,
    OrganizationRow,
    Problem,
    map_contact_row,
    map_organization_row,
)
from commcare_connect.organization.llo_import.workbook import CONNECT_ID_COLUMN, LLOWorkbook
from commcare_connect.organization.models import Contact, Organization, PrimarySector

CONTACT_FIELD_COLUMNS = {**FORM_FIELD_COLUMNS, "is_main_poc": MAIN_POC_COLUMN, "organization": ORGANIZATION_COLUMN}
ORGANIZATION_FIELD_COLUMNS = {
    **ORG_TEXT_FIELD_COLUMNS,
    **ORG_WHOLE_NUMBER_FIELD_COLUMNS,
    "countries": COUNTRIES_COLUMN,
    "primary_sectors": SECTORS_COLUMN,
    "has_used_connect": HAS_USED_CONNECT_COLUMN,
    "team_size": TEAM_SIZE_COLUMN,
    "contact_emails": CONTACT_EMAILS_COLUMN,
    "eoi_links": EOI_COLUMN,
}
BLOCKED_ORGANIZATION_REASON = "organization can't be created"


class Status(StrEnum):
    TO_CREATE = "to_create"
    CREATED = "created"
    ALREADY_EXISTED = "already_existed"
    ALREADY_IMPORTED = "already_imported"
    BLOCKED = "blocked"
    NOT_NEEDED = "not_needed"


@dataclass
class OrganizationPlan:
    source: OrganizationRow | None
    organization: Organization | None
    status: Status
    problems: list[Problem]
    contacts: list["ContactPlan"] = field(default_factory=list)


@dataclass
class ContactPlan:
    source: ContactRow
    organization_plan: OrganizationPlan | None
    contact: Contact | None
    status: Status
    problems: list[Problem]


@dataclass
class ImportPlan:
    path: Path
    contacts: list[ContactPlan]
    organizations: list[OrganizationPlan]


def build_import_plan(workbook: LLOWorkbook) -> ImportPlan:
    """Decides, without writing anything, what importing the workbook would create and what blocks each row."""
    countries = dict(Country.objects.values_list("name", "code"))
    sectors = dict(PrimarySector.objects.values_list("name", "pk"))
    contact_rows = [map_contact_row(row) for row in workbook.contacts]
    organization_rows = [map_organization_row(row, countries, sectors) for row in workbook.organizations]
    return _Planner(contact_rows, organization_rows).build(workbook.path)


@dataclass
class _Planner:
    contact_rows: list[ContactRow]
    organization_rows: list[OrganizationRow]
    organization_plans: dict[tuple, OrganizationPlan] = field(default_factory=dict)
    resolved_names: dict[str, tuple[OrganizationPlan | None, list[Problem]]] = field(default_factory=dict)
    has_main_contact: dict[int, bool] = field(default_factory=dict)

    def __post_init__(self):
        self.existing_contacts = _existing_contacts_by_email(self.contact_rows)

    def build(self, path: Path) -> ImportPlan:
        contact_plans = [self._plan_skipped_contact(row) for row in self.contact_rows]
        attempted = [row for row, plan in zip(self.contact_rows, contact_plans) if plan is None]
        sheet_problems = _sheet_wide_problems(attempted)
        for index, row in enumerate(self.contact_rows):
            if contact_plans[index] is None:
                contact_plans[index] = self._plan_contact(row, sheet_problems[row.row.number])
        for organization_plan in self.organization_plans.values():
            _mark_not_needed(organization_plan)
        return ImportPlan(path, contact_plans, list(self.organization_plans.values()))

    def _plan_skipped_contact(self, row: ContactRow) -> ContactPlan | None:
        """Plans a row that is not attempted because its contact is already in Connect, or None to attempt it."""
        if row.connect_id:
            contact, problem = _contact_with_id(row.connect_id)
            if contact:
                return ContactPlan(row, None, contact, Status.ALREADY_IMPORTED, [])
            return ContactPlan(row, None, None, Status.BLOCKED, [problem])
        if existing := self.existing_contacts.get(row.email.lower()):
            return ContactPlan(row, None, existing, Status.ALREADY_EXISTED, [])
        return None

    def _plan_contact(self, row: ContactRow, sheet_problems: list[Problem]) -> ContactPlan:
        organization_plan, organization_problems = self._resolve_organization(row.organization_name)
        problems = [*row.problems, *sheet_problems, *organization_problems]
        if organization_plan is not None:
            problems.extend(self._organization_plan_problems(row, organization_plan))
        problems.extend(_contact_form_problems(row, organization_plan, problems))

        status = Status.BLOCKED if problems else Status.TO_CREATE
        contact_plan = ContactPlan(row, organization_plan, None, status, problems)
        if organization_plan is not None:
            organization_plan.contacts.append(contact_plan)
        return contact_plan

    def _organization_plan_problems(self, row: ContactRow, organization_plan: OrganizationPlan) -> list[Problem]:
        if organization_plan.status == Status.BLOCKED:
            return [Problem(ORGANIZATION_COLUMN, row.organization_name, BLOCKED_ORGANIZATION_REASON)]
        organization = organization_plan.organization
        if row.is_main_poc and organization is not None and self._has_main_contact(organization):
            reason = f"{organization.name} already has a main contact in Connect"
            return [Problem(MAIN_POC_COLUMN, "Yes", reason)]
        return []

    def _has_main_contact(self, organization: Organization) -> bool:
        if organization.pk not in self.has_main_contact:
            main_contacts = Contact.objects.filter(organization=organization, is_main_poc=True)
            self.has_main_contact[organization.pk] = main_contacts.exists()
        return self.has_main_contact[organization.pk]

    def _resolve_organization(self, name: str) -> tuple[OrganizationPlan | None, list[Problem]]:
        if not name:
            return None, []  # the missing name is already a mapping problem
        if name not in self.resolved_names:
            self.resolved_names[name] = self._find_organization(name)
        return self.resolved_names[name]

    def _find_organization(self, name: str) -> tuple[OrganizationPlan | None, list[Problem]]:
        # TODO: block archived organizations once Organization.is_archived is on main (ticket 2936).
        tab_rows = [row for row in self.organization_rows if row.name == name]
        linked_rows = [row for row in tab_rows if row.connect_id]
        if len(linked_rows) == 1:
            return self._plan_linked_organization(linked_rows[0]), []

        matches = list(Organization.objects.filter(name=name))
        if len(matches) == 1:
            source = tab_rows[0] if len(tab_rows) == 1 else None
            return self._plan_organization(("organization", matches[0].pk), source, matches[0]), []
        if len(matches) > 1:
            slugs = ", ".join(organization.slug for organization in matches)
            return None, [Problem(ORGANIZATION_COLUMN, name, f"matches several organizations in Connect: {slugs}")]

        if len(tab_rows) == 1:
            return self._plan_new_organization(tab_rows[0]), []
        if tab_rows:
            reason = f"several Organizations rows have this name: rows {_row_numbers(tab_rows)}"
            return None, [Problem(ORGANIZATION_COLUMN, name, reason)]
        return None, [
            Problem(ORGANIZATION_COLUMN, name, "not found in Connect or on the Organizations tab"),
            *self._case_hints(name),
        ]

    def _plan_linked_organization(self, row: OrganizationRow) -> OrganizationPlan:
        organization = Organization.objects.filter(slug=row.connect_id).first()
        if organization is not None:
            return self._plan_organization(("organization", organization.pk), row, organization)
        problem = Problem(CONNECT_ID_COLUMN, row.connect_id, "no organization in Connect has this ID")
        return self._plan_organization(("row", row.row.number), row, None, [problem])

    def _plan_new_organization(self, row: OrganizationRow) -> OrganizationPlan:
        problems = list(row.problems)
        if existing := Organization.objects.filter(name__iexact=row.name).first():
            problems.append(Problem(ORGANIZATION_COLUMN, row.name, f"differs only in case from '{existing.name}'"))
        form = OrganizationDirectoryForm(data=row.form_data)
        problems.extend(form_problems(form, ORGANIZATION_FIELD_COLUMNS, problems))
        return self._plan_organization(("row", row.row.number), row, None, problems)

    def _plan_organization(self, key, source, organization, problems=None) -> OrganizationPlan:
        if key not in self.organization_plans:
            if problems:
                status = Status.BLOCKED
            else:
                status = Status.TO_CREATE if organization is None else Status.ALREADY_EXISTED
            self.organization_plans[key] = OrganizationPlan(source, organization, status, problems or [])
        return self.organization_plans[key]

    def _case_hints(self, name: str) -> list[Problem]:
        hints = [
            Problem(ORGANIZATION_COLUMN, name, f"differs only in case from Organizations row {row.row.number}")
            for row in self.organization_rows
            if row.name.lower() == name.lower()
        ]
        hints.extend(
            Problem(ORGANIZATION_COLUMN, name, f"differs only in case from '{organization.name}'")
            for organization in Organization.objects.filter(name__iexact=name)
        )
        return hints


def _existing_contacts_by_email(rows: list[ContactRow]) -> dict[str, Contact]:
    emails = {row.email.lower() for row in rows if row.email}
    contacts = (
        Contact.objects.annotate(lower_email=Lower("email"))
        .filter(lower_email__in=emails)
        .select_related("organization")
    )
    return {contact.lower_email: contact for contact in contacts}


def _contact_with_id(connect_id: str) -> tuple[Contact | None, Problem | None]:
    try:
        contact_id = uuid.UUID(connect_id)
    except ValueError:
        return None, Problem(CONNECT_ID_COLUMN, connect_id, "not a valid Connect ID")
    contact = Contact.objects.filter(contact_id=contact_id).select_related("organization").first()
    if contact is None:
        return None, Problem(CONNECT_ID_COLUMN, connect_id, "no contact in Connect has this ID")
    return contact, None


def _sheet_wide_problems(rows: list[ContactRow]) -> dict[int, list[Problem]]:
    problems = defaultdict(list)
    _report_shared_values(
        rows,
        key=lambda row: row.email.lower(),
        problem=lambda row, numbers: Problem(EMAIL_COLUMN, row.email, f"same email on Contacts rows {numbers}"),
        problems=problems,
    )
    _report_shared_values(
        [row for row in rows if row.is_main_poc],
        key=lambda row: row.organization_name,
        problem=lambda row, numbers: Problem(
            MAIN_POC_COLUMN, "Yes", f"several rows mark a main POC for {row.organization_name}: rows {numbers}"
        ),
        problems=problems,
    )
    return problems


def _report_shared_values(
    rows: list[ContactRow],
    key: Callable[[ContactRow], str],
    problem: Callable[[ContactRow, str], Problem],
    problems: dict[int, list[Problem]],
) -> None:
    """Adds a problem to every row that shares a non-empty key with another row."""
    rows_by_key = defaultdict(list)
    for row in rows:
        if key(row):
            rows_by_key[key(row)].append(row)
    for shared in rows_by_key.values():
        if len(shared) > 1:
            numbers = _row_numbers(shared)
            for row in shared:
                problems[row.row.number].append(problem(row, numbers))


def _contact_form_problems(
    row: ContactRow, organization_plan: OrganizationPlan | None, reported: list[Problem]
) -> list[Problem]:
    """Validates the row with ContactForm; the organization is checked only when it already exists."""
    organization = organization_plan.organization if organization_plan else None
    if organization is None:
        form = ContactForm(data=row.form_data)
        return form_problems(form, CONTACT_FIELD_COLUMNS, reported, ignored_fields=("organization",))
    form = ContactForm(data={**row.form_data, "organization": organization.pk})
    return form_problems(form, CONTACT_FIELD_COLUMNS, reported)


def form_problems(form, field_columns: dict[str, str], reported: list[Problem], ignored_fields=()) -> list[Problem]:
    """Turns the form's errors into problems, skipping columns that already have a problem."""
    reported_columns = {problem.column for problem in reported}
    problems = []
    for field_name, messages in form.errors.items():
        column = field_columns.get(field_name)
        if field_name in ignored_fields or (column is not None and column in reported_columns):
            continue
        value = form.data.get(field_name)
        value = value if isinstance(value, str) and value else None
        problems.extend(Problem(column, value, message) for message in messages)
    return problems


def _mark_not_needed(organization_plan: OrganizationPlan) -> None:
    if organization_plan.status != Status.TO_CREATE:
        return
    if not any(contact.status == Status.TO_CREATE for contact in organization_plan.contacts):
        organization_plan.status = Status.NOT_NEEDED


def _row_numbers(rows) -> str:
    return ", ".join(str(row.row.number) for row in rows)
