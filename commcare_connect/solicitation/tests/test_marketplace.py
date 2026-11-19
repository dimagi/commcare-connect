import datetime
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import localdate, now
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.opportunity.models import Country
from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory
from commcare_connect.solicitation.filters import MarketplaceFilterSet
from commcare_connect.solicitation.models import Solicitation
from commcare_connect.solicitation.tests.factories import SolicitationFactory
from commcare_connect.solicitation.tests.test_solicitation_statuses import DEADLINE_CASES

pytestmark = pytest.mark.django_db

MARKETPLACE_URL = reverse("marketplace:list")


@pytest.fixture
def solicitations_on():
    with override_switch(SOLICITATIONS, active=True):
        yield


def listed(**kwargs):
    return SolicitationFactory(status=Solicitation.Status.ACTIVE, public=True, **kwargs)


def in_days(days, hour=12, minute=0):
    day = localdate() + datetime.timedelta(days=days)
    return datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=datetime.UTC)


def marketplace_filter(data):
    return MarketplaceFilterSet(data=data, queryset=Solicitation.objects.public())


def custom_range(from_days, to_days):
    """The deadline filter's custom range, from and to these many days from today."""
    day = localdate()
    return {
        "deadline_range": "custom",
        "deadline_from": (day + datetime.timedelta(days=from_days)).isoformat(),
        "deadline_to": (day + datetime.timedelta(days=to_days)).isoformat(),
    }


@pytest.mark.parametrize("public", [True, False])
@pytest.mark.parametrize("status, deadline_offset, expected_status", DEADLINE_CASES)
def test_public_means_public_and_open(public, status, deadline_offset, expected_status):
    solicitation = SolicitationFactory(public=public, status=status, application_deadline=now() + deadline_offset)

    expected = public and expected_status == Solicitation.Status.ACTIVE
    assert Solicitation.objects.public().filter(pk=solicitation.pk).exists() is expected


@pytest.mark.usefixtures("solicitations_on")
def test_logged_out_visitor_sees_only_public_solicitations(client):
    listed(title="Open public RFP")
    SolicitationFactory(status=Solicitation.Status.ACTIVE, public=False, title="Private RFP")
    listed(title="Expired RFP", deadline_passed=True)
    SolicitationFactory(public=True, title="Draft RFP")

    response = client.get(MARKETPLACE_URL)

    assert response.status_code == 200
    assert [solicitation.title for solicitation in response.context["solicitations"]] == ["Open public RFP"]


def test_switch_off_is_not_found(client):
    listed()

    with override_switch(SOLICITATIONS, active=False):
        assert client.get(MARKETPLACE_URL).status_code == 404


@pytest.mark.usefixtures("solicitations_on")
def test_logged_out_visitor_gets_no_sidebar(client):
    content = client.get(MARKETPLACE_URL).content.decode()

    assert 'alt="Compact Logo"' not in content
    # Nor the room the body leaves for it.
    assert re.search(r"hasSidebar:\s*false", content)


@pytest.mark.usefixtures("solicitations_on")
def test_queries_do_not_grow_with_the_number_of_cards(client):
    def queries_for_page():
        with CaptureQueriesContext(connection) as queries:
            client.get(MARKETPLACE_URL)
        return len(queries)

    def card():
        # Every related row a card shows is set, so a missing select_related costs one query per card.
        listed(country=Country.objects.get(code="KEN"), delivery_type=DeliveryTypeFactory())

    card()
    queries_for_page()  # the first visit also saves the session and caches the switch
    one_card = queries_for_page()
    for _ in range(4):
        card()

    assert queries_for_page() == one_card


@pytest.fixture
def catalogue():
    """Listed solicitations that differ in every filtered field, keyed by when their deadline is."""
    kenya, uganda, nigeria = (Country.objects.get(code=code) for code in ("KEN", "UGA", "NGA"))
    rfp, eoi = Solicitation.Type.RFP, Solicitation.Type.EOI
    fields = {"soon": (rfp, kenya, 10), "later": (eoi, uganda, 60), "far": (rfp, nigeria, 150)}
    return {
        name: listed(
            type=kind, country=country, delivery_type=DeliveryTypeFactory(), application_deadline=in_days(days)
        )
        for name, (kind, country, days) in fields.items()
    }


@pytest.mark.parametrize(
    "data, expected",
    [
        (lambda c: {}, {"soon", "later", "far"}),
        (lambda c: {"type": "eoi"}, {"later"}),
        (lambda c: {"country": ["KEN"]}, {"soon"}),
        (lambda c: {"country": ["KEN", "NGA"]}, {"soon", "far"}),
        (lambda c: {"delivery_type": [c["soon"].delivery_type_id]}, {"soon"}),
        (lambda c: {"delivery_type": [c["soon"].delivery_type_id, c["far"].delivery_type_id]}, {"soon", "far"}),
        (lambda c: {"deadline_range": "next_30_days"}, {"soon"}),
        (lambda c: {"deadline_range": "next_3_months"}, {"soon", "later"}),
        (lambda c: {"deadline_range": "next_6_months"}, {"soon", "later", "far"}),
        (lambda c: custom_range(30, 90), {"later"}),
        (lambda c: {"type": "rfp", "deadline_range": "next_3_months"}, {"soon"}),
    ],
)
def test_each_filter_narrows_the_results(catalogue, data, expected):
    filterset = marketplace_filter(data(catalogue))

    assert filterset.is_valid(), filterset.errors
    assert set(filterset.qs) == {catalogue[name] for name in expected}


@pytest.mark.parametrize(
    "data, field",
    [
        # Malawi has no listed solicitation, as for a saved link whose solicitation has since closed.
        ({"country": ["MWI"]}, "country"),
        # To before From.
        (custom_range(9, 2), "deadline"),
    ],
)
def test_invalid_filter_is_an_error(catalogue, data, field):
    filterset = marketplace_filter(data)

    assert not filterset.is_valid()
    assert field in filterset.errors


@pytest.mark.parametrize(
    "deadline",
    [in_days(30, hour=0, minute=30), in_days(40, hour=23, minute=59)],
    ids=["early on the first day", "late on the last day"],
)
def test_custom_deadline_range_takes_in_the_whole_of_both_days(deadline):
    solicitation = listed(application_deadline=deadline)

    filterset = marketplace_filter(custom_range(30, 40))

    assert list(filterset.qs) == [solicitation]


def test_choices_come_only_from_listed_solicitations():
    shown, private, expired, draft = DeliveryTypeFactory.create_batch(4)
    listed(country=Country.objects.get(code="KEN"), delivery_type=shown)
    SolicitationFactory(
        status=Solicitation.Status.ACTIVE, public=False, country=Country.objects.get(code="UGA"), delivery_type=private
    )
    listed(deadline_passed=True, country=Country.objects.get(code="NGA"), delivery_type=expired)
    SolicitationFactory(public=True, country=Country.objects.get(code="MWI"), delivery_type=draft)

    form = marketplace_filter({}).form

    assert [country.code for country in form.fields["country"].queryset] == ["KEN"]
    assert list(form.fields["delivery_type"].queryset) == [shown]


def test_filter_with_no_options_is_left_out():
    listed(country=None, delivery_type=None)

    assert list(marketplace_filter({}).form.fields) == ["type", "deadline"]


def test_value_submitted_for_a_filter_with_no_options_is_refused():
    # A saved link from when Kenya had a listed solicitation; the one listed now has no country.
    listed(country=None)

    filterset = marketplace_filter({"country": ["KEN"]})

    assert not filterset.is_valid()
    assert "country" in filterset.errors


@pytest.mark.usefixtures("solicitations_on")
def test_invalid_filter_shows_why_the_list_is_empty(client, catalogue):
    response = client.get(MARKETPLACE_URL, custom_range(9, 2))

    assert list(response.context["solicitations"]) == []
    assert "The From date must be on or before the To date." in response.content.decode()


@pytest.mark.usefixtures("solicitations_on")
@pytest.mark.parametrize(
    "params, applied",
    [
        ({}, False),
        ({"page": "1"}, False),
        # Picked "Custom range" but gave no dates, so nothing is narrowed.
        ({"deadline_range": "custom"}, False),
        ({"type": "rfp"}, True),
        ({"country": ["KEN", ""]}, True),
        # Refused, which empties the list.
        ({"country": ["MWI"]}, True),
    ],
)
def test_clear_filters_shows_only_when_filters_apply(client, catalogue, params, applied):
    response = client.get(MARKETPLACE_URL, params)

    assert response.context["filters_applied"] is applied


@pytest.mark.usefixtures("solicitations_on")
def test_every_filter_label_points_at_its_input(client, catalogue):
    content = client.get(MARKETPLACE_URL).content.decode()

    for label_for in re.findall(r'<label class="title-sm"\s+for="([^"]*)"', content):
        assert f'id="{label_for}"' in content, label_for


@pytest.mark.usefixtures("solicitations_on")
def test_page_links_keep_the_filters(client):
    for _ in range(13):
        listed(type=Solicitation.Type.EOI)

    response = client.get(MARKETPLACE_URL, {"type": "eoi"})

    assert response.context["page_obj"].paginator.num_pages == 2
    assert 'href="?type=eoi&amp;page=2"' in response.content.decode()
