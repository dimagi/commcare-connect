import datetime
from collections import Counter

import pytest
from django.utils.timezone import now

from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    DeliverUnitFlagRules,
    InvoiceStatus,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityVerificationFlags,
    Payment,
    PaymentInvoice,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.simulation.scenarios import get_scenario, standard_scenario_names

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name", standard_scenario_names())
def test_scenario_builds(sim, name):
    """Every standard scenario must build at its smallest size; a failure here means a step fell behind the app."""
    scenario = get_scenario(name)

    scenario.build(sim, small=True)

    opportunities = Opportunity.objects.filter(organization__name__in=scenario.ORG_NAMES)
    assert opportunities.exists()
    _assert_history_reads_in_order(opportunities)


def test_delivery_review_shows_every_review_state(sim):
    get_scenario("delivery_review").build(sim, small=True)

    visits = UserVisit.objects.filter(opportunity__name="Delivery Review Opportunity")
    flags = {flag for visit in visits for flag, _ in (visit.flag_reason or {}).get("flags", [])}
    assert {"duplicate", "gps", "duration"} <= flags
    assert {VisitValidationStatus.approved, VisitValidationStatus.rejected} <= _values(visits, "status")
    # Left with the network manager. A duplicate keeps the receiver's "duplicate" status until someone reviews it,
    # and the review step draws from both, so either one counts as still pending.
    assert visits.filter(status__in=[VisitValidationStatus.pending, VisitValidationStatus.duplicate]).exists()
    reviewed = visits.filter(status=VisitValidationStatus.approved, flagged=True)
    assert {VisitReviewStatus.agree, VisitReviewStatus.disagree} <= _values(reviewed, "review_status")
    # Clean visits are approved on arrival and need no review.
    assert _values(visits.filter(flagged=False), "status") == {VisitValidationStatus.approved}


def test_learning_shows_every_learn_stage(sim):
    get_scenario("learning").build(sim, small=True)

    stages = Counter(
        _learn_stage(access) for access in OpportunityAccess.objects.filter(opportunity__name="Learning Opportunity")
    )

    assert stages == {"invited": 1, "part way": 1, "learned, not claimed": 1, "failed assessment": 1, "claimed": 1}


def test_opp_setup_shows_every_setup_state(sim):
    get_scenario("opp_setup").build(sim, small=True)

    opportunities = {
        opportunity.name: opportunity
        for opportunity in Opportunity.objects.filter(organization__name__in=get_scenario("opp_setup").ORG_NAMES)
    }
    today = now().date()
    assert not opportunities["Draft Opportunity"].is_setup_complete
    active = opportunities["Active Opportunity"]
    assert active.is_setup_complete and active.start_date <= today <= active.end_date
    ended = opportunities["Ended Opportunity"]
    assert ended.is_setup_complete and ended.end_date < today
    multi_unit = opportunities["Multi-unit Opportunity"]
    assert multi_unit.paymentunit_set.count() == 2
    flags = OpportunityVerificationFlags.objects.get(opportunity=multi_unit)
    assert (flags.duplicate, flags.gps) == (True, True)
    assert DeliverUnitFlagRules.objects.filter(opportunity=multi_unit).count() == 2


def test_payments_accrue_what_was_approved_and_bill_it(sim):
    get_scenario("payments").build(sim, small=True)

    opportunity = Opportunity.objects.get(name="Payments Opportunity")
    payment_unit = opportunity.paymentunit_set.get()
    for access in OpportunityAccess.objects.filter(opportunity=opportunity):
        approved = access.uservisit_set.filter(
            status=VisitValidationStatus.approved, review_status=VisitReviewStatus.agree
        )
        assert access.payment_accrued == approved.count() * payment_unit.amount
        assert 0 < access.total_paid < access.payment_accrued

    invoices = PaymentInvoice.objects.filter(opportunity=opportunity)
    assert Counter(invoices.values_list("status", flat=True)) == {
        InvoiceStatus.PAID: 1,
        InvoiceStatus.READY_TO_PAY: 1,
        InvoiceStatus.PENDING_PM_REVIEW: 1,
    }
    for invoice in invoices:
        for item in invoice.work_items.select_related("completed_work"):
            assert _latest(item.completed_work.uservisit_set, "visit_date").date() <= invoice.end_date < invoice.date
    # Some workers were paid all they had accrued by the payment date, the others part of it.
    shares = set()
    for payment in Payment.objects.filter(opportunity_access__opportunity=opportunity):
        visits_before = payment.opportunity_access.uservisit_set.filter(visit_date__lte=payment.date_paid)
        shares.add(payment.amount / (visits_before.count() * payment_unit.amount))
    assert len(shares) == 2 and 1 in shares


def test_payment_units_accrue_each_unit_at_its_rate(sim):
    get_scenario("payment_units").build(sim, small=True)

    opportunity = Opportunity.objects.get(name="Payment Units Opportunity")
    payment_units = list(opportunity.paymentunit_set.all())
    assert len(payment_units) == 2
    for access in OpportunityAccess.objects.filter(opportunity=opportunity):
        visits = access.uservisit_set.filter(status=VisitValidationStatus.approved)
        expected = sum(visits.filter(deliver_unit__payment_unit=unit).count() * unit.amount for unit in payment_units)
        assert access.payment_accrued == expected
        assert 0 < access.total_paid <= access.payment_accrued

    invoice = PaymentInvoice.objects.get(opportunity=opportunity)
    billed_units = set(invoice.work_items.values_list("completed_work__payment_unit", flat=True))
    assert billed_units == {unit.pk for unit in payment_units}


def _assert_history_reads_in_order(opportunities):
    """Each worker's invite, learn start, learn completion, claim day, first visit and first payment come in that
    order. Each visit is created and changes status after its visit date, and a flagged visit's PM review follows
    its NM review. No visit, payment or invoice is dated in the future."""
    current = now()
    for access in OpportunityAccess.objects.filter(opportunity__in=opportunities):
        steps = [access.invited_date, access.date_learn_started, access.completed_learn_date]
        claim = OpportunityClaim.objects.filter(opportunity_access=access).first()
        if claim:
            steps.append(_start_of(claim.date_claimed))
        visits = access.uservisit_set.all()
        if visits:
            steps.append(_earliest(visits, "visit_date"))
            for visit in visits:
                assert visit.visit_date <= visit.date_created <= current
                assert visit.visit_date <= visit.status_modified_date <= current
                assert visit.review_status_modified_date <= current
                if visit.flagged and visit.review_status != VisitReviewStatus.pending:
                    assert visit.status_modified_date <= visit.review_status_modified_date
        payments = Payment.objects.filter(opportunity_access=access)
        if payments:
            steps.append(_earliest(payments, "date_paid"))
            assert _latest(payments, "date_paid") <= current
        dated = [step for step in steps if step is not None]
        assert dated == sorted(dated), f"{access.user.username}'s history is out of order: {dated}"
        assert not dated or dated[-1] <= current
    for invoice in PaymentInvoice.objects.filter(opportunity__in=opportunities):
        assert invoice.date <= current.date()


def _learn_stage(access):
    if OpportunityClaim.objects.filter(opportunity_access=access).exists():
        return "claimed"
    assessment = Assessment.objects.filter(opportunity_access=access).first()
    if assessment:
        return "learned, not claimed" if assessment.passed else "failed assessment"
    if CompletedModule.objects.filter(opportunity_access=access).exists():
        return "part way"
    return "invited"


def _values(queryset, field):
    return set(queryset.values_list(field, flat=True))


def _earliest(queryset, field):
    return getattr(queryset.earliest(field), field)


def _latest(queryset, field):
    return getattr(queryset.latest(field), field)


def _start_of(day):
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC)
