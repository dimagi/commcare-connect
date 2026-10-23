from http import HTTPStatus

import pytest
from django.urls import reverse
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.program.tests.factories import _get_default_country
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication
from commcare_connect.solicitation.tests.factories import SolicitationApplicationFactory, SolicitationFactory
from commcare_connect.solicitation.views import org_solicitations
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory

Status = SolicitationApplication.Status


@pytest.mark.django_db
def test_org_solicitations_scoped_to_org_with_response_counts(program_manager_org):
    solicitation = SolicitationFactory(organization=program_manager_org)
    for status in Status:
        SolicitationApplicationFactory(solicitation=solicitation, status=status)
    empty = SolicitationFactory(organization=program_manager_org)
    SolicitationApplicationFactory(status=Status.SUBMITTED)  # another org's solicitation

    counts = {s.pk: s.response_count for s in org_solicitations(program_manager_org)}

    # Every status except draft and withdrawn counts as a response.
    assert counts == {solicitation.pk: len(Status) - 2, empty.pk: 0}


@pytest.mark.django_db
class TestSolicitationListAccess:
    @pytest.mark.parametrize(
        "switch_active, org_kwargs, role, expected",
        [
            (True, {"program_manager": True}, "admin", HTTPStatus.OK),
            (False, {"program_manager": True}, "admin", HTTPStatus.NOT_FOUND),
            (True, {"program_manager": True}, "member", HTTPStatus.NOT_FOUND),
            (True, {"funder": True}, "admin", HTTPStatus.NOT_FOUND),
            (True, {}, "admin", HTTPStatus.NOT_FOUND),
        ],
    )
    def test_access(self, client, switch_active, org_kwargs, role, expected):
        membership = MembershipFactory(organization=OrganizationFactory(**org_kwargs), role=role)
        client.force_login(membership.user)

        with override_switch(SOLICITATIONS, active=switch_active):
            response = client.get(reverse("solicitation:list", args=(membership.organization.slug,)))

        assert response.status_code == expected


@pytest.mark.django_db
class TestSolicitationNavItem:
    @pytest.mark.parametrize(
        "switch_active, program_manager, role, shown",
        [
            (True, True, "admin", True),
            (False, True, "admin", False),
            (True, True, "member", False),
            (True, False, "admin", False),
        ],
    )
    def test_nav_item(self, client, switch_active, program_manager, role, shown):
        membership = MembershipFactory(organization__program_manager=program_manager, role=role)
        org = membership.organization
        client.force_login(membership.user)

        with override_switch(SOLICITATIONS, active=switch_active):
            response = client.get(reverse("opportunity:list", args=(org.slug,)))

        assert (reverse("solicitation:list", args=(org.slug,)) in response.content.decode()) is shown


def step_url(step, solicitation):
    return reverse(f"solicitation:{step}", args=(solicitation.organization.slug, solicitation.solicitation_id))


@pytest.mark.django_db
class TestSolicitationSteps:
    @pytest.fixture(autouse=True)
    def pm_client(self, client, program_manager_org_user_admin):
        client.force_login(program_manager_org_user_admin)
        with override_switch(SOLICITATIONS, active=True):
            yield client

    @pytest.fixture
    def draft(self, program_manager_org):
        return SolicitationFactory(organization=program_manager_org, country=_get_default_country())

    def test_create_saves_draft_then_goes_to_criteria(self, pm_client, program_manager_org, draft_data):
        response = pm_client.post(reverse("solicitation:create", args=(program_manager_org.slug,)), draft_data)

        solicitation = Solicitation.objects.get(organization=program_manager_org)
        assert solicitation.status == Solicitation.Status.DRAFT
        assert response.url == step_url("criteria", solicitation)

    def test_criteria_step_saves_rows(self, pm_client, draft, program_manager_org_user_admin):
        data = {
            "criteria-TOTAL_FORMS": 1,
            "criteria-INITIAL_FORMS": 0,
            "criteria-0-label": "Technical expertise",
            "criteria-0-weight": 100,
        }

        response = pm_client.post(step_url("criteria", draft), data)

        assert response.url == reverse("solicitation:list", args=(draft.organization.slug,))
        criterion = draft.criteria.get()
        assert (criterion.label, criterion.weight, criterion.created_by) == (
            "Technical expertise",
            100,
            program_manager_org_user_admin.email,
        )

    @pytest.mark.parametrize(
        "status, linked",
        [
            (Solicitation.Status.DRAFT, True),
            (Solicitation.Status.ACTIVE, True),
            (Solicitation.Status.CLOSED, False),
            (Solicitation.Status.CANCELLED, False),
        ],
    )
    def test_list_links_only_editable_titles(self, pm_client, program_manager_org, status, linked):
        solicitation = SolicitationFactory(organization=program_manager_org, status=status)

        response = pm_client.get(reverse("solicitation:list", args=(program_manager_org.slug,)))

        assert (step_url("details", solicitation) in response.content.decode()) is linked

    def test_create_renders(self, pm_client, program_manager_org):
        response = pm_client.get(reverse("solicitation:create", args=(program_manager_org.slug,)))

        assert response.status_code == HTTPStatus.OK
