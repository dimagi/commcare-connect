import uuid

import pytest

from commcare_connect.organization.llo_import.mapping import (
    EMAIL_COLUMN,
    FORMULA_REASON,
    MAIN_POC_COLUMN,
    NAME_COLUMN,
    Problem,
)
from commcare_connect.organization.llo_import.planning import Status
from commcare_connect.organization.llo_import.workbook import CONNECT_ID_COLUMN
from commcare_connect.organization.tests.llo_import_helpers import contact_values, organization_values, plan_for
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory


def test_links_contact_to_existing_organization(tmp_path, db):
    organization = OrganizationFactory(name="Acme")

    plan = plan_for(tmp_path, [contact_values()])

    contact_plan = plan.contacts[0]
    assert contact_plan.status == Status.TO_CREATE
    assert contact_plan.problems == []
    assert contact_plan.organization_plan.organization == organization
    assert contact_plan.organization_plan.status == Status.ALREADY_EXISTED
    assert plan.organizations == [contact_plan.organization_plan]


def test_creates_missing_organization_once_for_two_contacts(tmp_path, countries, sectors):
    plan = plan_for(
        tmp_path,
        [contact_values(), contact_values(**{"Email Address": "bo@x.org"})],
        [organization_values()],
    )

    [organization_plan] = plan.organizations
    assert organization_plan.status == Status.TO_CREATE
    assert organization_plan.problems == []
    assert organization_plan.source.row.number == 2
    assert [contact.source.row.number for contact in organization_plan.contacts] == [2, 3]
    assert [contact.status for contact in plan.contacts] == [Status.TO_CREATE, Status.TO_CREATE]


def test_organization_name_match_is_case_sensitive(tmp_path, countries, sectors):
    OrganizationFactory(name="ACME")

    plan = plan_for(
        tmp_path,
        [contact_values(**{"Organization Name": "acme"})],
        [organization_values(**{"Organization Name": "acme"})],
    )

    organization_plan = plan.contacts[0].organization_plan
    assert organization_plan.status == Status.BLOCKED
    assert len([problem for problem in organization_plan.problems if "ACME" in problem.reason]) == 1
    assert plan.contacts[0].status == Status.BLOCKED


def test_name_differing_only_in_case_from_organizations_row_is_blocked(tmp_path, countries, sectors):
    plan = plan_for(
        tmp_path,
        [contact_values(**{"Organization Name": "Hope and Awareness"})],
        [organization_values(**{"Organization Name": "Hope And Awareness"})],
    )

    [contact_plan] = plan.contacts
    assert contact_plan.status == Status.BLOCKED
    assert contact_plan.organization_plan is None
    assert [problem.reason for problem in contact_plan.problems] == [
        "not found in Connect or on the Organizations tab",
        "differs only in case from Organizations row 2",
    ]


def test_ambiguous_organization_is_blocked(tmp_path, db):
    first = OrganizationFactory(name="Acme")
    second = OrganizationFactory(name="Acme")

    [contact_plan] = plan_for(tmp_path, [contact_values()]).contacts

    assert contact_plan.status == Status.BLOCKED
    assert contact_plan.organization_plan is None
    assert any(first.slug in problem.reason and second.slug in problem.reason for problem in contact_plan.problems)


@pytest.mark.parametrize("sheet_email, stored_email", [("A@X.org ", "a@x.org"), ("a@x.org", "A@X.ORG")])
def test_existing_email_is_already_existed(tmp_path, db, sheet_email, stored_email):
    existing = ContactFactory(email=stored_email)

    plan = plan_for(
        tmp_path,
        [contact_values(**{"Email Address": sheet_email}), contact_values(**{"Email Address": stored_email})],
    )

    assert [contact.status for contact in plan.contacts] == [Status.ALREADY_EXISTED, Status.ALREADY_EXISTED]
    assert [contact.contact for contact in plan.contacts] == [existing, existing]


def test_duplicate_emails_block_every_row(tmp_path, db):
    OrganizationFactory(name="Acme")
    emails = ["ada@x.org", "Ada@x.org", " ADA@X.ORG"]

    plan = plan_for(tmp_path, [contact_values(**{"Email Address": email}) for email in emails])

    assert [contact.status for contact in plan.contacts] == [Status.BLOCKED] * 3
    for contact in plan.contacts:
        assert [problem.column for problem in contact.problems] == [EMAIL_COLUMN]


@pytest.mark.parametrize("main_in_connect, main_rows_in_sheet", [(True, 1), (False, 2)])
def test_main_poc_conflicts(tmp_path, db, main_in_connect, main_rows_in_sheet):
    organization = OrganizationFactory(name="Acme")
    if main_in_connect:
        ContactFactory(organization=organization, is_main_poc=True)
    rows = [
        contact_values(**{"Email Address": f"person{index}@x.org", "Main POC?": "Yes"})
        for index in range(main_rows_in_sheet)
    ]

    plan = plan_for(tmp_path, rows)

    assert [contact.status for contact in plan.contacts] == [Status.BLOCKED] * main_rows_in_sheet
    for contact in plan.contacts:
        assert [problem.column for problem in contact.problems] == [MAIN_POC_COLUMN]


def test_blocked_organization_blocks_its_contacts(tmp_path, countries, sectors):
    plan = plan_for(tmp_path, [contact_values()], [organization_values(**{"Short Name": None})])

    organization_plan = plan.organizations[0]
    assert organization_plan.status == Status.BLOCKED
    assert [problem.column for problem in organization_plan.problems] == ["Short Name"]
    contact_plan = plan.contacts[0]
    assert contact_plan.status == Status.BLOCKED
    assert [(problem.value, problem.reason) for problem in contact_plan.problems] == [
        ("Acme", "organization can't be created")
    ]


def test_organization_not_needed_when_all_contacts_blocked(tmp_path, countries, sectors):
    plan = plan_for(tmp_path, [contact_values(**{"Main POC?": "maybe"})], [organization_values()])

    assert plan.contacts[0].status == Status.BLOCKED
    assert plan.organizations[0].status == Status.NOT_NEEDED


@pytest.mark.parametrize("id_kind", ["known", "unknown", "not a uuid"])
def test_contact_rows_with_connect_id_are_not_attempted(tmp_path, db, id_kind):
    existing = ContactFactory(email="someone@else.org")
    connect_id = {"known": str(existing.contact_id), "unknown": str(uuid.uuid4()), "not a uuid": "abc-123"}[id_kind]

    # The row has no organization, so it would be blocked if it were attempted.
    plan = plan_for(tmp_path, [contact_values(**{"Organization Name": None, CONNECT_ID_COLUMN: connect_id})])

    contact_plan = plan.contacts[0]
    if id_kind == "known":
        assert (contact_plan.status, contact_plan.contact, contact_plan.problems) == (
            Status.ALREADY_IMPORTED,
            existing,
            [],
        )
    else:
        assert contact_plan.status == Status.BLOCKED
        assert [problem.column for problem in contact_plan.problems] == [CONNECT_ID_COLUMN]


@pytest.mark.parametrize("known", [True, False])
def test_organization_rows_with_connect_id_are_not_attempted(tmp_path, db, known):
    existing = OrganizationFactory(name="Acme Renamed")
    slug = existing.slug if known else "no-such-organization"

    # The tab row lacks required fields, so it would be blocked if it were validated.
    plan = plan_for(tmp_path, [contact_values()], [{"Organization Name": "Acme", CONNECT_ID_COLUMN: slug}])

    organization_plan = plan.contacts[0].organization_plan
    assert organization_plan.source.row.number == 2
    if known:
        assert (organization_plan.status, organization_plan.organization) == (Status.ALREADY_EXISTED, existing)
        assert plan.contacts[0].status == Status.TO_CREATE
    else:
        assert organization_plan.status == Status.BLOCKED
        assert [problem.column for problem in organization_plan.problems] == [CONNECT_ID_COLUMN]
        assert plan.contacts[0].status == Status.BLOCKED


def test_collects_all_problems(tmp_path, db):
    OrganizationFactory(name="Acme")

    plan = plan_for(tmp_path, [contact_values(**{"Contact Full Name": None, "Main POC?": "maybe"})])

    assert [problem.column for problem in plan.contacts[0].problems] == [NAME_COLUMN, MAIN_POC_COLUMN]


def test_duplicated_invalid_email_reports_both_problems(tmp_path, db):
    OrganizationFactory(name="Acme")

    plan = plan_for(tmp_path, [contact_values(**{"Email Address": "not-an-email"})] * 2)

    for contact in plan.contacts:
        assert contact.problems == [
            Problem(EMAIL_COLUMN, "not-an-email", "same email on Contacts rows 2, 3"),
            Problem(EMAIL_COLUMN, "not-an-email", "Enter a valid email address."),
        ]


def test_formula_cell_reports_only_the_formula(tmp_path, db):
    OrganizationFactory(name="Acme")

    plan = plan_for(tmp_path, [contact_values(**{"Email Address": "=A1"})])

    assert plan.contacts[0].problems == [Problem(EMAIL_COLUMN, "=A1", FORMULA_REASON)]
