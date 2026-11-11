import pytest

from commcare_connect.organization.llo_import.execution import execute_import_plan
from commcare_connect.organization.llo_import.mapping import EMAIL_COLUMN, MAIN_POC_COLUMN
from commcare_connect.organization.llo_import.planning import BLOCKED_ORGANIZATION_REASON, Status
from commcare_connect.organization.models import Contact, ContactEvent, Organization, OrganizationEvent
from commcare_connect.organization.tests.llo_import_helpers import contact_values, organization_values, plan_for
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory


def test_creates_organization_and_contacts(tmp_path, user, countries, sectors):
    plan = plan_for(
        tmp_path,
        [contact_values(**{"Role / Title": "Director", "Main POC?": "Yes"})],
        [
            organization_values(
                **{"Countries of Operation": f"Kenya, {countries['TZA'].name}", "Primary Sector(s)": "Health, WASH"}
            )
        ],
    )

    execute_import_plan(plan, user)

    organization = Organization.objects.get(name="Acme")
    assert organization.short_name == "AC"
    assert organization.regions == "Nairobi"
    assert set(organization.countries.values_list("code", flat=True)) == {"KEN", "TZA"}
    assert set(organization.primary_sectors.all()) == {sectors["health"], sectors["wash"]}
    assert (organization.created_by, organization.modified_by) == (user.email, user.email)
    contact = Contact.objects.get(email="ada@x.org")
    assert (contact.organization, contact.name, contact.title) == (organization, "Ada Obi", "Director")
    assert contact.is_main_poc
    assert (contact.created_by, contact.modified_by) == (user.email, user.email)
    assert (plan.organizations[0].status, plan.organizations[0].organization) == (Status.CREATED, organization)
    assert (plan.contacts[0].status, plan.contacts[0].contact) == (Status.CREATED, contact)


def test_adds_contact_to_existing_organization(tmp_path, user):
    organization = OrganizationFactory(name="Acme")
    plan = plan_for(tmp_path, [contact_values()])

    execute_import_plan(plan, user)

    contact = Contact.objects.get(email="ada@x.org")
    assert contact.organization == organization
    assert plan.organizations[0].status == Status.ALREADY_EXISTED
    assert (plan.contacts[0].status, plan.contacts[0].contact) == (Status.CREATED, contact)


def test_records_who_imported_in_history(tmp_path, user, countries, sectors):
    plan = plan_for(tmp_path, [contact_values()], [organization_values()])

    execute_import_plan(plan, user)

    for event in [OrganizationEvent.objects.get(name="Acme"), ContactEvent.objects.get(email="ada@x.org")]:
        assert event.pgh_context.metadata["user_email"] == user.email
        assert event.pgh_context.metadata["username"] == user.username


def test_contact_conflict_at_save_time_is_blocked(tmp_path, user):
    OrganizationFactory(name="Acme")
    plan = plan_for(tmp_path, [contact_values(), contact_values(**{"Email Address": "bo@x.org"})])
    ContactFactory(email="ADA@x.org")

    execute_import_plan(plan, user)

    taken, saved = plan.contacts
    assert taken.status == Status.BLOCKED
    assert [problem.column for problem in taken.problems] == [EMAIL_COLUMN]
    assert saved.status == Status.CREATED
    assert Contact.objects.filter(email="bo@x.org").exists()


@pytest.mark.parametrize(
    "add_after_planning, organization_status, contact_reason",
    [
        (
            lambda: ContactFactory(email="ada@x.org"),
            Status.NOT_NEEDED,
            "This email already belongs to",
        ),
        (lambda: OrganizationFactory(name="ACME"), Status.BLOCKED, BLOCKED_ORGANIZATION_REASON),
    ],
)
def test_organization_rolled_back_when_no_contact_saves(
    tmp_path, user, countries, sectors, add_after_planning, organization_status, contact_reason
):
    plan = plan_for(tmp_path, [contact_values()], [organization_values()])
    add_after_planning()

    execute_import_plan(plan, user)

    assert not Organization.objects.filter(name="Acme").exists()
    assert not Contact.objects.filter(created_by=user.email).exists()
    organization_plan = plan.organizations[0]
    assert (organization_plan.status, organization_plan.organization) == (organization_status, None)
    assert plan.contacts[0].status == Status.BLOCKED
    [problem] = plan.contacts[0].problems
    assert problem.reason.startswith(contact_reason)


def test_never_changes_existing_records(tmp_path, user, countries, sectors):
    organization = OrganizationFactory(name="Acme", short_name="Old")
    plan = plan_for(
        tmp_path,
        [contact_values(**{"Main POC?": "Yes"})],
        [organization_values(**{"Short Name": "New"})],
    )
    main_contact = ContactFactory(organization=organization, is_main_poc=True)
    organization_modified = Organization.objects.get(pk=organization.pk).date_modified
    contact_modified = Contact.objects.get(pk=main_contact.pk).date_modified

    execute_import_plan(plan, user)

    organization.refresh_from_db()
    main_contact.refresh_from_db()
    assert (organization.short_name, organization.date_modified) == ("Old", organization_modified)
    assert main_contact.is_main_poc
    assert main_contact.date_modified == contact_modified
    contact_plan = plan.contacts[0]
    assert contact_plan.status == Status.BLOCKED
    assert [problem.column for problem in contact_plan.problems] == [MAIN_POC_COLUMN]
    assert not Contact.objects.filter(email="ada@x.org").exists()
