import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth.context_processors import PermWrapper
from django.core.cache import cache
from django.db import connection
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import now

from commcare_connect.opportunity.tests.factories import OpportunityFactory, UserVisitFactory
from commcare_connect.organization.context_selector import (
    clear_visit_stats_cache,
    end_label,
    opportunity_end_label,
    selector_tree,
)
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.program.models import ProgramWatcher
from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.program.utils import AccessLevel
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory, UserFactory


@pytest.fixture(autouse=True)
def empty_cache():
    """Visit stats are cached, and a test's entries outlive its rolled-back transaction."""
    cache.clear()


@pytest.fixture
def pm_org(db):
    return OrganizationFactory(name="A PM org", program_manager=True)


@pytest.fixture
def nm_org(db):
    return OrganizationFactory(name="B NM org")


@pytest.fixture
def program(pm_org):
    return ProgramFactory(organization=pm_org, name="Malaria")


def _by_name(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {node["name"]: node for node in nodes}


def _tree_for(user: User) -> dict[str, dict[str, Any]]:
    return _by_name(selector_tree(user))


def _member(org: Organization, role: str = "admin") -> User:
    user = UserFactory()
    MembershipFactory(user=user, organization=org, role=role)
    return user


def test_user_without_memberships_gets_empty_tree(user):
    """A user who belongs to no organization sees nothing in the selector."""
    assert selector_tree(user) == []


def test_pm_and_nm_orgs_see_their_own_opportunities_in_a_shared_program(pm_org, nm_org, program):
    """The PM org sees every opportunity in its program; the NM org sees only the ones it delivers."""
    user = _member(pm_org)
    MembershipFactory(user=user, organization=nm_org)
    nm_opp = OpportunityFactory(organization=nm_org, program=program, name="NM delivery")
    other_nm_opp = OpportunityFactory(program=program, name="Someone else's delivery")

    tree = _tree_for(user)

    pm_program = _by_name(tree["A PM org"]["programs"])["Malaria"]
    assert {opp["name"] for opp in pm_program["opportunities"]} == {nm_opp.name, other_nm_opp.name}
    nm_program = _by_name(tree["B NM org"]["programs"])["Malaria"]
    assert [opp["name"] for opp in nm_program["opportunities"]] == ["NM delivery"]
    assert nm_program["opportunities"][0]["url"] == f"/a/{nm_org.slug}/opportunity/{nm_opp.opportunity_id}/"


def test_program_without_opportunities_is_listed_for_its_owner(pm_org, program):
    """A PM org's program shows up even before it has any opportunities."""
    tree = _tree_for(_member(pm_org))

    assert _by_name(tree["A PM org"]["programs"])["Malaria"]["opportunities"] == []


def test_watched_program_is_listed(pm_org, nm_org, program):
    """An org that watches a program sees the program and its opportunities."""
    ProgramWatcher.objects.create(program=program, organization=nm_org)
    OpportunityFactory(program=program, name="Watched opp")

    tree = _tree_for(_member(nm_org))

    watched = _by_name(tree["B NM org"]["programs"])["Malaria"]
    assert [opp["name"] for opp in watched["opportunities"]] == ["Watched opp"]


def test_programs_of_delivered_opportunities_are_listed(nm_org, program):
    """An NM org sees the program of each opportunity it delivers, though it doesn't own the program."""
    OpportunityFactory(organization=nm_org, program=program, name="Delivery")

    tree = _tree_for(_member(nm_org))

    assert [p["name"] for p in tree["B NM org"]["programs"]] == ["Malaria"]


def test_archived_opportunities_are_left_out(nm_org, program):
    """Archived opportunities, and programs left empty by them, are not listed."""
    OpportunityFactory(organization=nm_org, program=program, archived=True)

    assert _tree_for(_member(nm_org))["B NM org"]["programs"] == []


@pytest.mark.parametrize(
    "opp_kwargs, active",
    [
        ({}, True),
        ({"active": False}, False),
        ({"end_date": datetime.date.today() - datetime.timedelta(days=1)}, False),
        ({"end_date": None}, False),
        ({"is_test": True}, False),
    ],
)
def test_opportunity_active_flag(nm_org, program, opp_kwargs, active):
    """An opportunity is active only when it is running, has not ended and is not a test opportunity."""
    OpportunityFactory(organization=nm_org, program=program, **{"is_test": False, **opp_kwargs})

    (program_node,) = _tree_for(_member(nm_org))["B NM org"]["programs"]

    assert program_node["opportunities"][0]["active"] is active


@pytest.mark.parametrize("days_from_today, active", [(0, True), (-1, False)])
def test_program_active_flag(pm_org, days_from_today, active):
    """A program with no opportunities is active until its end date passes."""
    ProgramFactory(organization=pm_org, name="P", end_date=datetime.date.today() + datetime.timedelta(days_from_today))

    program_node = _by_name(_tree_for(_member(pm_org))["A PM org"]["programs"])["P"]

    assert program_node["active"] is active


@pytest.mark.parametrize(
    "days_from_today, expected_prefix",
    [(-1, "Ended "), (0, "Ends "), (1, "Ends ")],
)
def test_end_label_says_ends_or_ended(days_from_today, expected_prefix):
    end_date = now().date() + datetime.timedelta(days=days_from_today)
    assert end_label(end_date) == expected_prefix + end_date.strftime("%d %b, %Y")


def test_end_label_without_end_date():
    assert end_label(None) == "No end date"


@pytest.mark.parametrize(
    "opp_kwargs, expected_prefix",
    [
        ({}, "Ends "),
        ({"end_date": datetime.date.today() - datetime.timedelta(days=1)}, "Ended "),
        ({"end_date": None}, "Inactive · No end date"),
        ({"active": False}, "Inactive · Ends "),
        ({"is_test": True}, "Test opportunity · Ends "),
    ],
)
def test_opportunity_end_label_explains_why_it_is_inactive(opp_kwargs, expected_prefix):
    opportunity = OpportunityFactory.build(
        **{
            "active": True,
            "is_test": False,
            "end_date": datetime.date.today() + datetime.timedelta(days=5),
            **opp_kwargs,
        }
    )
    assert opportunity_end_label(opportunity).startswith(expected_prefix)


def test_programs_and_opportunities_are_sorted_by_end_date(pm_org):
    today = datetime.date.today()
    for name, days in [("Ended", -10), ("Later", 30), ("Sooner", 5)]:
        program = ProgramFactory(organization=pm_org, name=name, end_date=today + datetime.timedelta(days))
        OpportunityFactory(organization=pm_org, program=program, name=name, end_date=program.end_date)
    OpportunityFactory(organization=pm_org, program=program, name="Open-ended", end_date=None)

    programs = _tree_for(_member(pm_org))["A PM org"]["programs"]

    assert [p["name"] for p in programs] == ["Later", "Sooner", "Ended"]
    assert [o["name"] for o in programs[1]["opportunities"]] == ["Open-ended", "Sooner"]


@pytest.mark.parametrize(
    "role, landing", [("admin", "organization/"), ("member", "opportunity/"), ("viewer", "opportunity/")]
)
def test_org_landing_page_depends_on_role(nm_org, role, landing):
    """Admins land on Organization Home; members and viewers, who can't open it, land on the opportunity list."""
    org_node = _tree_for(_member(nm_org, role=role))["B NM org"]

    assert org_node["url"] == f"/a/{nm_org.slug}/{landing}"


def test_visit_stats_roll_up_to_program_and_org(pm_org, program):
    """Programs and orgs show the total visits and the latest visit date of their opportunities."""
    first_opp = OpportunityFactory(program=program, name="First")
    second_opp = OpportunityFactory(program=program, name="Second")
    latest = now() - datetime.timedelta(days=1)
    UserVisitFactory.create_batch(2, opportunity=first_opp, visit_date=latest - datetime.timedelta(days=10))
    UserVisitFactory(opportunity=second_opp, visit_date=latest)

    org_node = _tree_for(_member(pm_org))["A PM org"]
    program_node = _by_name(org_node["programs"])["Malaria"]
    opps = _by_name(program_node["opportunities"])

    first_date = (latest - datetime.timedelta(days=10)).strftime("%d %b, %Y")
    assert opps["First"]["visit_summary"] == f"2 visits · Last visit {first_date}"
    assert opps["Second"]["visit_summary"] == f"1 visit · Last visit {latest.strftime('%d %b, %Y')}"
    for node in (program_node, org_node):
        assert node["visit_summary"] == f"3 visits · Last visit {latest.strftime('%d %b, %Y')}"


def test_visit_stats_are_cached(nm_org, program):
    """A second load reuses the cached visit stats, so a visit nobody announced doesn't show yet."""
    opp = OpportunityFactory(organization=nm_org, program=program)
    UserVisitFactory(opportunity=opp)
    user = _member(nm_org)
    selector_tree(user)
    UserVisitFactory(opportunity=opp)

    (program_node,) = _tree_for(user)["B NM org"]["programs"]

    assert program_node["visit_summary"].startswith("1 visit ·")


def test_clearing_the_cache_shows_new_visits(nm_org, program, django_capture_on_commit_callbacks):
    """Once a new visit clears its opportunity's cached stats, the next load counts it."""
    opp = OpportunityFactory(organization=nm_org, program=program)
    UserVisitFactory(opportunity=opp)
    user = _member(nm_org)
    selector_tree(user)
    UserVisitFactory(opportunity=opp)
    with django_capture_on_commit_callbacks(execute=True):
        clear_visit_stats_cache(opp.id)

    (program_node,) = _tree_for(user)["B NM org"]["programs"]

    assert program_node["visit_summary"].startswith("2 visits ·")


def test_query_count_does_not_grow_with_orgs(pm_org, program):
    """Each kind of row is fetched once for all the user's orgs, not once per org."""
    user = _member(pm_org)
    OpportunityFactory(program=program)
    selector_tree(user)  # Fills the user's permission cache, which would otherwise count once.
    cache.clear()  # Count the visit stats query too.
    with CaptureQueriesContext(connection) as one_org:
        selector_tree(user)

    for name in ("C NM org", "D NM org"):
        org = OrganizationFactory(name=name)
        MembershipFactory(user=user, organization=org)
        program.watchers.add(org)
        OpportunityFactory(organization=org, program=program)
    cache.clear()
    with CaptureQueriesContext(connection) as three_orgs:
        selector_tree(user)

    assert len(three_orgs) == len(one_org)


def test_opportunity_without_visits_says_so(nm_org, program):
    """An opportunity with no visits says so instead of showing a count."""
    OpportunityFactory(organization=nm_org, program=program)

    (group,) = _tree_for(_member(nm_org))["B NM org"]["programs"]

    assert group["opportunities"][0]["visit_summary"] == "No visits yet"


def _request(user: User, org: Organization, **attrs: Any) -> SimpleNamespace:
    membership = UserOrganizationMembership.objects.filter(user=user, organization=org).first()
    attrs.setdefault("resolver_match", None)
    return SimpleNamespace(user=user, org=org, org_membership=membership, **attrs)


def _page_context(request: SimpleNamespace) -> str:
    template = Template(
        "{% load organization_extras %}{% page_context as page %}{{ page.program.name }}|{{ page.opportunity.name }}"
    )
    return template.render(Context({"request": request}))


@pytest.mark.parametrize("member_of_owner, expected", [(True, "Malaria|Bednets"), (False, "|")])
def test_page_context_shows_opportunity_only_with_access(nm_org, program, member_of_owner, expected):
    """The header shows the page's opportunity and program only to a user whose org can see them."""
    opp = OpportunityFactory(program=program, name="Bednets")
    org = program.organization if member_of_owner else nm_org
    request = _request(_member(org), org, opportunity=opp)

    assert _page_context(request) == expected


def test_page_context_reuses_the_views_opportunity_access_level(pm_org, program):
    """On a URL with an opp_id, the header trusts the access level the view already worked out."""
    opp = OpportunityFactory(program=program, name="Bednets")
    request = _request(
        _member(pm_org),
        pm_org,
        opportunity=opp,
        resolver_match=SimpleNamespace(kwargs={"opp_id": opp.opportunity_id}),
        opp_access_level=AccessLevel.NONE,
    )

    assert _page_context(request) == "|"


@pytest.mark.parametrize("member_of_owner, expected", [(True, "Malaria|"), (False, "|")])
def test_page_context_shows_program_only_with_access(nm_org, program, member_of_owner, expected):
    """The header shows the page's program only to a user whose org can see it."""
    org = program.organization if member_of_owner else nm_org
    request = _request(_member(org), org, program=program)

    assert _page_context(request) == expected


def test_see_all_filters_by_program_only_where_the_list_allows_it(pm_org, nm_org, program):
    """The "See all" link filters the opportunity list by program only where that list offers the program as a filter.

    An NM org that only delivers for a program can't filter by it, so its link opens the list without a program.
    """
    OpportunityFactory(organization=nm_org, program=program)
    user = _member(pm_org)
    MembershipFactory(user=user, organization=nm_org)

    tree = _tree_for(user)

    (pm_program,) = tree["A PM org"]["programs"]
    assert pm_program["opportunities_url"] == f"/a/{pm_org.slug}/opportunity/?program={program.slug}&filtered=1"
    (nm_program,) = tree["B NM org"]["programs"]
    assert nm_program["opportunities_url"] == f"/a/{nm_org.slug}/opportunity/?filtered=1"


def test_see_all_turns_off_the_lists_default_filters(pm_org):
    """See all turns off the list's default filters, so the list shows test and inactive opportunities too."""
    org_node = _tree_for(_member(pm_org))["A PM org"]

    assert org_node["opportunities_url"] == f"/a/{pm_org.slug}/opportunity/?filtered=1"


@pytest.mark.parametrize("is_test", [True, False])
def test_test_orgs_and_opportunities_are_marked(is_test):
    """The selector marks test organizations and test opportunities."""
    org = OrganizationFactory(name="Org", is_test=is_test)
    OpportunityFactory(organization=org, program=ProgramFactory(organization=org), is_test=is_test)

    org_node = _tree_for(_member(org))["Org"]
    (program_node,) = org_node["programs"]

    assert org_node["test"] is is_test
    assert program_node["opportunities"][0]["test"] is is_test


def test_ended_program_with_a_running_opportunity_is_active(pm_org):
    """A program past its end date stays active while one of its opportunities runs."""
    ended = ProgramFactory(organization=pm_org, end_date=datetime.date.today() - datetime.timedelta(days=1))
    OpportunityFactory(program=ended, is_test=False)

    (program_node,) = _tree_for(_member(pm_org))["A PM org"]["programs"]

    assert program_node["active"] is True


def test_superuser_sees_only_orgs_they_belong_to(pm_org, nm_org):
    """Reaching every org doesn't add orgs the user isn't a member of."""
    user = UserFactory(is_superuser=True)
    MembershipFactory(user=user, organization=pm_org)

    assert [org["name"] for org in selector_tree(user)] == ["A PM org"]


def test_context_search_requires_login(client):
    """The selector's data endpoint redirects anonymous users to log in."""
    response = client.get(reverse("context_search"))

    assert response.status_code == 302


def test_context_search_returns_the_tree(client, nm_org):
    """The endpoint returns the user's orgs."""
    user = _member(nm_org)
    client.force_login(user)

    response = client.get(reverse("context_search"))

    assert [org["name"] for org in response.json()["organizations"]] == ["B NM org"]


def _render_selector(
    user: User, org: Organization | None = None, membership: UserOrganizationMembership | None = None
) -> str:
    request = RequestFactory().get("/")
    request.user = user
    request.org = org
    request.org_membership = membership
    request.memberships = UserOrganizationMembership.objects.filter(user=user)
    return render_to_string("layouts/context_selector.html", {"request": request, "perms": PermWrapper(user)})


def test_header_does_not_name_org_for_superuser_who_is_not_a_member(pm_org, nm_org):
    """A superuser on another org's page gets the selector for their own orgs, with nothing selected."""
    user = UserFactory(is_superuser=True)
    MembershipFactory(user=user, organization=pm_org)

    html = _render_selector(user, org=nm_org)

    assert "B NM org" not in html
    assert 'data-org=""' in html


@pytest.mark.parametrize("can_create", [True, False])
def test_header_without_memberships_offers_only_org_creation(db, can_create):
    """Without memberships there is no selector; only users who may create orgs (here, superusers) get the link."""
    html = _render_selector(UserFactory(is_superuser=can_create))

    assert "x-data" not in html
    assert ("Create Organization" in html) is can_create
