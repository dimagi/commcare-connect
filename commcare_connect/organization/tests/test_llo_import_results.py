import csv
from datetime import datetime
from pathlib import Path

from commcare_connect.organization.llo_import.execution import execute_import_plan
from commcare_connect.organization.llo_import.results import RESULT_COLUMNS, results_path_for, write_results
from commcare_connect.organization.tests.llo_import_helpers import contact_values, organization_values, plan_for
from commcare_connect.users.tests.factories import ContactFactory, OrganizationFactory


def test_results_path_for():
    path = results_path_for(Path("/data/Connect LLO Directory.xlsx"), datetime(2026, 11, 11, 14, 30))

    assert path == Path("/data/Connect LLO Directory - imported 2026-11-11 1430.csv")


def test_lists_imported_rows_with_their_ids(tmp_path, user, countries, sectors):
    existing = ContactFactory(email="old@x.org", organization=OrganizationFactory(name="Old Org"))
    linked = OrganizationFactory(name="Linked Org")
    plan = plan_for(
        tmp_path,
        [
            contact_values(),
            _contact("Old Timer", "Old@x.org", "Acme"),
            _contact("Ada Obi", "bad", "Old Org"),
            _contact("Lin", "lin@x.org", "Old Org"),
            _contact("Kim", "kim@x.org", "Linked Org"),
        ],
        [organization_values(), organization_values(**{"Organization Name": "Linked Org"})],
    )
    execute_import_plan(plan, user)
    output_path = tmp_path / "results.csv"

    write_results(plan, output_path)

    created = {contact_plan.source.email: contact_plan.contact for contact_plan in plan.contacts}
    old_org = existing.organization
    assert _read(output_path) == [
        list(RESULT_COLUMNS),
        ["Contacts", "2", "Ada Obi", "ada@x.org", str(created["ada@x.org"].contact_id), "Created"],
        ["Contacts", "3", "Old Timer", "Old@x.org", str(existing.contact_id), "Already existed"],
        ["Contacts", "5", "Lin", "lin@x.org", str(created["lin@x.org"].contact_id), "Created"],
        ["Contacts", "6", "Kim", "kim@x.org", str(created["kim@x.org"].contact_id), "Created"],
        ["Organizations", "2", "Acme", "", plan.organizations[0].organization.slug, "Created"],
        ["Organizations", "", "Old Org", "", old_org.slug, "Already existed"],
        ["Organizations", "3", "Linked Org", "", linked.slug, "Already existed"],
    ]


def test_writes_only_the_header_when_nothing_was_imported(tmp_path, user):
    plan = plan_for(tmp_path, [contact_values(**{"Email Address": "bad"})])
    execute_import_plan(plan, user)
    output_path = tmp_path / "results.csv"

    write_results(plan, output_path)

    assert _read(output_path) == [list(RESULT_COLUMNS)]


def _contact(name, email, organization):
    return contact_values(**{"Contact Full Name": name, "Email Address": email, "Organization Name": organization})


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as results_file:
        return list(csv.reader(results_file))
