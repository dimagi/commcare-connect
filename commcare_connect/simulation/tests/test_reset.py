import re

import pytest
from django.db import connection

from commcare_connect.opportunity.models import (
    CommCareApp,
    CompletedWork,
    Opportunity,
    OpportunityAccess,
    Payment,
    PaymentInvoice,
    UserVisit,
)
from commcare_connect.opportunity.tests.factories import OpportunityFactory
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program, ProgramApplication
from commcare_connect.simulation.reset import ResetBlocked, reset_orgs
from commcare_connect.simulation.scenarios import get_scenario
from commcare_connect.solicitation.tests.factories import SolicitationFactory
from commcare_connect.users.models import User

pytestmark = pytest.mark.django_db

BUILT = (
    Organization,
    Program,
    ProgramApplication,
    Opportunity,
    CommCareApp,
    OpportunityAccess,
    UserVisit,
    CompletedWork,
    PaymentInvoice,
    Payment,
)


BASELINE_ORGS = get_scenario("baseline").ORG_NAMES


def test_reset_removes_everything_a_scenario_built(sim, user):
    before = _counts()
    scenario = get_scenario("payments")
    scenario.build(sim, small=True)
    assert Payment.objects.filter(invoice__isnull=False).exists()

    reset_orgs(scenario.ORG_NAMES, log=_quiet)

    # Several relations are DO_NOTHING, so a missed row only shows when the deferred foreign keys are checked.
    connection.check_constraints()
    assert _counts() == before
    assert User.objects.filter(pk=user.pk).exists()


def test_reset_leaves_other_organizations_alone(baseline, organization):

    reset_orgs(BASELINE_ORGS, log=_quiet)

    assert Organization.objects.filter(pk=organization.pk).exists()


def test_reset_twice_or_of_nothing_is_a_no_op(baseline):

    reset_orgs(BASELINE_ORGS, log=_quiet)
    reset_orgs(BASELINE_ORGS, log=_quiet)
    reset_orgs(["No such organization"], log=_quiet)

    assert not Organization.objects.filter(name__in=BASELINE_ORGS).exists()


@pytest.mark.parametrize(
    "outside",
    [
        lambda sim_opportunity: {"program": sim_opportunity.program, "managed": True},
        lambda sim_opportunity: {"learn_app": sim_opportunity.learn_app},
        lambda sim_opportunity: {"deliver_app": sim_opportunity.deliver_app},
    ],
    ids=["under-sim-program", "sim-learn-app", "sim-deliver-app"],
)
def test_reset_refuses_when_another_orgs_opportunity_depends_on_it(baseline, organization, outside):
    sim_opportunity = Opportunity.objects.get(organization__name__in=BASELINE_ORGS)
    other = OpportunityFactory(organization=organization, **outside(sim_opportunity))
    before = _counts()

    with pytest.raises(ResetBlocked, match=re.escape(f"{other.name} (id {other.pk}, owned by {organization.slug})")):
        reset_orgs(BASELINE_ORGS, log=_quiet)

    assert _counts() == before
    assert Opportunity.objects.filter(pk=other.pk).exists()


def test_reset_refuses_when_a_solicitation_protects_an_org(baseline):
    pm_org = Organization.objects.get(name=BASELINE_ORGS[0])
    solicitation = SolicitationFactory(organization=pm_org)
    before = _counts()

    with pytest.raises(ResetBlocked, match=rf"Solicitation {solicitation.pk} .*Nothing was deleted"):
        reset_orgs(BASELINE_ORGS, log=_quiet)

    assert _counts() == before


def test_reset_logs_each_organization(baseline):
    logged = []

    reset_orgs(BASELINE_ORGS, log=logged.append)

    assert set(logged) == {
        "Deleting organization Sim Baseline PM (sim-baseline-pm)",
        "Deleting organization Sim Baseline NM (sim-baseline-nm)",
    }


def test_scenario_rebuilds_after_reset(sim):
    scenario = get_scenario("delivery_review")
    scenario.build(sim, small=True)
    built = _counts()

    reset_orgs(scenario.ORG_NAMES, log=_quiet)
    sim.rng.seed(42)
    scenario.build(sim, small=True)

    assert _counts() == built


@pytest.fixture
def baseline(sim):
    """The baseline scenario, built at its smallest size."""
    get_scenario("baseline").build(sim, small=True)


def _quiet(message):
    pass


def _counts():
    return {model.__name__: model.objects.count() for model in BUILT}
