import importlib

import pytest
from django.apps import apps as django_apps
from rest_framework.test import APIClient, APIRequestFactory

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.models import OpportunityClaimLimit
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    HQApiKeyFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    OpportunityVerificationFlagsFactory,
    PaymentUnitFactory,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.tests.factories import ManagedOpportunityFactory
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import (
    ConnectIdUserLinkFactory,
    MobileUserFactory,
    OrgWithUsersFactory,
    ProgramManagerOrgWithUsersFactory,
    UserFactory,
)


@pytest.fixture(autouse=True)
def media_storage(settings, tmpdir):
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture()
def api_rf() -> APIRequestFactory:
    """APIRequestFactory instance"""

    return APIRequestFactory()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def organization(db) -> Organization:
    return OrgWithUsersFactory()


@pytest.fixture
def user(db) -> User:
    return UserFactory()


@pytest.fixture()
def opportunity(request, organization):
    hq_server = HQServerFactory()
    api_key = HQApiKeyFactory(hq_server=hq_server)
    learn_app = CommCareAppFactory(hq_server=hq_server)
    deliver_app = CommCareAppFactory(hq_server=hq_server)
    verification_flags = getattr(request, "param", {}).get("verification_flags", {})
    opp_options = {
        "is_test": False,
        "hq_server": hq_server,
        "api_key": api_key,
        "learn_app": learn_app,
        "deliver_app": deliver_app,
        "organization": organization,
    }
    opp_options.update(getattr(request, "param", {}).get("opp_options", {}))
    if opp_options.get("managed", False):
        factory = ManagedOpportunityFactory(**opp_options)
    else:
        factory = OpportunityFactory(**opp_options)
    OpportunityVerificationFlagsFactory(opportunity=factory, **verification_flags)
    return factory


@pytest.fixture
def mobile_user(db, opportunity) -> User:
    user = MobileUserFactory()
    OpportunityAccessFactory(user=user, opportunity=opportunity)
    PaymentUnitFactory(opportunity=opportunity)
    return user


@pytest.fixture
def user_with_connectid_link(db, opportunity):
    user = MobileUserFactory()
    ConnectIdUserLinkFactory(
        user=user,
        commcare_username=f"test@{opportunity.learn_app.cc_domain}.commcarehq.org",
        hq_server=opportunity.hq_server,
    )
    if opportunity.learn_app.cc_domain != opportunity.deliver_app.cc_domain:
        ConnectIdUserLinkFactory(
            user=user, commcare_username=f"test@{opportunity.deliver_app.cc_domain}.commcarehq.org"
        )
    return user


@pytest.fixture
def paymentunit_options():
    # let tests parametrize as needed
    return {}


@pytest.fixture
def mobile_user_with_connect_link(db, opportunity, paymentunit_options) -> User:
    user = MobileUserFactory()
    access = OpportunityAccessFactory(user=user, opportunity=opportunity, accepted=True)
    claim = OpportunityClaimFactory(end_date=opportunity.end_date, opportunity_access=access)
    payment_units = PaymentUnitFactory.create_batch(
        2, opportunity=opportunity, parent_payment_unit=None, **(paymentunit_options)
    )
    budget_per_user = sum([p.max_total * p.amount for p in payment_units])
    opportunity.total_budget = budget_per_user
    OpportunityClaimLimit.create_claim_limits(opportunity, claim)
    ConnectIdUserLinkFactory(user=user, commcare_username=f"test@{opportunity.learn_app.cc_domain}.commcarehq.org")
    if opportunity.learn_app.cc_domain != opportunity.deliver_app.cc_domain:
        ConnectIdUserLinkFactory(
            user=user, commcare_username=f"test@{opportunity.deliver_app.cc_domain}.commcarehq.org"
        )
    return user


@pytest.fixture
def org_user_member(organization) -> User:
    return organization.memberships.filter(role="member").first().user


@pytest.fixture
def org_user_admin(organization) -> User:
    return organization.memberships.filter(role="admin").first().user


@pytest.fixture
def program_manager_org(db) -> Organization:
    return ProgramManagerOrgWithUsersFactory()


@pytest.fixture
def program_manager_org_user_member(program_manager_org) -> User:
    return program_manager_org.memberships.filter(role="member").first().user


@pytest.fixture
def program_manager_org_user_admin(program_manager_org) -> User:
    return program_manager_org.memberships.filter(role="admin").first().user


@pytest.fixture(autouse=True)
def ensure_currency_country_data(db):
    # These models get flushed in between tests; so make sure they exist
    Currency = django_apps.get_model("opportunity", "Currency")
    if Currency.objects.exists():
        return
    migration_module = importlib.import_module(
        "commcare_connect.opportunity.migrations.0092_currency_country_opportunity_currency_fk"
    )
    migration_module.load_currency_and_country_data(django_apps, schema_editor=None)
