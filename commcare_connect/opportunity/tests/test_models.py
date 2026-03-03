import datetime

import pytest

from commcare_connect.opportunity.models import OpportunityActiveEvent  # added via pghistory
from commcare_connect.opportunity.models import PaymentInvoiceStatusEvent  # added via pghistory
from commcare_connect.opportunity.models import (
    InvoiceStatus,
    Opportunity,
    OpportunityClaimLimit,
    PaymentInvoice,
    UserVisit,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedModuleFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.utils.invoice import generate_invoice_number
from commcare_connect.opportunity.visit_import import update_payment_accrued
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MobileUserFactory
from commcare_connect.utils.flags import Flags


@pytest.mark.django_db
class TestPaymentInvoice:
    def test_pghistory_tracking(self):
        payment_invoice = PaymentInvoiceFactory()

        invoice_status_events = payment_invoice.status_events.all()
        assert len(invoice_status_events) == 1
        assert invoice_status_events[0].status == InvoiceStatus.PENDING_NM_REVIEW
        # no context since this was not done via django view/request but directly via model
        assert invoice_status_events[0].pgh_context is None

        payment_invoice.status = InvoiceStatus.PENDING_PM_REVIEW
        payment_invoice.save()

        assert payment_invoice.status_events.count() == 2
        recent_invoice_status_event = payment_invoice.status_events.last()
        assert recent_invoice_status_event.status == InvoiceStatus.PENDING_PM_REVIEW
        # no context since this was not done via django view/request but directly via model
        assert recent_invoice_status_event.pgh_context is None

    def test_pghistory_tracking_for_bulk_actions(self):
        opportunity = OpportunityFactory()
        payment_invoices = []

        assert PaymentInvoiceStatusEvent.objects.count() == 0
        assert PaymentInvoice.objects.count() == 0

        for counter in range(1, 11):
            payment_invoices.append(
                PaymentInvoice(
                    opportunity=opportunity,
                    amount=10,
                    date=datetime.date.today(),
                    invoice_number=generate_invoice_number(),
                )
            )

        # bulk create action
        created_invoices = PaymentInvoice.objects.bulk_create(payment_invoices)
        created_invoice_ids = {invoice.pk for invoice in created_invoices}

        # assert events created for each created record
        assert len(created_invoices) == 10
        assert PaymentInvoiceStatusEvent.objects.count() == 10
        assert PaymentInvoiceStatusEvent.objects.filter(status=InvoiceStatus.PENDING_NM_REVIEW).count() == 10

        create_invoice_events_invoice_obj_ids = {
            invoice_event.pgh_obj_id for invoice_event in PaymentInvoiceStatusEvent.objects.all()
        }
        assert created_invoice_ids == create_invoice_events_invoice_obj_ids

        # bulk update action
        updated_invoices_count = PaymentInvoice.objects.filter(pk__in=created_invoice_ids).update(
            status=InvoiceStatus.PENDING_PM_REVIEW
        )

        # assert successful update
        assert updated_invoices_count == 10
        assert PaymentInvoice.objects.filter(status=InvoiceStatus.PENDING_NM_REVIEW).count() == 0
        assert PaymentInvoice.objects.filter(status=InvoiceStatus.PENDING_PM_REVIEW).count() == 10

        # assert new events created for each updated record
        assert PaymentInvoiceStatusEvent.objects.count() == 20
        assert PaymentInvoiceStatusEvent.objects.filter(status=InvoiceStatus.PENDING_NM_REVIEW).count() == 10
        assert PaymentInvoiceStatusEvent.objects.filter(status=InvoiceStatus.PENDING_PM_REVIEW).count() == 10

        # assert expected status value for the recent event for each record
        for invoice in created_invoices:
            assert invoice.status_events.last().status == InvoiceStatus.PENDING_PM_REVIEW


@pytest.mark.django_db
def test_learn_progress(opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access_1, access_2 = OpportunityAccessFactory.create_batch(2, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory(module=learn_module, opportunity_access=access_1)
    assert access_1.learn_progress == 100
    assert access_2.learn_progress == 0


@pytest.mark.django_db
@pytest.mark.parametrize("opportunity", [{}, {"opp_options": {"managed": True}}], indirect=True)
def test_opportunity_stats(opportunity: Opportunity, user: User):
    payment_unit_sub = PaymentUnitFactory.create(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    payment_unit1 = PaymentUnitFactory.create(
        opportunity=opportunity,
        max_total=100,
        max_daily=10,
        amount=3,
        parent_payment_unit=payment_unit_sub,
    )
    payment_unit2 = PaymentUnitFactory.create(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    assert set(list(opportunity.paymentunit_set.values_list("id", flat=True))) == {
        payment_unit1.id,
        payment_unit2.id,
        payment_unit_sub.id,
    }
    payment_units = [payment_unit_sub, payment_unit1, payment_unit2]
    budget_per_user = sum(pu.max_total * pu.amount for pu in payment_units)

    if opportunity.managed:
        budget_per_user += sum(pu.max_total * pu.org_amount for pu in payment_units)
    opportunity.total_budget = budget_per_user * 3

    payment_units = [payment_unit1, payment_unit2, payment_unit_sub]
    assert opportunity.budget_per_user == sum([p.amount * p.max_total for p in payment_units])
    assert opportunity.number_of_users == 3
    assert opportunity.allotted_visits == sum([pu.max_total for pu in payment_units]) * opportunity.number_of_users
    assert opportunity.max_visits_per_user == sum([pu.max_total for pu in payment_units])
    assert opportunity.daily_max_visits_per_user == sum([pu.max_daily for pu in payment_units])
    assert opportunity.budget_per_visit == sum([pu.amount for pu in payment_units])

    access = OpportunityAccessFactory(user=user, opportunity=opportunity)
    claim = OpportunityClaimFactory(opportunity_access=access)

    ocl1 = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit1)
    ocl2 = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit2)

    assert opportunity.claimed_budget == (
        ocl1.max_visits * (payment_unit1.amount + (payment_unit1.org_amount if opportunity.managed else 0))
    ) + (ocl2.max_visits * (payment_unit2.amount + (payment_unit2.org_amount if opportunity.managed else 0)))
    assert opportunity.remaining_budget == opportunity.total_budget - opportunity.claimed_budget


@pytest.mark.django_db
def test_claim_limits(opportunity: Opportunity):
    payment_unit_sub = PaymentUnitFactory(opportunity=opportunity, parent_payment_unit=None)
    payment_units = PaymentUnitFactory.create_batch(2, opportunity=opportunity, parent_payment_unit=None) + [
        payment_unit_sub
    ]
    payment_unit_sub.parent_payment_unit = payment_units[0]
    budget_per_user = sum([p.max_total * p.amount for p in payment_units])
    # budget not enough for more than 2 users
    opportunity.total_budget = budget_per_user * 1.5
    mobile_users = MobileUserFactory.create_batch(3)
    for mobile_user in mobile_users:
        access = OpportunityAccessFactory(user=mobile_user, opportunity=opportunity, accepted=True)
        claim = OpportunityClaimFactory(opportunity_access=access)
        OpportunityClaimLimit.create_claim_limits(opportunity, claim)

    assert opportunity.claimed_budget <= int(opportunity.total_budget)
    assert opportunity.claimed_visits <= int(opportunity.allotted_visits)
    assert opportunity.remaining_budget < payment_units[0].amount + payment_units[1].amount

    def limit_count(user):
        return OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__user=user).count()

    # enough for 1st user
    assert limit_count(mobile_users[0]) == 3
    # partially enough for 2nd user, depending on paymentunit.amount
    assert limit_count(mobile_users[1]) in [2, 3]
    # Not enough for 3rd user at all
    assert limit_count(mobile_users[2]) == 0


@pytest.mark.django_db
def test_access_visit_count(opportunity: Opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    assert access.visit_count == 0

    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    completed_work = CompletedWorkFactory(payment_unit=payment_unit, opportunity_access=access)
    UserVisitFactory(
        completed_work=completed_work, deliver_unit=deliver_unit, user=access.user, opportunity=access.opportunity
    )
    update_payment_accrued(opportunity, [access.user])
    assert access.visit_count == 1


@pytest.mark.django_db
class TestOpportunityActiveTracking:
    def test_pghistory_records_initial_active_state(self):
        opp = OpportunityFactory()
        events = OpportunityActiveEvent.objects.filter(pgh_obj=opp).order_by("pgh_id")
        assert events.count() == 1
        assert events.first().active is True

    def test_pghistory_records_manual_deactivation(self):
        opp = OpportunityFactory()
        opp.active = False
        opp.save()
        events = OpportunityActiveEvent.objects.filter(pgh_obj=opp).order_by("pgh_id")
        assert events.count() == 2
        assert events.last().active is False
        # No request context in tests — both events should have no context
        assert events.first().pgh_context is None
        assert events.last().pgh_context is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query_flags, expected_keys",
    [
        ([Flags.DUPLICATE.value], {"duplicate"}),
        ([Flags.DUPLICATE.value, Flags.GPS.value], {"duplicate", "gps"}),
        ([], {"duplicate", "gps", "clean"}),
    ],
)
def test_uservisit_queryset_with_any_flags(query_flags, expected_keys):
    visits = {
        "duplicate": UserVisitFactory(
            flagged=True,
            flag_reason={"flags": [(Flags.DUPLICATE.value, "Duplicate submission")]},
        ),
        "gps": UserVisitFactory(
            flagged=True,
            flag_reason={"flags": [(Flags.GPS.value, "GPS missing")]},
        ),
        "clean": UserVisitFactory(
            flagged=False,
            flag_reason=None,
        ),
    }

    queryset = UserVisit.objects.with_any_flags(query_flags)
    result_ids = {visit_id for visit_id in queryset.values_list("id", flat=True)}
    expected_ids = {visits[key].id for key in expected_keys}

    assert result_ids == expected_ids
