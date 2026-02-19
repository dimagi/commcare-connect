from datetime import datetime
from unittest.mock import patch

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
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.utils.completed_work import get_uninvoiced_visit_items, update_status
from commcare_connect.program.tests.factories import ManagedOpportunityFactory


@pytest.mark.django_db
class TestUninvoicedVisitItems:
    def test_items_without_prior_invoice(self):
        opp_access = OpportunityAccessFactory()
        CompletedWorkFactory(status=CompletedWorkStatus.pending, opportunity_access=opp_access)
        items = get_uninvoiced_visit_items(opp_access.opportunity)
        assert len(items) == 0

        completed_work = CompletedWorkFactory(status=CompletedWorkStatus.approved, opportunity_access=opp_access)
        completed_work.saved_payment_accrued = completed_work.payment_unit.amount
        completed_work.save()

        items = get_uninvoiced_visit_items(opp_access.opportunity)
        assert len(items) == 1

        invoice_item = items[0]
        assert invoice_item["payment_unit_name"] == completed_work.payment_unit.name
        assert invoice_item["number_approved"] == 1
        assert invoice_item["amount_per_unit"] == completed_work.payment_unit.amount
        assert invoice_item["total_amount_local"] == completed_work.payment_unit.amount

    def test_items_with_prior_invoice(self):
        opp_access = OpportunityAccessFactory()
        invoice = PaymentInvoiceFactory(opportunity=opp_access.opportunity)
        CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
            invoice=invoice,
        )
        items = get_uninvoiced_visit_items(opp_access.opportunity)
        assert len(items) == 0

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
        )
        completed_work.saved_payment_accrued = completed_work.payment_unit.amount
        completed_work.save()

        items = get_uninvoiced_visit_items(opp_access.opportunity)
        assert len(items) == 1

        invoice_item = items[0]
        assert invoice_item["payment_unit_name"] == completed_work.payment_unit.name
        assert invoice_item["number_approved"] == 1
        assert invoice_item["amount_per_unit"] == completed_work.payment_unit.amount
        assert invoice_item["total_amount_local"] == completed_work.payment_unit.amount

    @patch("commcare_connect.opportunity.visit_import.get_exchange_rate")
    def test_same_pu_items_across_multiple_months(self, mock_get_exchange_rate):
        two_months_ago = datetime.now() - relativedelta(months=2)
        one_month_ago = datetime.now() - relativedelta(months=1)
        today = datetime.now()

        ExchangeRateFactory(rate_date=two_months_ago, currency_code="EUR", rate=0.25)
        ExchangeRateFactory(rate_date=one_month_ago, currency_code="EUR", rate=0.50)
        ExchangeRateFactory(rate_date=today, currency_code="EUR", rate=0.75)

        mock_get_exchange_rate.side_effect = lambda _, date: {
            two_months_ago.month: 0.25,
            one_month_ago.month: 0.50,
            today.month: 0.75,
        }[date.month]

        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.currency_id = "EUR"
        opp_access.opportunity.save()

        payment_unit = PaymentUnitFactory()

        self._create_completed_work(opp_access, two_months_ago, payment_unit, exchange_rate=0.25, n=2)
        self._create_completed_work(opp_access, one_month_ago, payment_unit, exchange_rate=0.50, n=1)
        self._create_completed_work(opp_access, today, payment_unit, exchange_rate=0.75, n=1)

        items = get_uninvoiced_visit_items(opp_access.opportunity)
        assert len(items) == 3

        for item in items:
            expected_number_approved = 0
            expected_payment_unit = None
            expected_exchange_rate = 0.0

            if item["month"].month == two_months_ago.month:
                expected_number_approved = 2
                expected_payment_unit = payment_unit
                expected_exchange_rate = 0.25
            elif item["month"].month == one_month_ago.month:
                expected_number_approved = 1
                expected_payment_unit = payment_unit
                expected_exchange_rate = 0.50
            elif item["month"].month == today.month:
                expected_number_approved = 1
                expected_payment_unit = payment_unit
                expected_exchange_rate = 0.75
            else:
                pytest.fail("Unexpected month in invoice items")

            total_local_amount = expected_number_approved * expected_payment_unit.amount
            assert item["number_approved"] == expected_number_approved
            assert item["amount_per_unit"] == expected_payment_unit.amount
            assert item["total_amount_local"] == total_local_amount
            assert item["exchange_rate"] == expected_exchange_rate
            assert float(item["total_amount_usd"]) == round(total_local_amount / expected_exchange_rate, 2)

    @patch("commcare_connect.opportunity.visit_import.get_exchange_rate")
    def test_different_pu_items_across_multiple_months(self, mock_get_exchange_rate):
        two_months_ago = datetime.now() - relativedelta(months=2)
        one_month_ago = datetime.now() - relativedelta(months=1)
        today = datetime.now()

        rate_two_months_ago = 0.25
        rate_one_month_ago = 0.50
        rate_today = 0.75

        ExchangeRateFactory(rate_date=two_months_ago, currency_code="EUR", rate=rate_two_months_ago)
        ExchangeRateFactory(rate_date=one_month_ago, currency_code="EUR", rate=rate_one_month_ago)
        ExchangeRateFactory(rate_date=today, currency_code="EUR", rate=rate_today)

        mock_get_exchange_rate.side_effect = lambda _, date: {
            two_months_ago.month: rate_two_months_ago,
            one_month_ago.month: rate_one_month_ago,
            today.month: rate_today,
        }[date.month]

        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.currency_id = "EUR"
        opp_access.opportunity.save(update_fields=["currency"])

        payment_unit1 = PaymentUnitFactory()
        payment_unit2 = PaymentUnitFactory()

        self._create_completed_work(opp_access, two_months_ago, payment_unit1, exchange_rate=rate_two_months_ago, n=2)
        self._create_completed_work(opp_access, two_months_ago, payment_unit2, exchange_rate=rate_two_months_ago, n=1)

        self._create_completed_work(opp_access, one_month_ago, payment_unit1, exchange_rate=rate_one_month_ago, n=1)

        self._create_completed_work(opp_access, today, payment_unit1, exchange_rate=rate_today, n=1)
        self._create_completed_work(opp_access, today, payment_unit2, exchange_rate=rate_today, n=1)

        items = get_uninvoiced_visit_items(opp_access.opportunity)
        assert len(items) == 5

        for item in items:
            expected_number_approved = 0
            expected_payment_unit = None
            expected_exchange_rate = 0.0

            if item["month"].month == two_months_ago.month:
                expected_exchange_rate = rate_two_months_ago
                if item["payment_unit_name"] == payment_unit1.name:
                    expected_number_approved = 2
                    expected_payment_unit = payment_unit1
                elif item["payment_unit_name"] == payment_unit2.name:
                    expected_number_approved = 1
                    expected_payment_unit = payment_unit2
                else:
                    pytest.fail("Unexpected payment unit name")

            elif item["month"].month == one_month_ago.month:
                expected_exchange_rate = rate_one_month_ago

                if item["payment_unit_name"] == payment_unit1.name:
                    expected_number_approved = 1
                    expected_payment_unit = payment_unit1
                else:
                    pytest.fail("Unexpected payment unit name")

            elif item["month"].month == today.month:
                expected_exchange_rate = rate_today

                if item["payment_unit_name"] == payment_unit1.name:
                    expected_number_approved = 1
                    expected_payment_unit = payment_unit1
                elif item["payment_unit_name"] == payment_unit2.name:
                    expected_number_approved = 1
                    expected_payment_unit = payment_unit2
                else:
                    pytest.fail("Unexpected payment unit name")
            else:
                pytest.fail("Unexpected month in invoice items")

            total_local_amount = expected_number_approved * expected_payment_unit.amount
            assert item["number_approved"] == expected_number_approved
            assert item["amount_per_unit"] == expected_payment_unit.amount
            assert item["total_amount_local"] == total_local_amount
            assert item["exchange_rate"] == expected_exchange_rate
            assert float(item["total_amount_usd"]) == round(total_local_amount / expected_exchange_rate, 2)

    def _create_completed_work(self, opp_access, status_modified_date, payment_unit, exchange_rate=1.0, n=1):
        for _ in range(n):
            cw = CompletedWorkFactory(
                status=CompletedWorkStatus.approved,
                opportunity_access=opp_access,
                payment_unit=payment_unit,
            )
            cw.status_modified_date = status_modified_date
            cw.saved_payment_accrued = cw.payment_unit.amount
            cw.saved_payment_accrued_usd = cw.saved_payment_accrued / exchange_rate
            cw.save()


@pytest.mark.django_db
class TestUpdateStatus:
    def test_completed_work_not_updated_to_approved_when_missing_required_visit(self):
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=optional_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.pending

    def test_completed_work_updated_to_approved_with_all_visits_approved(self):
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_completed_work_updated_to_approved_with_all_required_visits_approved(self):
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )
        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=optional_deliver_unit_1,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )
        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=optional_deliver_unit_2,
            completed_work=completed_work,
            status=VisitValidationStatus.pending,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_completed_work_not_updated_to_approved_with_not_all_required_visits_approved(self):
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit_1,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )
        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit_2,
            completed_work=completed_work,
            status=VisitValidationStatus.pending,
        )
        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=optional_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_managed_opp_completed_work_not_updated_to_approved_without_agreement(self):
        managed_opp = ManagedOpportunityFactory()
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity = managed_opp
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()
        opp_access.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.pending,
        )
        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=optional_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_managed_opp_completed_work_updated_to_approved_with_agreement(self):
        managed_opp = ManagedOpportunityFactory()
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity = managed_opp
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=optional_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 100

    def test_managed_opp_completed_work_updated_to_approved_with_same_unit_over_limit(self):
        managed_opp = ManagedOpportunityFactory()
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity = managed_opp
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )
        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.over_limit,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 2
        assert completed_work.saved_payment_accrued == 100

    def test_managed_opp_completed_work_not_updated_to_approved_with_no_optional_visit(self):
        managed_opp = ManagedOpportunityFactory()
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity = managed_opp
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=required_deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
            review_status=VisitReviewStatus.agree,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 0
        assert completed_work.saved_payment_accrued == 0

    def test_completed_work_updated_to_rejected_when_any_visit_rejected(self):
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.rejected,
            reason="Invalid data",
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.rejected
        assert completed_work.reason == "Invalid data"
        assert completed_work.saved_approved_count == 0
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0

    def test_payment_calculations_when_completed_work_approved(self):
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.auto_approve_payments = True
        opp_access.opportunity.save()

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
            UserVisitFactory(
                opportunity=opp_access.opportunity,
                user=opp_access.user,
                opportunity_access=opp_access,
                deliver_unit=deliver_unit,
                completed_work=completed_work,
                status=VisitValidationStatus.approved,
            )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.approved
        assert completed_work.saved_approved_count == 3
        assert completed_work.saved_completed_count == 3
        assert completed_work.saved_payment_accrued == 450
        assert completed_work.saved_payment_accrued_usd > 0

    def test_no_status_update_when_auto_approve_disabled(self):
        opp_access = OpportunityAccessFactory()
        opp_access.opportunity.auto_approve_payments = False
        opp_access.opportunity.save()

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

        UserVisitFactory(
            opportunity=opp_access.opportunity,
            user=opp_access.user,
            opportunity_access=opp_access,
            deliver_unit=deliver_unit,
            completed_work=completed_work,
            status=VisitValidationStatus.approved,
        )

        completed_works = CompletedWork.objects.filter(id=completed_work.id).select_related("payment_unit")
        update_status(completed_works, opp_access, compute_payment=True)
        completed_work.refresh_from_db()

        assert completed_work.status == CompletedWorkStatus.pending
        assert completed_work.saved_approved_count == 1
        assert completed_work.saved_completed_count == 1
        assert completed_work.saved_payment_accrued == 0
