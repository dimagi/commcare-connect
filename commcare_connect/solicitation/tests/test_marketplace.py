import datetime
import re
from urllib.parse import urlencode

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import localdate, now
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.opportunity.models import Country
from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory
from commcare_connect.solicitation.filters import MarketplaceFilterSet
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationAttachment
from commcare_connect.solicitation.tests.factories import (
    SolicitationApplicationFactory,
    SolicitationEvaluationCriterionFactory,
    SolicitationFactory,
    SolicitationInvitationFactory,
    SolicitationQuestionFactory,
)
from commcare_connect.solicitation.tests.test_solicitation_statuses import DEADLINE_CASES
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory

pytestmark = pytest.mark.django_db

MARKETPLACE_URL = reverse("marketplace:list")


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


@pytest.mark.usefixtures("solicitations_on")
def test_cards_link_to_the_current_organizations_applications(client, org_user_admin, organization):
    applied, not_applied = listed(), listed()
    application = SolicitationApplicationFactory(
        solicitation=applied, organization=organization, status=SolicitationApplication.Status.SUBMITTED
    )
    SolicitationApplicationFactory(solicitation=not_applied, status=SolicitationApplication.Status.SUBMITTED)
    client.force_login(org_user_admin)

    content = client.get(MARKETPLACE_URL).content.decode()

    assert content.count("View application") == 1
    assert f'href="{application.applicant_url}"' in content


@pytest.mark.usefixtures("solicitations_on")
def test_queries_do_not_grow_with_the_number_of_applications(client, org_user_admin, organization):
    client.force_login(org_user_admin)

    def queries_for_page():
        with CaptureQueriesContext(connection) as queries:
            client.get(MARKETPLACE_URL)
        return len(queries)

    def applied_card():
        SolicitationApplicationFactory(solicitation=listed(), organization=organization)

    applied_card()
    queries_for_page()
    one_card = queries_for_page()
    for _ in range(4):
        applied_card()

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


@pytest.mark.usefixtures("solicitations_on")
@pytest.mark.parametrize("has_org", [True, False], ids=["with an organization", "without an organization"])
def test_sidebar_links_logged_in_users_to_the_marketplace(client, request, has_org):
    user = request.getfixturevalue("org_user_member" if has_org else "user")
    client.force_login(user)

    content = client.get(MARKETPLACE_URL).content.decode()

    assert f'href="{MARKETPLACE_URL}"' in content


@pytest.mark.parametrize("switch_on", [True, False])
def test_sidebar_marketplace_link_follows_the_switch(client, organization, org_user_member, switch_on):
    client.force_login(org_user_member)

    with override_switch(SOLICITATIONS, active=switch_on):
        content = client.get(reverse("opportunity:list", args=(organization.slug,))).content.decode()

    assert (f'href="{MARKETPLACE_URL}"' in content) is switch_on


def private(**kwargs):
    return SolicitationFactory(status=Solicitation.Status.ACTIVE, public=False, **kwargs)


def detail_url(solicitation):
    return reverse("marketplace:detail", args=[solicitation.solicitation_id])


@pytest.mark.usefixtures("solicitations_on")
def test_detail_shows_scope_but_not_questions_or_criteria(client):
    solicitation = listed(description="Reach 5,000 households.")
    SolicitationQuestionFactory(solicitation=solicitation, text="Describe your team")
    SolicitationEvaluationCriterionFactory(solicitation=solicitation, label="Past performance")

    response = client.get(detail_url(solicitation))

    content = response.content.decode()
    assert response.status_code == 200
    assert "Reach 5,000 households." in content
    assert "Describe your team" not in content
    assert "Past performance" not in content


@pytest.mark.usefixtures("solicitations_on")
@pytest.mark.parametrize(
    "status, shows_apply",
    [
        (None, True),
        (SolicitationApplication.Status.DRAFT, False),
        (SolicitationApplication.Status.SUBMITTED, False),
        (SolicitationApplication.Status.WITHDRAWN, False),
    ],
)
def test_detail_offers_apply_until_the_organization_has_applied(
    client, org_user_admin, organization, status, shows_apply
):
    solicitation = listed()
    application = None
    if status is not None:
        application = SolicitationApplicationFactory(
            solicitation=solicitation, organization=organization, status=status
        )
    client.force_login(org_user_admin)

    content = client.get(detail_url(solicitation)).content.decode()

    assert (reverse("marketplace:apply", args=[solicitation.solicitation_id]) in content) == shows_apply
    assert (application is not None and application.applicant_url in content) == (not shows_apply)


@pytest.mark.usefixtures("solicitations_on")
@pytest.mark.parametrize(
    "status, label",
    [
        (SolicitationApplication.Status.DRAFT, "Draft"),
        (SolicitationApplication.Status.SUBMITTED, "Submitted"),
        (SolicitationApplication.Status.WITHDRAWN, "Withdrawn"),
    ],
)
def test_detail_shows_the_application_status(client, org_user_admin, organization, status, label):
    solicitation = listed()
    SolicitationApplicationFactory(solicitation=solicitation, organization=organization, status=status)
    client.force_login(org_user_admin)

    content = client.get(detail_url(solicitation)).content.decode()

    assert re.search(rf'<span class="badge[^"]*">{label}</span>', content)


def apply_href(solicitation, **params):
    url = reverse("marketplace:apply", args=[solicitation.solicitation_id])
    return f'href="{url}?{urlencode(params)}"' if params else f'href="{url}"'


@pytest.mark.usefixtures("solicitations_on")
@pytest.mark.parametrize("has_applied", [False, True])
def test_apply_uses_the_organization_the_user_is_in_until_it_has_applied(
    client, org_user_admin, organization, has_applied
):
    """Once the current organization has applied, Apply leads to picking another of the user's organizations."""
    solicitation = listed()
    if has_applied:
        SolicitationApplicationFactory(
            solicitation=solicitation, organization=organization, status=SolicitationApplication.Status.SUBMITTED
        )
    MembershipFactory(user=org_user_admin, role="admin")
    client.force_login(org_user_admin)

    content = client.get(detail_url(solicitation)).content.decode()

    expected = apply_href(solicitation) if has_applied else apply_href(solicitation, organization=organization.slug)
    assert expected in content


@pytest.mark.usefixtures("solicitations_on")
def test_apply_asks_logged_out_visitors_to_log_in_then_pick(client):
    solicitation = listed()

    content = client.get(detail_url(solicitation)).content.decode()

    assert apply_href(solicitation) in content


def invite(solicitation, organization, invited):
    """Invites ``organization`` to ``solicitation``, or another organization when ``invited`` is False."""
    SolicitationInvitationFactory(
        solicitation=solicitation, organization=organization if invited else OrganizationFactory()
    )


@pytest.mark.usefixtures("solicitations_on")
@pytest.mark.parametrize("invited, status_code", [(True, 200), (False, 404)])
def test_private_detail_is_only_for_members_of_invited_organizations(
    client, org_user_member, organization, invited, status_code
):
    solicitation = private()
    invite(solicitation, organization, invited)
    client.force_login(org_user_member)

    assert client.get(detail_url(solicitation)).status_code == status_code


@pytest.mark.usefixtures("solicitations_on")
def test_private_detail_is_not_found_logged_out(client):
    solicitation = private()
    SolicitationInvitationFactory(solicitation=solicitation)

    assert client.get(detail_url(solicitation)).status_code == 404


@pytest.mark.usefixtures("solicitations_on")
def test_cards_link_to_the_detail(client):
    solicitation = listed()

    assert detail_url(solicitation) in client.get(MARKETPLACE_URL).content.decode()


def attachment_for(solicitation):
    return SolicitationAttachment.objects.create(
        solicitation=solicitation, file=SimpleUploadedFile("scope.pdf", b"%PDF"), filename="Scope of work.pdf"
    )


def attachment_url(attachment):
    return reverse("marketplace:attachment", args=[attachment.solicitation.solicitation_id, attachment.pk])


@pytest.mark.usefixtures("solicitations_on")
def test_detail_shows_budget_deadline_and_attachments(client):
    solicitation = listed(budget_min=10_000, budget_max=50_000, application_deadline=in_days(10, hour=9))
    attachment = attachment_for(solicitation)

    content = client.get(detail_url(solicitation)).content.decode()

    assert "10,000 – 50,000" in content
    assert in_days(10, hour=9).strftime("%-d %b %Y, 09:00") in content
    assert attachment_url(attachment) in content


@pytest.mark.usefixtures("solicitations_on")
def test_attachment_downloads_through_the_app(client):
    attachment = attachment_for(listed())

    response = client.get(attachment_url(attachment))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"%PDF"
    assert 'filename="Scope of work.pdf"' in response["Content-Disposition"]


@pytest.mark.usefixtures("solicitations_on")
@pytest.mark.parametrize("invited, status_code", [(True, 200), (False, 404)])
def test_private_attachment_is_only_for_members_of_invited_organizations(
    client, org_user_member, organization, invited, status_code
):
    attachment = attachment_for(private())
    invite(attachment.solicitation, organization, invited)
    client.force_login(org_user_member)

    response = client.get(attachment_url(attachment))

    assert response.status_code == status_code
