import math
from datetime import UTC, datetime, timedelta

import pytest
from factory.faker import Faker

from commcare_connect.conftest import MobileUserFactory
from commcare_connect.opportunity.models import CompletedWorkStatus, Opportunity, VisitValidationStatus
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    PaymentFactory,
    PaymentInvoiceFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.reports.helpers import get_table_data_for_year_month
from commcare_connect.reports.views import _results_to_geojson, get_table_data_for_quarter


@pytest.mark.django_db
def test_delivery_stats(opportunity: Opportunity):
    payment_units = PaymentUnitFactory.create_batch(2, opportunity=opportunity)
    mobile_users = MobileUserFactory.create_batch(5)
    for payment_unit in payment_units:
        DeliverUnitFactory.create_batch(2, payment_unit=payment_unit, app=opportunity.deliver_app, optional=False)
    access_objects = []
    for mobile_user in mobile_users:
        access = OpportunityAccessFactory(user=mobile_user, opportunity=opportunity, accepted=True)
        access_objects.append(access)
        for payment_unit in payment_units:
            completed_work = CompletedWorkFactory(
                opportunity_access=access,
                payment_unit=payment_unit,
                status=CompletedWorkStatus.approved.value,
            )
            for deliver_unit in payment_unit.deliver_units.all():
                UserVisitFactory(
                    opportunity=opportunity,
                    user=mobile_user,
                    deliver_unit=deliver_unit,
                    status=VisitValidationStatus.approved.value,
                    opportunity_access=access,
                    completed_work=completed_work,
                    visit_date=Faker("date_time_this_month", tzinfo=UTC),
                )

    quarter = math.ceil(datetime.utcnow().month / 12 * 4)

    # delivery_type filter not applied
    all_data = get_table_data_for_quarter((datetime.now().year, quarter), None)
    assert all_data[0]["users"] == 5
    assert all_data[0]["services"] == 10
    assert all_data[0]["beneficiaries"] == 10

    # test delivery_type filter
    filtered_data = get_table_data_for_quarter((datetime.now().year, quarter), opportunity.delivery_type.slug)
    assert filtered_data == all_data

    # unknown delivery-type should have no data
    unknown_delivery_type_data = get_table_data_for_quarter((datetime.now().year, quarter), "unknown")
    assert unknown_delivery_type_data[0]["users"] == 0
    assert unknown_delivery_type_data[0]["services"] == 0
    assert unknown_delivery_type_data[0]["beneficiaries"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "year, month, delivery_type",
    [
        (None, None, None),
        (datetime.now().year, None, None),
        (datetime.now().year, datetime.now().month, None),
    ],
)
def test_get_table_data_for_year_month(year, month, delivery_type):
    now = datetime.now(UTC)
    users = MobileUserFactory.create_batch(10)
    for i, user in enumerate(users):
        access = OpportunityAccessFactory(
            user=user,
            opportunity__is_test=False,
            opportunity__delivery_type__name=f"Delivery Type {(i % 2) + 1}",
        )
        cw = CompletedWorkFactory(
            status_modified_date=now - timedelta(3),
            opportunity_access=access,
            status=CompletedWorkStatus.approved,
            saved_approved_count=1,
            saved_payment_accrued_usd=i * 100,
            saved_org_payment_accrued_usd=100,
            payment_date=now,
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
    data = get_table_data_for_year_month(year, month, delivery_type)

    assert len(data)
    for row in data:
        assert row["month"][1] == datetime.now().year
        assert row["users"] == 10
        assert row["services"] == 10
        assert row["avg_time_to_payment"] == 45
        assert 0 <= row["max_time_to_payment"] <= 90
        assert row["flw_amount_earned"] == 4500
        assert row["flw_amount_paid"] == 4500
        assert row["nm_amount_earned"] == 5500
        assert row["nm_amount_paid"] == 1000
        assert row["avg_top_paid_flws"] == 1700


@pytest.mark.django_db
@pytest.mark.parametrize(
    "year, month, delivery_type",
    [
        (None, None, None),
        (datetime.now().year, datetime.now().month, "Delivery Type A"),
        (datetime.now().year, datetime.now().month, "Delivery Type B"),
    ],
)
def test_get_table_data_for_year_month_by_delivery_type(year, month, delivery_type):
    now = datetime.now(UTC)
    delivery_type_names = ["Delivery Type 1", "Delivery Type 2"]
    for d_name in delivery_type_names:
        users = MobileUserFactory.create_batch(5)
        for i, user in enumerate(users):
            access = OpportunityAccessFactory(
                user=user,
                opportunity__is_test=False,
                opportunity__delivery_type__name=d_name,
            )
            cw = CompletedWorkFactory(
                status_modified_date=now - timedelta(3),
                opportunity_access=access,
                status=CompletedWorkStatus.approved,
                saved_approved_count=1,
                saved_payment_accrued_usd=i * 100,
                saved_org_payment_accrued_usd=100,
                payment_date=now,
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
    data = get_table_data_for_year_month(year, month, delivery_type, group_by_delivery_type=True)

    assert len(data)
    for row in data:
        assert row["delivery_type"] in delivery_type_names
        assert row["month"][1] == datetime.now().year
        assert row["users"] == 5
        assert row["services"] == 5
        assert row["avg_time_to_payment"] == 20
        assert row["max_time_to_payment"] == 40
        assert row["flw_amount_earned"] == 1000
        assert row["flw_amount_paid"] == 1000
        assert row["nm_amount_earned"] == 1500
        assert row["nm_amount_paid"] == 500
        assert row["avg_top_paid_flws"] == 400


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
