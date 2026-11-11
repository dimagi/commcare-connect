import pytest

from commcare_connect.organization.llo_import.mapping import Problem, cell_text, map_contact_row
from commcare_connect.organization.llo_import.workbook import SheetRow

FULL_VALUES = {
    "Contact Full Name": "  Ada Obi ",
    "Organization Name": " Acme Health ",
    "Email Address": " ada@x.org ",
    "Role / Title": " Director ",
    "Main POC?": "Yes",
    "Phone Number": "+234 801",
    "Contact Notes (e.g. best way to contact)": " Call after 5 ",
}


def make_row(**overrides):
    values = {**FULL_VALUES, **overrides}
    return SheetRow(number=2, values=values, hyperlinks={})


def row_without(column):
    values = {key: value for key, value in FULL_VALUES.items() if key != column}
    return SheetRow(number=2, values=values, hyperlinks={})


def test_maps_all_fields():
    contact = map_contact_row(make_row())

    assert contact.problems == []
    assert contact.name == "Ada Obi"
    assert contact.email == "ada@x.org"
    assert contact.organization_name == "Acme Health"
    assert contact.is_main_poc is True
    assert contact.connect_id == ""
    assert contact.form_data == {
        "name": "Ada Obi",
        "title": "Director",
        "is_main_poc": "True",
        "email": "ada@x.org",
        "phone": "+234 801",
        "notes": "Call after 5",
    }


def test_connect_id_is_read():
    row = SheetRow(number=2, values={**FULL_VALUES, "Connect ID": " abc "}, hyperlinks={})
    assert map_contact_row(row).connect_id == "abc"


@pytest.mark.parametrize(
    "cell, expected",
    [("Yes", True), ("No", False), (None, False), ("", False)],
)
def test_main_poc_values(cell, expected):
    contact = map_contact_row(make_row(**{"Main POC?": cell}))

    assert contact.is_main_poc is expected
    assert contact.form_data["is_main_poc"] == str(expected)
    assert contact.problems == []


def test_unknown_main_poc_value_is_a_problem():
    contact = map_contact_row(make_row(**{"Main POC?": "yes please"}))

    assert [problem.column for problem in contact.problems] == ["Main POC?"]
    assert contact.problems[0].value == "yes please"
    assert contact.is_main_poc is False


@pytest.mark.parametrize("phone", [2348012345678.0, 2348012345678])
def test_phone_from_number(phone):
    contact = map_contact_row(make_row(**{"Phone Number": phone}))

    assert contact.form_data["phone"] == "2348012345678"
    assert contact.problems == []


@pytest.mark.parametrize(
    "value, expected",
    [(None, ""), ("  a b ", "a b"), (12.5, "12.5"), (3.0, "3"), (7, "7")],
)
def test_cell_text(value, expected):
    assert cell_text(value) == expected


@pytest.mark.parametrize("email", ["a@x.org, b@x.org", "a@x.org\nb@x.org"])
def test_email_with_several_addresses_is_a_problem(email):
    contact = map_contact_row(make_row(**{"Email Address": email}))

    assert [problem.column for problem in contact.problems] == ["Email Address"]
    assert contact.problems[0].value == email


@pytest.mark.parametrize("column", ["Contact Full Name", "Email Address", "Organization Name"])
@pytest.mark.parametrize("blank", ["missing", None])
def test_missing_required_values(column, blank):
    row = row_without(column) if blank == "missing" else make_row(**{column: blank})

    contact = map_contact_row(row)

    assert [(problem.column, problem.value) for problem in contact.problems] == [(column, None)]


def test_formula_cell_is_a_problem():
    contact = map_contact_row(make_row(**{"Role / Title": "=A1"}))

    assert len(contact.problems) == 1
    problem = contact.problems[0]
    assert isinstance(problem, Problem)
    assert problem.column == "Role / Title"
    assert problem.value == "=A1"
    assert "formulas aren't read" in problem.reason
    assert contact.form_data["title"] == ""
