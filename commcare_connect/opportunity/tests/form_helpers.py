"""Request, data and HQ-mock builders shared by the opportunity form test modules."""

import json

import pytest

from commcare_connect.opportunity.forms import OpportunityChangeForm, OpportunityInitForm
from commcare_connect.organization.models import UserOrganizationMembership
from commcare_connect.program.models import ProgramApplicationStatus
from commcare_connect.program.tests.factories import ProgramApplicationFactory
from commcare_connect.users.tests.factories import UserFactory
from commcare_connect.utils.test_utils import make_membership, make_request


def request_for(org, opportunity, role=UserOrganizationMembership.Role.ADMIN):
    user = UserFactory()
    return make_request(user, org=org, membership=make_membership(org, user, role), opportunity=opportunity)


def change_form(opportunity, request, **overrides):
    data = {
        "name": opportunity.name,
        "description": "Updated description",
        "short_description": "Updated short",
        "active": True,
        "currency": opportunity.currency.code,
        "country": opportunity.country,
        "is_test": opportunity.is_test,
        "delivery_type": opportunity.delivery_type_id,
        "supervising_organization": opportunity.supervising_organization_id,
    }
    data.update(overrides)
    return OpportunityChangeForm(data=data, instance=opportunity, request=request)


def mock_hq_app(httpx_mock, hq_server, cc_domain, cc_app_id, status=200):
    """HQ answers `status` when the app is fetched from `hq_server`, the server of the key being checked."""
    url = f"{hq_server.url}/a/{cc_domain}/api/v0.5/application/{cc_app_id}/"
    httpx_mock.add_response(url=url, status_code=status, json={})


def mock_hq_apps(httpx_mock, opportunity, learn_status=200, deliver_status=200):
    """HQ answers `learn_status` and `deliver_status` when asked for the opportunity's current apps."""
    for app, status in ((opportunity.learn_app, learn_status), (opportunity.deliver_app, deliver_status)):
        mock_hq_app(httpx_mock, opportunity.hq_server, app.cc_domain, app.cc_app_id, status)


class OpportunityInitFormTestBase:
    """Shared fixtures and form builders for the opportunity setup form tests."""

    @pytest.fixture(autouse=True)
    def program_application(self, opportunity):
        return ProgramApplicationFactory(
            organization=opportunity.organization,
            program=opportunity.program,
            status=ProgramApplicationStatus.ACCEPTED,
        )

    def _build_data(self, opportunity):
        learn_app = opportunity.learn_app
        deliver_app = opportunity.deliver_app
        return {
            "hq_server": opportunity.hq_server.id,
            "api_key": str(opportunity.api_key.id),
            "learn_app_domain": learn_app.cc_domain,
            "learn_app": json.dumps({"id": learn_app.cc_app_id, "name": learn_app.name}),
            "learn_app_description": "Learn description",
            "learn_app_passing_score": 70,
            "deliver_app_domain": deliver_app.cc_domain,
            "deliver_app": json.dumps({"id": deliver_app.cc_app_id, "name": deliver_app.name}),
            "organization": opportunity.organization.pk,
        }

    def _build_form(self, opportunity, extra_data=None):
        data = {
            "name": "Brand new opportunity",
            "description": "Description",
            "short_description": "Short",
            **self._build_data(opportunity),
            **(extra_data or {}),
        }
        return OpportunityInitForm(data=data, **self._form_kwargs(opportunity))

    @staticmethod
    def _form_kwargs(opportunity):
        return {
            "user": opportunity.api_key.user,
            "org_slug": opportunity.organization.slug,
            "program": opportunity.program,
        }
