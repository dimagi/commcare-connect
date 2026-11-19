import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import now
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.opportunity.models import Country
from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory
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
