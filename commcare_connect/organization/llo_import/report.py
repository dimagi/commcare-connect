import re
from collections.abc import Callable
from pathlib import Path

from commcare_connect.organization.llo_import.mapping import Problem
from commcare_connect.organization.llo_import.planning import ContactPlan, ImportPlan, OrganizationPlan, Status
from commcare_connect.organization.llo_import.workbook import CONTACTS_TAB, ORGANIZATIONS_TAB

CREATING = (Status.TO_CREATE, Status.CREATED)
LINE_BREAK = re.compile(r"\r\n?|\n")


def render_report(plan: ImportPlan, committed: bool, output_path: Path | None) -> str:
    """Describes, as plain text, what the import would do (dry run) or did (real run)."""
    to_create = "created" if committed else "to create"
    sections = [
        _header(plan, committed, output_path),
        _totals(plan, to_create),
        _section(f"Organizations {to_create}", _new_organization_lines(plan)),
        _section("Existing organizations (marked Already existed)", _existing_organization_lines(plan, to_create)),
        _section(
            "Blocked organizations",
            _records_with_problems(
                _with_status(plan.organizations, Status.BLOCKED),
                ORGANIZATIONS_TAB,
                summary=_organization_label,
                footer=_blocked_contacts_line,
            ),
        ),
        _section(
            "Organizations not created: no importable contacts",
            _records_with_problems(
                _with_status(plan.organizations, Status.NOT_NEEDED), ORGANIZATIONS_TAB, summary=_needed_by_label
            ),
        ),
        _section(
            "Blocked contacts",
            _records_with_problems(
                _with_status(plan.contacts, Status.BLOCKED), CONTACTS_TAB, summary=_blocked_contact_label
            ),
        ),
        _section("Skipped: email already in Connect", _skipped_contact_lines(plan)),
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def _header(plan: ImportPlan, committed: bool, output_path: Path | None) -> str:
    if committed:
        return (
            f"REAL RUN — records were saved. Workbook: {plan.path.name}. Output copy: {output_path or 'not written'}"
        )
    return f"DRY RUN — nothing was written. Workbook: {plan.path.name}"


def _totals(plan: ImportPlan, to_create: str) -> str:
    def count(plans, *statuses):
        return len(_with_status(plans, *statuses))

    return _section(
        "Totals",
        [
            f"Contacts {to_create}: {count(plan.contacts, *CREATING)}",
            f"Organizations {to_create}: {count(plan.organizations, *CREATING)}",
            f"Contacts skipped, email already in Connect: {count(plan.contacts, Status.ALREADY_EXISTED)}",
            f"Rows already imported (have a Connect ID): {count(plan.contacts, Status.ALREADY_IMPORTED)}",
            f"Contacts blocked: {count(plan.contacts, Status.BLOCKED)}",
            f"Organizations blocked: {count(plan.organizations, Status.BLOCKED)}",
            f"Organizations not needed: {count(plan.organizations, Status.NOT_NEEDED)}",
        ],
    )


def _new_organization_lines(plan: ImportPlan) -> list[str]:
    return [
        f"{_organization_label(organization)} — for Contacts rows {_contact_rows(organization, *CREATING)}"
        for organization in _with_status(plan.organizations, *CREATING)
    ]


def _existing_organization_lines(plan: ImportPlan, to_create: str) -> list[str]:
    lines = []
    for organization in _with_status(plan.organizations, Status.ALREADY_EXISTED):
        label = f"{organization.organization.name} ({organization.organization.slug})"
        rows = _contact_rows(organization, *CREATING)
        lines.append(
            f"{label} — contacts {to_create}: Contacts rows {rows}" if rows else f"{label} — no contacts {to_create}"
        )
    return lines


def _skipped_contact_lines(plan: ImportPlan) -> list[str]:
    return [
        f"{_contact_label(contact)}, already belongs to {contact.contact.organization.name}"
        for contact in _with_status(plan.contacts, Status.ALREADY_EXISTED)
    ]


def _section(title: str, lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join([title, *(f"  {line}" for line in lines)])


def _with_status(plans: list, *statuses: Status) -> list:
    return [item for item in plans if item.status in statuses]


def _records_with_problems(records: list, tab: str, summary: Callable, footer: Callable | None = None) -> list[str]:
    """Lists each record's summary line, then its problems, then the optional footer line."""
    lines = []
    for record in records:
        lines.append(summary(record))
        lines.extend(_problem_lines(tab, record.source, record.problems))
        if footer is not None:
            lines.append(footer(record))
    return lines


def _organization_label(organization: OrganizationPlan) -> str:
    if organization.source is None:
        return f"{organization.organization.name} (in Connect)"
    return f"{ORGANIZATIONS_TAB} row {organization.source.row.number}: {organization.source.name}"


def _blocked_contacts_line(organization: OrganizationPlan) -> str:
    return f"  Contacts blocked by it: rows {_contact_rows(organization, Status.BLOCKED)}"


def _needed_by_label(organization: OrganizationPlan) -> str:
    return f"{_organization_label(organization)} — needed by Contacts rows {_contact_rows(organization)}"


def _blocked_contact_label(contact: ContactPlan) -> str:
    return f"{_contact_label(contact)}, organization {contact.source.organization_name or '(none)'}"


def _contact_label(contact: ContactPlan) -> str:
    return f"{CONTACTS_TAB} row {contact.source.row.number}: {contact.source.name} <{contact.source.email}>"


def _contact_rows(organization: OrganizationPlan, *statuses: Status) -> str:
    contacts = [contact for contact in organization.contacts if not statuses or contact.status in statuses]
    return ", ".join(str(contact.source.row.number) for contact in contacts)


def _problem_lines(tab: str, source, problems: list[Problem]) -> list[str]:
    location = f"{tab} row {source.row.number}" if source is not None else tab
    return [f"  - {_problem_text(location, problem)}" for problem in problems]


def _problem_text(location: str, problem: Problem) -> str:
    parts = [location]
    if problem.column:
        parts.append(f'column "{problem.column}"')
    if problem.value:
        parts.append(f'value "{LINE_BREAK.sub(" / ", problem.value)}"')
    return f"{', '.join(parts)}: {problem.reason}"
