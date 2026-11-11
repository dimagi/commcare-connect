from pathlib import Path

from commcare_connect.organization.llo_import.planning import ImportPlan
from commcare_connect.organization.llo_import.report import render_report
from commcare_connect.organization.llo_import.workbook import CONNECT_ID_COLUMN
from commcare_connect.organization.tests.llo_import_helpers import contact_values, organization_values, plan_for
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory


def test_dry_run_header_says_nothing_was_written():
    plan = ImportPlan(Path("/data/Connect LLO Directory.xlsx"), [], [])

    report = render_report(plan, committed=False, output_path=None)

    assert report.startswith("DRY RUN — nothing was written")
    assert "Connect LLO Directory.xlsx" in report.splitlines()[0]


def test_lists_each_section(tmp_path, countries, sectors):
    acme = OrganizationFactory(name="Acme")
    ContactFactory(organization=acme, email="old@x.org")
    imported = ContactFactory(email="imported@x.org")
    plan = plan_for(
        tmp_path,
        [
            contact_values(**{"Organization Name": "New Org"}),
            contact_values(**{"Email Address": "bo@x.org", "Organization Name": "Bad Org", "Main POC?": "maybe"}),
            contact_values(**{"Contact Full Name": "Old Timer", "Email Address": "old@x.org"}),
            contact_values(**{"Email Address": "imported@x.org", CONNECT_ID_COLUMN: str(imported.contact_id)}),
            contact_values(**{"Email Address": "cy@x.org", "Organization Name": "Spare Org", "Main POC?": "?"}),
        ],
        [
            organization_values(**{"Organization Name": "New Org"}),
            organization_values(**{"Organization Name": "Bad Org", "Short Name": None}),
            organization_values(**{"Organization Name": "Spare Org"}),
        ],
    )

    report = render_report(plan, committed=False, output_path=None)

    for expected in [
        "Contacts to create: 1",
        "Organizations to create: 1",
        "Contacts skipped, email already in Connect: 1",
        "Rows already imported (have a Connect ID): 1",
        "Contacts blocked: 2",
        "Organizations blocked: 1",
        "Organizations row 2: New Org — for Contacts rows 2",
        "Organizations row 3: Bad Org",
        '- Organizations row 3, column "Short Name": This field is required.',
        "Contacts blocked by it: rows 3",
        "Organizations row 4: Spare Org — needed by Contacts rows 6",
        "Contacts row 3: Ada Obi <bo@x.org>, organization Bad Org",
        '- Contacts row 3, column "Main POC?", value "maybe": must be Yes, No or empty',
        '- Contacts row 3, column "Organization Name", value "Bad Org": organization can\'t be created',
        "Contacts row 4: Old Timer <old@x.org>, already belongs to Acme",
    ]:
        assert expected in report
    headings = ["Totals", "Organizations to create", "Blocked organizations", "Blocked contacts", "Skipped"]
    positions = [report.index(f"\n{heading}") for heading in headings]
    assert positions == sorted(positions)


def test_real_run_names_output_file():
    output_path = Path("/data/Connect LLO Directory - imported 2026-11-11 1430.xlsx")

    report = render_report(ImportPlan(Path("/data/x.xlsx"), [], []), committed=True, output_path=output_path)

    assert report.startswith("REAL RUN")
    assert str(output_path) in report


def test_lists_existing_organizations_with_their_new_contacts(tmp_path):
    acme = OrganizationFactory(name="Acme")
    beta = OrganizationFactory(name="Beta")
    plan = plan_for(
        tmp_path,
        [
            contact_values(),
            contact_values(**{"Email Address": "bo@x.org"}),
            contact_values(**{"Email Address": "cy@x.org", "Organization Name": "Beta", "Main POC?": "maybe"}),
        ],
    )

    report = render_report(plan, committed=False, output_path=None)

    assert "Existing organizations (marked Already existed)" in report
    assert f"Acme ({acme.slug}) — contacts to create: Contacts rows 2, 3" in report
    assert f"Beta ({beta.slug}) — no contacts to create" in report
