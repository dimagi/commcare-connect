import pghistory
from django.db import IntegrityError, transaction

from commcare_connect.organization.forms import ContactForm, OrganizationDirectoryForm
from commcare_connect.organization.llo_import.mapping import (
    EMAIL_COLUMN,
    MAIN_POC_COLUMN,
    ORGANIZATION_COLUMN,
    Problem,
)
from commcare_connect.organization.llo_import.planning import (
    BLOCKED_ORGANIZATION_REASON,
    CONTACT_FIELD_COLUMNS,
    ORGANIZATION_FIELD_COLUMNS,
    ContactPlan,
    ImportPlan,
    OrganizationPlan,
    Status,
    form_problems,
)
from commcare_connect.organization.models import Contact, Organization
from commcare_connect.users.models import User

EMAIL_TAKEN_REASON = "a contact with this email is already in Connect"


class _NoContactSaved(Exception):
    """Raised inside a new organization's transaction to roll it back when none of its contacts were saved."""


def execute_import_plan(plan: ImportPlan, user: User) -> None:
    """Saves the plan's new organizations and contacts, re-checking each record just before it is saved.

    Plans are updated in place: a saved record becomes CREATED and holds the saved instance, and a record that
    fails its re-check becomes BLOCKED with the reason added to its problems.
    """
    with pghistory.context(username=user.username, user_email=user.email):
        for organization_plan in plan.organizations:
            if organization_plan.status == Status.TO_CREATE:
                _create_organization_with_contacts(organization_plan, user)
            elif organization_plan.status == Status.ALREADY_EXISTED:
                with transaction.atomic():
                    _create_contacts(organization_plan, organization_plan.organization, user)


def _create_organization_with_contacts(organization_plan: OrganizationPlan, user: User) -> None:
    """Saves a new organization and its contacts together, rolling the organization back if no contact saves."""
    try:
        with transaction.atomic():
            organization = _save_organization(organization_plan, user)
            if organization is None:
                return
            _create_contacts(organization_plan, organization, user)
            if not any(contact.status == Status.CREATED for contact in organization_plan.contacts):
                raise _NoContactSaved
    except _NoContactSaved:
        organization_plan.status = Status.NOT_NEEDED
        organization_plan.organization = None


def _save_organization(organization_plan: OrganizationPlan, user: User) -> Organization | None:
    """Re-validates and saves the new organization, or blocks it and its contacts and returns None."""
    form = OrganizationDirectoryForm(data=organization_plan.source.form_data)
    problems = form_problems(form, ORGANIZATION_FIELD_COLUMNS, [])
    organization = None if problems else _save_form(form, user, problems)
    if problems:
        _block(organization_plan, problems)
        for contact_plan in _contacts_to_create(organization_plan):
            reason = Problem(ORGANIZATION_COLUMN, contact_plan.source.organization_name, BLOCKED_ORGANIZATION_REASON)
            _block(contact_plan, [reason])
        return None
    organization_plan.organization = organization
    organization_plan.status = Status.CREATED
    return organization


def _create_contacts(organization_plan: OrganizationPlan, organization: Organization, user: User) -> None:
    for contact_plan in _contacts_to_create(organization_plan):
        _create_contact(contact_plan, organization, user)


def _create_contact(contact_plan: ContactPlan, organization: Organization, user: User) -> None:
    """Re-checks and saves one contact in its own savepoint, or blocks it."""
    row = contact_plan.source
    problems = _conflict_problems(contact_plan, organization)
    form = ContactForm(data={**row.form_data, "organization": organization.pk})
    problems.extend(form_problems(form, CONTACT_FIELD_COLUMNS, problems))
    contact = None if problems else _save_form(form, user, problems)
    if problems:
        _block(contact_plan, problems)
        return
    contact_plan.contact = contact
    contact_plan.status = Status.CREATED


def _conflict_problems(contact_plan: ContactPlan, organization: Organization) -> list[Problem]:
    """Finds records added since planning that the contact would clash with or change."""
    row = contact_plan.source
    problems = []
    if Contact.objects.filter(email__iexact=row.email).exists():
        problems.append(Problem(EMAIL_COLUMN, row.email, EMAIL_TAKEN_REASON))
    if row.is_main_poc and Contact.objects.filter(organization=organization, is_main_poc=True).exists():
        reason = f"{organization.name} already has a main contact in Connect"
        problems.append(Problem(MAIN_POC_COLUMN, "Yes", reason))
    return problems


def _save_form(form, user: User, problems: list[Problem]):
    """Saves the form's instance attributed to the user, adding a problem if the database rejects it."""
    form.instance.created_by = user.email
    form.instance.modified_by = user.email
    try:
        with transaction.atomic():
            return form.save()
    except IntegrityError as error:
        problems.append(Problem(None, None, f"the database rejected it: {error}"))
        return None


def _contacts_to_create(organization_plan: OrganizationPlan) -> list[ContactPlan]:
    return [contact for contact in organization_plan.contacts if contact.status == Status.TO_CREATE]


def _block(plan: OrganizationPlan | ContactPlan, problems: list[Problem]) -> None:
    plan.status = Status.BLOCKED
    plan.problems.extend(problems)
