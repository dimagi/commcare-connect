from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from dateutil.relativedelta import relativedelta

from commcare_connect.opportunity.models import (
    CompletedWork,
    CompletedWorkStatus,
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    ExchangeRateFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.utils.completed_work import update_status
from commcare_connect.opportunity.utils.invoice_line_items import CENTS, get_billable_line_items
from commcare_connect.utils.datetime import get_month_start_date


@pytest.mark.django_db
class TestBillableLineItemsAcrossMonths:
    @pytest.fixture
    def access(self):
        """A USD opportunity with a baseline rate of 1; each test adds its own payment units."""
        opp_access = OpportunityAccessFactory()
        ExchangeRateFactory(
            currency_code=opp_access.opportunity.currency_code, rate=Decimal("1"), rate_date=date(2020, 1, 1)
        )
        return opp_access

    def test_groups_each_month_and_unit_at_the_rate_in_force(self, access):
        two_months_ago, one_month_ago, now = self._recent_months()
        rates = self._pin_monthly_rates(
            access.opportunity, [(two_months_ago, "0.25"), (one_month_ago, "0.50"), (now, "0.75")]
        )
        unit_a = PaymentUnitFactory(opportunity=access.opportunity, amount=100, org_amount=0)
        unit_b = PaymentUnitFactory(opportunity=access.opportunity, amount=50, org_amount=0)
        # One entry per expected line item; the list is each of its works' saved_approved_count.
        groups = [
            (two_months_ago, unit_a, [1, 1]),
            (two_months_ago, unit_b, [1]),
            (one_month_ago, unit_a, [2]),
            (now, unit_a, [1]),
            (now, unit_b, [1]),
        ]
        for approved_on, payment_unit, approvals in groups:
            for approved in approvals:
                self._create_approved_completed_work(access, approved_on, payment_unit, approved=approved)

        items = self._billable_items(access.opportunity)

        assert len(items) == len(groups)
        by_key = {(item.month.month, item.payment_unit_name): item for item in items}
        for approved_on, payment_unit, approvals in groups:
            item = by_key[(approved_on.month, payment_unit.name)]
            self._assert_priced(item, sum(approvals), payment_unit, rates[approved_on.month])

    def test_total_includes_org_pay(self, access):
        payment_unit = PaymentUnitFactory(opportunity=access.opportunity, amount=10, org_amount=4)
        now = datetime.now(tz=timezone.utc)
        self._pin_monthly_rates(access.opportunity, [(now, "2")])
        self._create_approved_completed_work(access, now, payment_unit, approved=2)

        (item,) = self._billable_items(access.opportunity)
        # Raw FLW/Org breakdowns are surfaced separately.
        assert item.flw_pay.local == Decimal("20")
        assert item.org_pay.local == Decimal("8")
        assert item.flw_pay.usd == Decimal("10")
        assert item.org_pay.usd == Decimal("4")
        # Totals always fold in org pay (FLW + Org).
        assert item.total_pay.local == Decimal("28")
        assert item.total_pay.usd == Decimal("14")

    def _billable_items(self, opportunity):
        """These tests span three months back, so bill from before the earliest through today."""
        start_date = (datetime.now(tz=timezone.utc) - relativedelta(months=4)).date()
        return get_billable_line_items(opportunity, start_date, date.today())

    def _create_approved_completed_work(self, opp_access, status_modified_date, payment_unit, approved=1):
        return CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
            saved_approved_count=approved,
            invoiced_approved_count=0,
            status_modified_date=status_modified_date,
        )

    def _recent_months(self):
        now = datetime.now(tz=timezone.utc)
        return now - relativedelta(months=2), now - relativedelta(months=1), now

    def _pin_monthly_rates(self, opportunity, rates):
        return {
            approved_on.month: ExchangeRateFactory(
                currency_code=opportunity.currency_code,
                rate=Decimal(rate),
                rate_date=get_month_start_date(approved_on),
            ).rate
            for approved_on, rate in rates
        }

    def _assert_priced(self, item, expected_count, payment_unit, expected_rate):
        total_local = expected_count * payment_unit.amount

        assert item.number_approved == expected_count
        assert item.total_pay.local == total_local
        assert item.exchange_rate == expected_rate
        assert item.total_pay.usd == (total_local / expected_rate).quantize(CENTS, rounding=ROUND_HALF_EVEN)


@pytest.mark.django_db
class TestUpdateStatus:
    def _create_visit(self, completed_work, deliver_unit, **kwargs):
        opp_access = completed_work.opportunity_access
        if kwargs.get("status") == VisitValidationStatus.approved and "review_status" not in kwargs:
            kwargs["review_status"] = VisitReviewStatus.agree
        return UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=deliver_unit,
            completed_work=completed_work,
            **kwargs,
        )

    def _run_update_status(self, completed_work):
        opp_access = completed_work.opportunity_access
        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

    def test_completed_work_not_updated_to_approved_when_missing_required_visit(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, optional_deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending

    def test_completed_work_updated_to_approved_with_all_visits_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_completed_work_updated_to_approved_with_all_required_visits_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit_1 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )
        optional_deliver_unit_2 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, required_deliver_unit, status=VisitValidationStatus.approved)
        self._create_visit(completed_work, optional_deliver_unit_1, status=VisitValidationStatus.approved)
        self._create_visit(completed_work, optional_deliver_unit_2, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_completed_work_not_updated_to_approved_with_not_all_required_visits_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit_1 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        required_deliver_unit_2 = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, required_deliver_unit_1, status=VisitValidationStatus.approved)
        self._create_visit(completed_work, required_deliver_unit_2, status=VisitValidationStatus.pending)
        self._create_visit(completed_work, optional_deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_managed_opp_completed_work_not_updated_to_approved_without_agreement(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        self._create_visit(
            completed_work,
            optional_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_managed_opp_completed_work_updated_to_approved_with_agreement(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        optional_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._create_visit(
            completed_work,
            optional_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_managed_opp_completed_work_updated_to_approved_with_same_unit_over_limit(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.over_limit,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 2
        assert completed_work.saved_payment_accrued == 100

    def test_managed_opp_completed_work_not_updated_to_approved_with_no_optional_visit(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )
        DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
            optional=True,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            required_deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 0
        assert completed_work.saved_payment_accrued == 0

    def test_completed_work_updated_to_rejected_when_any_visit_rejected(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.rejected,
            reason="Invalid data",
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.rejected
        assert completed_work.reason == "Invalid data"
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_payment_calculations_when_completed_work_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=150)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        for _ in range(3):
            self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 3
        assert completed_work.saved_completed_count == 3
        assert completed_work.saved_payment_accrued == 450
        assert completed_work.saved_payment_accrued_usd > 0

    def test_no_status_update_when_auto_approve_disabled(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=False)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.pending,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_incomplete_completed_work_updated_to_pending_when_visits_not_yet_all_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        # CW starts at the model default: incomplete
        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.incomplete,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.pending

    def test_rejected_completed_work_status_preserved_when_visits_not_all_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.rejected,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.rejected

    def test_rejected_completed_work_updated_to_approved_when_all_visits_now_approved(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.rejected,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.approved)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved

    def test_approved_completed_work_status_preserved_when_visit_reverted(self):
        opp_access = OpportunityAccessFactory(opportunity__auto_approve_payments=True)
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(completed_work, deliver_unit, status=VisitValidationStatus.pending)
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved

    def test_managed_opp_saved_approved_count_uses_agreed_count(self):
        """For managed opps, saved_approved_count tallies only PM-agreed visits.

        An approved-but-unagreed duplicate must not raise the billable count.
        """
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        # One agreed visit — the baseline bill
        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        # Approved but pending — must not raise the billable count
        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        # Approved but disagreed — must also not raise the billable count
        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.disagree,
        )
        self._run_update_status(completed_work)

        # Only the agreed visit should count toward the billable total
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_payment_accrued == 100
        assert completed_work.saved_completed_count == 3

    def test_managed_opp_billable_count_is_min_agreed_across_required_deliver_units(self):
        """Billable count is the minimum agreed count across required deliver units."""
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        du1 = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)
        du2 = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        # DU1: 3 agreed visits
        for _ in range(3):
            self._create_visit(
                completed_work,
                du1,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.agree,
            )
        # DU2: 1 agreed + 2 approved-but-unagreed
        self._create_visit(
            completed_work,
            du2,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        for _ in range(2):
            self._create_visit(
                completed_work,
                du2,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.pending,
            )
        self._run_update_status(completed_work)

        # min(agreed_DU1=3, agreed_DU2=1) = 1
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_payment_accrued == 100
        assert completed_work.saved_completed_count == 3

    def test_managed_opp_billable_count_caps_at_agreed_optional_visits(self):
        """Optional unit's agreed count caps the billable total, not its approved count."""
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        required_du = DeliverUnitFactory(app=opp_access.opportunity.deliver_app, payment_unit=payment_unit)
        optional_du = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app, payment_unit=payment_unit, optional=True
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        for _ in range(3):
            self._create_visit(
                completed_work,
                required_du,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.agree,
            )
        for _ in range(2):
            self._create_visit(
                completed_work,
                optional_du,
                status=VisitValidationStatus.approved,
                review_status=VisitReviewStatus.agree,
            )
        self._create_visit(
            completed_work,
            optional_du,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        self._run_update_status(completed_work)

        # min(required_agreed=3, optional_agreed=2) = 2
        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 2
        assert completed_work.saved_payment_accrued == 200
        assert completed_work.saved_completed_count == 3

    def test_managed_opp_approved_completed_work_status_preserved_when_agreement_revoked(self):
        opp_access = OpportunityAccessFactory(
            opportunity=OpportunityFactory(auto_approve_payments=True),
        )
        payment_unit = PaymentUnitFactory(opportunity=opp_access.opportunity, amount=100)
        deliver_unit = DeliverUnitFactory(
            app=opp_access.opportunity.deliver_app,
            payment_unit=payment_unit,
        )

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            payment_unit=payment_unit,
        )

        self._create_visit(
            completed_work,
            deliver_unit,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        self._run_update_status(completed_work)

        assert completed_work.status == CompletedWorkStatus.approved
