from http import HTTPStatus

import pytest
from django.urls import reverse
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import SOLICITATIONS
from commcare_connect.solicitation.models import SolicitationApplication
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
