from datetime import datetime
from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta

from commcare_connect.opportunity.models import CompletedWorkStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    ExchangeRateFactory,
    OpportunityAccessFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
)
from commcare_connect.opportunity.utils.completed_work import get_uninvoiced_visit_items


@pytest.mark.django_db
class TestUninvoicedVisitItems:
    def test_items_without_prior_invoice(self):
        opp_access = OpportunityAccessFactory()
        CompletedWorkFactory(status=CompletedWorkStatus.pending, opportunity_access=opp_access)
        assert len(get_uninvoiced_visit_items(opp_access.opportunity)) == 0

        completed_work = CompletedWorkFactory(status=CompletedWorkStatus.approved, opportunity_access=opp_access)
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
        assert len(get_uninvoiced_visit_items(opp_access.opportunity)) == 0

        completed_work = CompletedWorkFactory(
            status=CompletedWorkStatus.approved,
            opportunity_access=opp_access,
        )
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
        opp_access.opportunity.currency = "EUR"
        opp_access.opportunity.save()

        payment_unit = PaymentUnitFactory()

        self._create_completed_work(opp_access, two_months_ago, payment_unit, n=2)
        self._create_completed_work(opp_access, one_month_ago, payment_unit, n=1)
        self._create_completed_work(opp_access, today, payment_unit, n=1)

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
            assert item["total_amount_usd"] == total_local_amount / expected_exchange_rate

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
        opp_access.opportunity.currency = "EUR"
        opp_access.opportunity.save()

        payment_unit1 = PaymentUnitFactory()
        payment_unit2 = PaymentUnitFactory()

        self._create_completed_work(opp_access, two_months_ago, payment_unit1, n=2)
        self._create_completed_work(opp_access, two_months_ago, payment_unit2, n=1)

        self._create_completed_work(opp_access, one_month_ago, payment_unit1, n=1)

        self._create_completed_work(opp_access, today, payment_unit1, n=1)
        self._create_completed_work(opp_access, today, payment_unit2, n=1)

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
            assert item["total_amount_usd"] == total_local_amount / expected_exchange_rate

    def _create_completed_work(self, opp_access, date_created, payment_unit, n=1):
        for _ in range(n):
            cw = CompletedWorkFactory(
                status=CompletedWorkStatus.approved,
                opportunity_access=opp_access,
                payment_unit=payment_unit,
            )
            cw.date_created = date_created
            cw.save()
