import pytest

from commcare_connect.organization.llo_import.mapping import (
    FORMULA_REASON,
    Problem,
    cell_text,
    eoi_links,
    map_contact_row,
    map_organization_row,
    split_countries,
    split_sectors,
)
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


ORG_VALUES = {
    "Organization Name": " Acme Health ",
    "Short Name": " Acme ",
    "Countries of Operation": "Nigeria, Chad",
    "Regions/States of Operation": " Lagos ",
    "Primary Sector(s)": "Health, WASH (Water, Sanitation, Hygiene)",
    "Has Used Connect": "X",
    "Year of Establishment": 1999.0,
    "Org Team Size": "51-200",
    "No. of FLWs Managed": 50,
    "Website": " https://acme.example ",
    "Office Address": " 1 Main St ",
    "Email addresses from Contacts sheet": "a@x.org, b@x.org;c@x.org",
    "EOIs Applied for (Add Link to EOI)": "Title — https://a.example/x",
    "Organization Notes": " Notes ",
    "Latest MSA Link": "https://msa.example",
    "Latest Work Order Link": "https://wo.example",
}
COUNTRIES = {"Nigeria": "NGA", "Chad": "TCD"}
SECTORS = {"Health": 1, "WASH (Water, Sanitation, Hygiene)": 2}


def make_org_row(hyperlinks=None, **overrides):
    values = {**ORG_VALUES, **overrides}
    return SheetRow(number=3, values=values, hyperlinks=hyperlinks or {})


def map_org(hyperlinks=None, **overrides):
    return map_organization_row(make_org_row(hyperlinks, **overrides), COUNTRIES, SECTORS)


def test_maps_organization_row():
    organization = map_org()

    assert organization.problems == []
    assert organization.name == "Acme Health"
    assert organization.connect_id == ""
    assert organization.form_data == {
        "name": "Acme Health",
        "short_name": "Acme",
        "year_of_establishment": 1999,
        "has_used_connect": "True",
        "team_size": "51-200",
        "flws_managed": 50,
        "countries": ["NGA", "TCD"],
        "regions": "Lagos",
        "primary_sectors": [1, 2],
        "website": "https://acme.example",
        "office_address": "1 Main St",
        "contact_emails": "a@x.org\nb@x.org\nc@x.org",
        "eoi_links": "https://a.example/x",
        "latest_msa_link": "https://msa.example",
        "latest_work_order_link": "https://wo.example",
        "notes": "Notes",
    }


@pytest.mark.parametrize(
    "value, expected",
    [
        (
            'Nigeria, Chad, "Congo, the Democratic Republic of the"',
            ["Nigeria", "Chad", "Congo, the Democratic Republic of the"],
        ),
        ("Nigeria ", ["Nigeria"]),
        ("Nigeria,\nChad", ["Nigeria", "Chad"]),
        ("Nigeria\r\nChad, Mali", ["Nigeria", "Chad", "Mali"]),
    ],
)
def test_split_countries(value, expected):
    assert split_countries(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "Health, WASH (Water, Sanitation, Hygiene), Education",
        "Health\nWASH (Water, Sanitation, Hygiene)\r\nEducation",
    ],
)
def test_split_sectors(value):
    assert split_sectors(value) == ["Health", "WASH (Water, Sanitation, Hygiene)", "Education"]


def test_unreadable_countries_are_a_problem(monkeypatch):
    import csv

    def raise_error(*args, **kwargs):
        raise csv.Error("bad")

    monkeypatch.setattr(csv, "reader", raise_error)

    organization = map_org()

    assert Problem("Countries of Operation", "Nigeria, Chad", "the names couldn't be read") in organization.problems


def test_unknown_country_and_sector_are_problems():
    organization = map_org(**{"Countries of Operation": "Nigeria, DRC", "Primary Sector(s)": "health"})

    country_problem, sector_problem = organization.problems
    assert country_problem.column == "Countries of Operation"
    assert country_problem.value == "DRC"
    assert "Connect's country list" in country_problem.reason
    assert sector_problem.column == "Primary Sector(s)"
    assert sector_problem.value == "health"
    assert "primary sector in Connect" in sector_problem.reason
    assert organization.form_data["countries"] == ["NGA"]


def test_duplicate_countries_are_kept_once():
    assert map_org(**{"Countries of Operation": "Chad, Nigeria, Chad"}).form_data["countries"] == ["TCD", "NGA"]


@pytest.mark.parametrize(
    "text, hyperlink, expected",
    [
        ("Title — https://a.example/x", None, (["https://a.example/x"], [])),
        ('=HYPERLINK("https://a.example/y","Title")', None, (["https://a.example/y"], [])),
        ("Title", "https://a.example/z", (["https://a.example/z"], [])),
        ("One\nTwo", "https://a.example/z", (["https://a.example/z"], ["Two"])),
        (
            "None yet, but has deployed Connect",
            None,
            ([], ["None yet, but has deployed Connect"]),
        ),
        ("A https://a.example/x\nB https://a.example/x", None, (["https://a.example/x"], [])),
        ("See (https://x.org),", None, (["https://x.org"], [])),
        ("HTTPS://X.ORG/a", None, (["HTTPS://X.ORG/a"], [])),
    ],
)
def test_eoi_links(text, hyperlink, expected):
    assert eoi_links(text, hyperlink) == expected


def test_eoi_line_without_link_blocks():
    organization = map_org(**{"EOIs Applied for (Add Link to EOI)": "https://a.example/x\nNone yet"})

    assert organization.problems == [
        Problem("EOIs Applied for (Add Link to EOI)", "None yet", "not a link; add the EOI's URL or remove the line")
    ]


def test_eoi_cell_hyperlink_is_used():
    organization = map_org(
        hyperlinks={"EOIs Applied for (Add Link to EOI)": "https://a.example/z"},
        **{"EOIs Applied for (Add Link to EOI)": "Title"},
    )

    assert organization.problems == []
    assert organization.form_data["eoi_links"] == "https://a.example/z"


def test_eoi_hyperlink_formula_is_not_a_formula_problem():
    organization = map_org(**{"EOIs Applied for (Add Link to EOI)": '=HYPERLINK("https://a.example/y","T")'})

    assert organization.problems == []


@pytest.mark.parametrize("value, expected", [("X", "True"), ("x", "True"), (None, "False")])
def test_has_used_connect(value, expected):
    organization = map_org(**{"Has Used Connect": value})

    assert organization.problems == []
    assert organization.form_data["has_used_connect"] == expected


def test_has_used_connect_other_text_is_a_problem():
    organization = map_org(**{"Has Used Connect": "Yes"})

    assert [problem.column for problem in organization.problems] == ["Has Used Connect"]


def test_team_size():
    assert map_org(**{"Org Team Size": "51-200"}).problems == []

    organization = map_org(**{"Org Team Size": "Medium"})

    assert organization.problems == [
        Problem("Org Team Size", "Medium", "must be one of 1-10, 11-50, 51-200, 201-500, 500+")
    ]


@pytest.mark.parametrize("value", [50.0, "50", "50.0"])
def test_whole_numbers_are_read(value):
    assert map_org(**{"No. of FLWs Managed": value}).form_data["flws_managed"] == 50


@pytest.mark.parametrize("value", ["1_000", "1e3", "+5", "50.5"])
def test_malformed_whole_numbers_are_problems(value):
    organization = map_org(**{"No. of FLWs Managed": value})

    assert organization.problems == [Problem("No. of FLWs Managed", value, "must be a whole number")]


def test_whole_numbers():

    organization = map_org(**{"No. of FLWs Managed": 50.5, "Year of Establishment": "about 1990"})

    assert [problem.column for problem in organization.problems] == ["Year of Establishment", "No. of FLWs Managed"]


def test_contact_emails_one_per_line():
    organization = map_org(**{"Email addresses from Contacts sheet": "a@x.org, b@x.org;c@x.org"})

    assert organization.form_data["contact_emails"] == "a@x.org\nb@x.org\nc@x.org"


def test_formula_in_organization_column_is_a_problem():
    organization = map_org(**{"Regions/States of Operation": "=A1"})

    assert organization.problems == [Problem("Regions/States of Operation", "=A1", FORMULA_REASON)]


def test_empty_optional_cells_are_fine():
    organization = map_org(
        **{"Website": None, "Year of Establishment": None, "EOIs Applied for (Add Link to EOI)": None}
    )

    assert organization.problems == []
    assert organization.form_data["website"] == ""
    assert organization.form_data["year_of_establishment"] == ""
    assert organization.form_data["eoi_links"] == ""
