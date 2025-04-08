from datetime import UTC, date, datetime, timedelta
from unittest import mock

import pytest
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from django.utils.timezone import now

from commcare_connect.conftest import MobileUserFactory
from commcare_connect.connect_id_client.main import fetch_user_counts
from commcare_connect.opportunity.models import CompletedWorkStatus, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    OpportunityAccessFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    UserVisitFactory,
)
from commcare_connect.reports.helpers import get_table_data_for_year_month
from commcare_connect.reports.views import _results_to_geojson
from commcare_connect.utils.datetime import get_month_series


def get_month_range_start_end(months=1):
    """
    Returns specified months before past before current month.
    """
    today = now().date()
    end = date(today.year, today.month, 1)
    start = end - relativedelta(months=months)
    return start, end


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
            for i, user in enumerate(users):
                access = OpportunityAccessFactory(
                    user=user,
                    opportunity__is_test=False,
                    opportunity__delivery_type__name=f"Delivery Type {(i % 2) + 1}",
                )
                cw = CompletedWorkFactory(
                    status_modified_date=today,
                    opportunity_access=access,
                    status=CompletedWorkStatus.approved,
                    saved_approved_count=1,
                    saved_payment_accrued_usd=i * 100,
                    saved_org_payment_accrued_usd=100,
                    payment_date=today + timedelta(minutes=30),
                )
                UserVisitFactory(
                    visit_date=today - timedelta(i * 10),
                    opportunity_access=access,
                    completed_work=cw,
                    status=VisitValidationStatus.approved,
                )
                PaymentFactory(opportunity_access=access, date_paid=today, amount_usd=i * 100, confirmed=True)
                inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100)
                PaymentFactory(invoice=inv, date_paid=today, amount_usd=100)
                other_inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100, service_delivery=False)
                PaymentFactory(invoice=other_inv, date_paid=today, amount_usd=100)

    fetch_user_counts.clear()
    httpx_mock.add_response(
        method="GET",
        json=connectid_user_counts,
    )
    data = get_table_data_for_year_month(from_date=from_date, to_date=to_date)

    assert len(data)
    total_connectid_user_count = 0
    for i, row in enumerate(data):
        assert date(row["month_group"].year, row["month_group"].month, 1) in months
        total_connectid_user_count += connectid_user_counts.get(row["month_group"].strftime("%Y-%m"))
        assert row["connectid_users"] == total_connectid_user_count
        assert row["total_eligible_users"] == (i + 1) * 10
        assert row["users"] == 10
        assert row["services"] == 10
        assert row["avg_time_to_payment"] == 50
        assert 0 <= row["max_time_to_payment"] <= 90
        assert row["flw_amount_earned"] == 4500
        assert row["flw_amount_paid"] == 4500
        assert row["nm_amount_earned"] == 5500
        assert row["nm_amount_paid"] == 1000
        assert row["nm_other_amount_paid"] == 1000
        assert row["avg_top_paid_flws"] == 900


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
            cw = CompletedWorkFactory(
                status_modified_date=now,
                opportunity_access=access,
                status=CompletedWorkStatus.approved,
                saved_approved_count=1,
                saved_payment_accrued_usd=i * 100,
                saved_org_payment_accrued_usd=100,
                payment_date=now + timedelta(minutes=1),
            )
            UserVisitFactory(
                visit_date=now - timedelta(i * 10),
                opportunity_access=access,
                completed_work=cw,
                status=VisitValidationStatus.approved,
            )
            PaymentFactory(opportunity_access=access, date_paid=now, amount_usd=i * 100, confirmed=True)
            inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100)
            PaymentFactory(invoice=inv, date_paid=now, amount_usd=100)
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
            continue
        assert row["delivery_type_name"] in delivery_type_slugs
        assert row["users"] == 5
        assert row["services"] == 5
        assert row["avg_time_to_payment"] == 25
        assert row["max_time_to_payment"] == 40
        assert row["flw_amount_earned"] == 1000
        assert row["flw_amount_paid"] == 1000
        assert row["nm_amount_earned"] == 1500
        assert row["nm_amount_paid"] == 500
        assert row["avg_top_paid_flws"] == 400


@pytest.mark.django_db
@pytest.mark.parametrize("opp_currency, filter_currency", [("ETB", "KES"), ("ETB", "ETB")])
def test_get_table_data_for_year_month_by_country_currency(opp_currency, filter_currency, httpx_mock):
    now = datetime.now(UTC)
    users = MobileUserFactory.create_batch(10)
    for i, user in enumerate(users):
        access = OpportunityAccessFactory(
            user=user,
            opportunity__is_test=False,
            opportunity__delivery_type__name=f"Delivery Type {(i % 2) + 1}",
            opportunity__currency=opp_currency,
        )
        cw = CompletedWorkFactory(
            status_modified_date=now,
            opportunity_access=access,
            status=CompletedWorkStatus.approved,
            saved_approved_count=1,
            saved_payment_accrued_usd=i * 100,
            saved_org_payment_accrued_usd=100,
            payment_date=now + timedelta(minutes=1),
        )
        UserVisitFactory(
            visit_date=now - timedelta(i * 10),
            opportunity_access=access,
            completed_work=cw,
            status=VisitValidationStatus.approved,
        )
        PaymentFactory(opportunity_access=access, date_paid=now, amount_usd=i * 100, confirmed=True)
        inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100)
        PaymentFactory(invoice=inv, date_paid=now, amount_usd=100)
        other_inv = PaymentInvoiceFactory(opportunity=access.opportunity, amount=100, service_delivery=False)
        PaymentFactory(invoice=other_inv, date_paid=now, amount_usd=100)
    fetch_user_counts.clear()
    httpx_mock.add_response(
        method="GET",
        json={},
    )
    data = get_table_data_for_year_month(country_currency=filter_currency)
    if opp_currency == filter_currency:
        assert len(data)
        for row in data:
            if row["month_group"].month != now.month or row["month_group"].year != now.year:
                continue

            assert row["users"] == 10
            assert row["services"] == 10
            assert row["avg_time_to_payment"] == 50
            assert 0 <= row["max_time_to_payment"] <= 90
            assert row["flw_amount_earned"] == 4500
            assert row["flw_amount_paid"] == 4500
            assert row["nm_amount_earned"] == 5500
            assert row["nm_amount_paid"] == 1000
            assert row["nm_other_amount_paid"] == 1000
            assert row["avg_top_paid_flws"] == 900


def test_results_to_geojson():
    class MockQuerySet:
        def __init__(self, results):
            self.results = results

        def all(self):
            return self.results

    # Test input
    results = MockQuerySet(
        [
            {"location_str": "20.456 10.123 0 0", "status": "approved", "other_field": "value1"},
            {"location_str": "40.012 30.789", "status": "rejected", "other_field": "value2"},
            {"location_str": "invalid location", "status": "unknown", "other_field": "value3"},
            {"location_str": "bad location", "status": "unknown", "other_field": "value4"},
            {
                "location_str": None,
                "status": "approved",
                "other_field": "value5",
            },  # Case where lat/lon are not present
            {  # Case where lat/lon are null
                "location_str": None,
                "status": "rejected",
                "other_field": "value5",
            },
        ]
    )

    # Call the function
    geojson = _results_to_geojson(results)

    # Assertions
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 2  # Only the first two results should be included

    # Check the first feature
    feature1 = geojson["features"][0]
    assert feature1["type"] == "Feature"
    assert feature1["geometry"]["type"] == "Point"
    assert feature1["geometry"]["coordinates"] == [10.123, 20.456]
    assert feature1["properties"]["status"] == "approved"
    assert feature1["properties"]["other_field"] == "value1"
    assert feature1["properties"]["color"] == "#4ade80"

    # Check the second feature
    feature2 = geojson["features"][1]
    assert feature2["type"] == "Feature"
    assert feature2["geometry"]["type"] == "Point"
    assert feature2["geometry"]["coordinates"] == [30.789, 40.012]
    assert feature2["properties"]["status"] == "rejected"
    assert feature2["properties"]["other_field"] == "value2"
    assert feature2["properties"]["color"] == "#f87171"

    # Check that the other cases are not included
    assert all(f["properties"]["other_field"] not in ["value3", "value4", "value5"] for f in geojson["features"])
