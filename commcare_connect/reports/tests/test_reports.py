from datetime import UTC, date, datetime, timedelta
from unittest import mock

import pytest
from dateutil.relativedelta import relativedelta
from django.http import HttpResponse
from django.urls import clear_url_caches, path, reverse
from django.utils import timezone
from django.utils.timezone import now
from django.views import View

from commcare_connect.conftest import MobileUserFactory
from commcare_connect.connect_id_client.main import fetch_user_counts
from commcare_connect.opportunity.helpers import get_payment_report_data
from commcare_connect.opportunity.models import CompletedWorkStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    OpportunityFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.reports import urls as reports_urls
from commcare_connect.reports.decorators import KPIReportMixin, kpi_report_access_required
from commcare_connect.reports.helpers import get_table_data_for_year_month
from commcare_connect.users.tests.factories import LLOEntityFactory, OrganizationFactory, UserFactory
from commcare_connect.utils.datetime import get_month_series
from commcare_connect.utils.test_utils import check_basic_permissions


def get_month_range_start_end(months=1):
    """
    Returns specified months before past before current month.
    """
    today = now().date()
    end = date(today.year, today.month, 1)
    start = end - relativedelta(months=months)
    return start, end


def _create_kpi_test_data(users, timestamp, **access_kwargs):
    """Create completed work, visits, payments, and invoices for KPI report tests."""
    for i, user in enumerate(users):
        access = OpportunityAccessFactory(
            user=user,
            opportunity__is_test=False,
            opportunity__delivery_type__name=f"Delivery Type {(i % 2) + 1}",
            **access_kwargs,
        )
        inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100)
        cw = CompletedWorkFactory(
            status_modified_date=timestamp,
            opportunity_access=access,
            status=CompletedWorkStatus.approved,
            saved_approved_count=1,
            saved_payment_accrued_usd=i * 100,
            saved_org_payment_accrued_usd=100,
            payment_date=timestamp + timedelta(minutes=30),
            invoice=inv,
        )
        UserVisitFactory(
            date_created=timestamp - timedelta(i * 10),
            opportunity_access=access,
            completed_work=cw,
            status=VisitValidationStatus.approved,
        )
        PaymentFactory(opportunity_access=access, date_paid=timestamp, amount_usd=i * 50, confirmed=True)
        PaymentFactory(invoice=inv, date_paid=timestamp, amount_usd=50)
        other_inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100, service_delivery=False)
        CompletedWorkFactory(
            status_modified_date=timestamp,
            opportunity_access=access,
            status=CompletedWorkStatus.approved,
            saved_approved_count=0,
            saved_payment_accrued_usd=0,
            saved_org_payment_accrued_usd=100,
            payment_date=timestamp + timedelta(minutes=30),
            invoice=other_inv,
        )
        PaymentFactory(invoice=other_inv, date_paid=timestamp, amount_usd=100)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "from_date, to_date",
    [
        (None, None),
        (get_month_range_start_end(1)),
        (get_month_range_start_end(13)),
    ],
)
def test_get_table_data_for_year_month(from_date, to_date, httpx_mock):
    users = MobileUserFactory.create_batch(10)
    months = get_month_series(
        from_date or datetime(now().year, now().month, 1).date(),
        to_date or datetime(now().year, now().month, 1).date(),
    )
    connectid_user_counts = {}
    for i, month in enumerate(months):
        today = datetime.combine(month, datetime.min.time(), tzinfo=UTC)
        connectid_user_counts[today.strftime("%Y-%m")] = i
        with mock.patch.object(timezone, "now", return_value=today):
            _create_kpi_test_data(users, today)

    fetch_user_counts.clear()
    httpx_mock.add_response(
        method="GET",
        json={"total_users": connectid_user_counts, "non_invited_users": connectid_user_counts},
    )
    data = get_table_data_for_year_month(from_date=from_date, to_date=to_date)

    assert len(data)
    total_connectid_user_count = 0
    for i, row in enumerate(data):
        assert date(row["month_group"].year, row["month_group"].month, 1) in months
        total_connectid_user_count += connectid_user_counts.get(row["month_group"].strftime("%Y-%m"))
        assert row["connectid_users"] == total_connectid_user_count
        assert row["activated_connect_users"] == 10
        assert row["users"] == 10
        assert row["services"] == 10
        assert row["avg_time_to_payment"] == 50
        assert 0 <= row["max_time_to_payment"] <= 90
        assert row["flw_amount_earned"] == 4500
        assert row["flw_amount_paid"] == 2250
        assert row["intervention_funding_deployed"] == 5500
        assert row["organization_funding_deployed"] == 1000
        assert row["avg_top_earned_flws"] == 900


@pytest.mark.django_db
def test_get_payment_report_data_with_multiple_payment_units():
    unit_a = PaymentUnitFactory(name="Unit A", amount=100)
    unit_b = PaymentUnitFactory(name="Unit B", amount=200)

    amount_a_user = 150
    amount_a_nm = 300

    amount_b_user = 250
    amount_b_nm = 500

    opportunity = OpportunityFactory()
    access_a = OpportunityAccessFactory(opportunity=opportunity)
    access_b = OpportunityAccessFactory(opportunity=opportunity)

    # Create 2 CompletedWork for Unit A
    CompletedWorkFactory.create_batch(
        2,
        opportunity_access=access_a,
        payment_unit=unit_a,
        status=CompletedWorkStatus.approved,
        saved_payment_accrued=amount_a_user,
        saved_org_payment_accrued=amount_a_nm,
    )

    # Create 3 CompletedWork for Unit B
    CompletedWorkFactory.create_batch(
        3,
        opportunity_access=access_b,
        payment_unit=unit_b,
        status=CompletedWorkStatus.approved,
        saved_payment_accrued=amount_b_user,
        saved_org_payment_accrued=amount_b_nm,
    )

    report_data, total_user_payment, total_nm_payment = get_payment_report_data(opportunity)

    assert len(report_data) == 2

    report_dict = {r.payment_unit: r for r in report_data}

    unit_a_data = report_dict["Unit A"]
    assert unit_a_data.approved == 2
    assert unit_a_data.user_payment_accrued == 2 * amount_a_user
    assert unit_a_data.nm_payment_accrued == 2 * amount_a_nm

    unit_b_data = report_dict["Unit B"]
    assert unit_b_data.approved == 3
    assert unit_b_data.user_payment_accrued == 3 * amount_b_user
    assert unit_b_data.nm_payment_accrued == 3 * amount_b_nm

    expected_user_total = 2 * amount_a_user + 3 * amount_b_user
    expected_nm_total = 2 * amount_a_nm + 3 * amount_b_nm

    assert total_user_payment == expected_user_total
    assert total_nm_payment == expected_nm_total


@pytest.mark.django_db
@pytest.mark.parametrize(
    "delivery_type",
    [(None), ("delivery_1"), ("delivery_2")],
)
def test_get_table_data_for_year_month_by_delivery_type(delivery_type, httpx_mock):
    now = datetime.now(UTC)
    delivery_type_slugs = ["delivery_1", "delivery_2"]
    for slug in delivery_type_slugs:
        users = MobileUserFactory.create_batch(5)
        for i, user in enumerate(users):
            access = OpportunityAccessFactory(
                user=user,
                opportunity__is_test=False,
                opportunity__delivery_type__slug=slug,
                opportunity__delivery_type__name=slug,
            )
            inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100)
            cw = CompletedWorkFactory(
                status_modified_date=now,
                opportunity_access=access,
                status=CompletedWorkStatus.approved,
                saved_approved_count=1,
                saved_payment_accrued_usd=i * 100,
                saved_org_payment_accrued_usd=100,
                payment_date=now + timedelta(minutes=1),
                invoice=inv,
            )
            UserVisitFactory(
                date_created=now - timedelta(i * 10),
                opportunity_access=access,
                completed_work=cw,
                status=VisitValidationStatus.approved,
            )
            PaymentFactory(opportunity_access=access, date_paid=now, amount_usd=i * 50, confirmed=True)
            PaymentFactory(invoice=inv, date_paid=now, amount_usd=50)
    fetch_user_counts.clear()
    httpx_mock.add_response(
        method="GET",
        json={},
    )
    data = get_table_data_for_year_month(delivery_type=delivery_type)

    assert len(data)
    for row in data:
        if (
            row["month_group"].month != now.month
            or row["month_group"].year != now.year
            or row["delivery_type_name"] == "All"
        ):
            assert row["activated_connect_users"] == 10
            continue
        assert row["delivery_type_name"] in delivery_type_slugs
        assert row["users"] == 5
        assert row["services"] == 5
        assert row["avg_time_to_payment"] == 25
        assert row["max_time_to_payment"] == 40
        assert row["flw_amount_earned"] == 1000
        assert row["flw_amount_paid"] == 500
        assert row["intervention_funding_deployed"] == 1500
        assert row["avg_top_earned_flws"] == 400
        assert row["activated_connect_users"] == 5


@pytest.mark.django_db
@pytest.mark.parametrize("opp_country, filter_country", [("ETH", "KEN"), ("ETH", "ETH")])
def test_get_table_data_for_year_month_by_country(opp_country, filter_country, httpx_mock):
    now = datetime.now(UTC)
    users = MobileUserFactory.create_batch(10)
    _create_kpi_test_data(users, now, opportunity__country_id=opp_country)
    fetch_user_counts.clear()
    httpx_mock.add_response(
        method="GET",
        json={},
    )
    data = get_table_data_for_year_month(country=filter_country)
    assert len(data)

    if opp_country == filter_country:
        for row in data:
            if row["month_group"].month != now.month or row["month_group"].year != now.year:
                continue

            assert row["users"] == 10
            assert row["services"] == 10
            assert row["avg_time_to_payment"] == 50
            assert 0 <= row["max_time_to_payment"] <= 90
            assert row["flw_amount_earned"] == 4500
            assert row["flw_amount_paid"] == 2250
            assert row["intervention_funding_deployed"] == 5500
            assert row["organization_funding_deployed"] == 1000
            assert row["avg_top_earned_flws"] == 900
    else:
        for row in data:
            if row["month_group"].month != now.month or row["month_group"].year != now.year:
                continue

            assert row["users"] == 0
            assert row["services"] == 0
            assert row["avg_time_to_payment"] == 0
            assert row["max_time_to_payment"] == 0
            assert row["flw_amount_earned"] == 0
            assert row["flw_amount_paid"] == 0
            assert row["intervention_funding_deployed"] == 0
            assert row["organization_funding_deployed"] == 0
            assert row["avg_top_earned_flws"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("filter_same_llo", [True, False])
def test_get_table_data_for_year_month_by_llo(filter_same_llo, httpx_mock):
    now = datetime.now(UTC)
    llo_entity = LLOEntityFactory()
    other_llo_entity = LLOEntityFactory()
    org = OrganizationFactory(llo_entity=llo_entity)

    users = MobileUserFactory.create_batch(10)
    _create_kpi_test_data(users, now, opportunity__organization=org)
    fetch_user_counts.clear()
    httpx_mock.add_response(
        method="GET",
        json={},
    )

    filter_llo = llo_entity if filter_same_llo else other_llo_entity
    data = get_table_data_for_year_month(llo=filter_llo)
    assert len(data)

    for row in data:
        if filter_same_llo:
            assert row["users"] == 10
            assert row["services"] == 10
            assert row["flw_amount_earned"] == 4500
            assert row["flw_amount_paid"] == 2250
            assert row["intervention_funding_deployed"] == 5500
            assert row["organization_funding_deployed"] == 1000
        else:
            assert row["users"] == 0
            assert row["services"] == 0
            assert row["flw_amount_earned"] == 0
            assert row["flw_amount_paid"] == 0
            assert row["intervention_funding_deployed"] == 0
            assert row["organization_funding_deployed"] == 0


class TestKPIReportPermission:
    @pytest.fixture(autouse=True)
    def setup(self, db):
        clear_url_caches()

        # Dummy function-based view
        @kpi_report_access_required
        def dummy_view(request):
            return HttpResponse("OK")

        # Dummy class-based view
        class DummyKPIReportView(KPIReportMixin, View):
            def get(self, request, *args, **kwargs):
                return HttpResponse("OK")

        # Add dummy views to URLs
        reports_urls.urlpatterns.extend(
            [
                path("dummy_fbv/", dummy_view, name="dummy_fbv"),
                path("dummy_cbv/", DummyKPIReportView.as_view(), name="dummy_cbv"),
            ]
        )

    @pytest.mark.parametrize("url_name", ["dummy_fbv", "dummy_cbv"])
    def test_permissions(self, url_name):
        url = reverse(f"reports:{url_name}")
        check_basic_permissions(
            UserFactory(),
            url,
            "kpi_report_access",
        )
