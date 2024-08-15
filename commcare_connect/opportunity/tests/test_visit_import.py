import random
import re
from decimal import Decimal
from itertools import chain

import pytest
from django.utils.timezone import now
from tablib import Dataset

from commcare_connect.conftest import MobileUserFactory
from commcare_connect.opportunity.models import (
    CatchmentArea,
    CompletedWork,
    CompletedWorkStatus,
    Opportunity,
    OpportunityAccess,
    Payment,
    PaymentUnit,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CatchmentAreaFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.visit_import import (
    ImportException,
    _bulk_update_catchments,
    _bulk_update_completed_work_status,
    _bulk_update_payments,
    _bulk_update_visit_status,
    get_status_by_visit_id,
    update_payment_accrued,
)
from commcare_connect.users.models import User


@pytest.mark.django_db
def test_bulk_update_visit_status(opportunity: Opportunity, mobile_user: User):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    visits = UserVisitFactory.create_batch(
        5,
        opportunity=opportunity,
        status=VisitValidationStatus.pending.value,
        user=mobile_user,
        opportunity_access=access,
    )
    dataset = Dataset(headers=["visit id", "status", "rejected reason"])
    dataset.extend([[visit.xform_id, VisitValidationStatus.approved.value, ""] for visit in visits])

    before_update = now()
    import_status = _bulk_update_visit_status(opportunity, dataset)
    after_update = now()

    assert not import_status.missing_visits

    updated_visits = UserVisit.objects.filter(opportunity=opportunity)
    for visit in updated_visits:
        assert visit.status == VisitValidationStatus.approved.value
        assert visit.status_modified_date is not None
        assert before_update <= visit.status_modified_date <= after_update


@pytest.mark.django_db
def test_bulk_update_completed_work_status(opportunity: Opportunity, mobile_user: User):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    payment_unit = PaymentUnit.objects.get(opportunity=opportunity)
    DeliverUnitFactory(payment_unit=payment_unit, app=opportunity.deliver_app, optional=False)

    completed_works = CompletedWorkFactory.create_batch(5, opportunity_access=access, payment_unit=payment_unit)
    dataset = Dataset(headers=["instance id", "payment approval", "rejected reason"])
    dataset.extend([[work.id, CompletedWorkStatus.approved.value, ""] for work in completed_works])

    before_update = now()
    _bulk_update_completed_work_status(opportunity=opportunity, dataset=dataset)
    after_update = now()

    updated_work = CompletedWork.objects.filter(opportunity_access=access)
    for work in updated_work:
        assert work.status == CompletedWorkStatus.approved.value
        assert work.status_modified_date is not None
        assert before_update <= work.status_modified_date <= after_update


@pytest.mark.django_db
def test_bulk_update_reason(opportunity: Opportunity, mobile_user: User):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    visit = UserVisitFactory.create(
        opportunity=opportunity,
        status=VisitValidationStatus.pending.value,
        user=mobile_user,
        opportunity_access=access,
    )
    reason = "bad form"
    dataset = Dataset(headers=["visit id", "status", "rejected reason"])
    dataset.extend([[visit.xform_id, VisitValidationStatus.rejected.value, reason]])
    import_status = _bulk_update_visit_status(opportunity, dataset)
    assert not import_status.missing_visits
    visit.refresh_from_db()
    assert visit.status == VisitValidationStatus.rejected
    assert visit.reason == reason


@pytest.mark.django_db
def test_payment_accrued(opportunity: Opportunity):
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
                )
    update_payment_accrued(opportunity, {mobile_user.id for mobile_user in mobile_users})
    for access in access_objects:
        access.refresh_from_db()
        assert access.payment_accrued == sum(payment_unit.amount for payment_unit in payment_units)


@pytest.mark.django_db
def test_duplicate_payment(opportunity: Opportunity, mobile_user: User):
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    payment_unit = PaymentUnitFactory.create(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(payment_unit=payment_unit, app=opportunity.deliver_app)
    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(payment_unit=payment_unit, app=opportunity.deliver_app, optional=False)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    completed_work = CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=payment_unit,
        status=CompletedWorkStatus.approved.value,
    )
    UserVisitFactory.create_batch(
        2,
        opportunity=opportunity,
        user=mobile_user,
        deliver_unit=deliver_unit,
        status=VisitValidationStatus.approved.value,
        opportunity_access=access,
        completed_work=completed_work,
    )
    update_payment_accrued(opportunity, {mobile_user.id})
    access.refresh_from_db()
    assert access.payment_accrued == payment_unit.amount * 2


@pytest.mark.django_db
def test_payment_accrued_optional_deliver_units(opportunity: Opportunity):
    payment_units = PaymentUnitFactory.create_batch(2, opportunity=opportunity)
    access_objects = OpportunityAccessFactory.create_batch(5, opportunity=opportunity, accepted=True)
    for payment_unit in payment_units:
        DeliverUnitFactory.create_batch(2, payment_unit=payment_unit, app=opportunity.deliver_app, optional=False)
        DeliverUnitFactory.create_batch(2, payment_unit=payment_unit, app=opportunity.deliver_app, optional=True)
    for access in access_objects:
        for payment_unit in payment_units:
            completed_work = CompletedWorkFactory(
                opportunity_access=access,
                payment_unit=payment_unit,
                status=CompletedWorkStatus.approved.value,
            )
            for deliver_unit in payment_unit.deliver_units.filter(optional=False):
                UserVisitFactory(
                    opportunity=opportunity,
                    user=access.user,
                    deliver_unit=deliver_unit,
                    status=VisitValidationStatus.approved.value,
                    completed_work=completed_work,
                )
            optional_deliver_unit = random.choice(payment_unit.deliver_units.filter(optional=True))
            UserVisitFactory(
                opportunity=opportunity,
                user=access.user,
                deliver_unit=optional_deliver_unit,
                status=VisitValidationStatus.approved.value,
                completed_work=completed_work,
            )
    update_payment_accrued(opportunity, {access.user.id for access in access_objects})
    for access in access_objects:
        access.refresh_from_db()
        assert access.payment_accrued == sum(payment_unit.amount for payment_unit in payment_units)


@pytest.mark.django_db
def test_payment_accrued_asymmetric_optional_deliver_units(opportunity: Opportunity, mobile_user: User):
    payment_unit = PaymentUnitFactory.create(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(payment_unit=payment_unit, app=opportunity.deliver_app, optional=False)
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    completed_work = CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=payment_unit,
        status=CompletedWorkStatus.approved.value,
    )
    UserVisitFactory.create_batch(
        2,
        opportunity=opportunity,
        user=mobile_user,
        deliver_unit=deliver_unit,
        status=VisitValidationStatus.approved.value,
        completed_work=completed_work,
    )
    optional_deliver_unit = DeliverUnitFactory(payment_unit=payment_unit, app=opportunity.deliver_app, optional=True)
    UserVisitFactory.create_batch(
        1,
        opportunity=opportunity,
        user=mobile_user,
        deliver_unit=optional_deliver_unit,
        status=VisitValidationStatus.approved.value,
        completed_work=completed_work,
    )
    update_payment_accrued(opportunity, {mobile_user.id})
    access.refresh_from_db()
    assert access.payment_accrued == payment_unit.amount * 1
    optional_deliver_unit_2 = DeliverUnitFactory(payment_unit=payment_unit, app=opportunity.deliver_app, optional=True)
    UserVisitFactory.create_batch(
        1,
        opportunity=opportunity,
        user=mobile_user,
        deliver_unit=optional_deliver_unit_2,
        status=VisitValidationStatus.approved.value,
        completed_work=completed_work,
    )
    update_payment_accrued(opportunity, {mobile_user.id})
    access.refresh_from_db()
    assert access.payment_accrued == payment_unit.amount * 2


@pytest.mark.parametrize(
    "headers,rows,expected",
    [
        (
            ["visit id", "status", "rejected reason"],
            [[123, "approved", ""], ["abc", "rejected", ""]],
            {"123": VisitValidationStatus.approved.value, "abc": VisitValidationStatus.rejected.value},
        ),
        (
            ["extra col", "visit id", "status", "rejected reason"],
            [["x", "1", "approved", ""], ["y", "2", "rejected", ""]],
            {"1": VisitValidationStatus.approved.value, "2": VisitValidationStatus.rejected.value},
        ),
        (["a", "status"], [], ImportException("Missing required column(s): 'visit id'")),
        (["visit id", "a"], [], ImportException("Missing required column(s): 'status'")),
    ],
)
def test_get_status_by_visit_id(headers, rows, expected):
    dataset = Dataset(headers=headers)
    dataset.extend(rows)

    if isinstance(expected, ImportException):
        with pytest.raises(ImportException, match=re.escape(expected.message)):
            get_status_by_visit_id(dataset)
    else:
        actual, _ = get_status_by_visit_id(dataset)
        assert actual == expected


@pytest.mark.django_db
def test_bulk_update_payments(opportunity: Opportunity):
    mobile_user_seen = MobileUserFactory.create_batch(5)
    mobile_user_missing = MobileUserFactory.create_batch(5)
    access_objects = []
    for mobile_user in mobile_user_seen:
        access_objects.append(OpportunityAccessFactory(opportunity=opportunity, user=mobile_user))
    dataset = Dataset(headers=["Username", "Phone Number", "Name", "Payment Amount"])
    for mobile_user in chain(mobile_user_seen, mobile_user_missing):
        dataset.append((mobile_user.username, mobile_user.phone_number, mobile_user.name, 50))

    payment_import_status = _bulk_update_payments(opportunity, dataset)
    assert payment_import_status.seen_users == {user.username for user in mobile_user_seen}
    assert payment_import_status.missing_users == {user.username for user in mobile_user_missing}
    assert Payment.objects.filter(opportunity_access__opportunity=opportunity).count() == 5
    for access in access_objects:
        payment = Payment.objects.get(opportunity_access=access)
        assert payment.amount == 50


@pytest.fixture
def dataset():
    return Dataset(headers=["latitude", "longitude", "area name", "radius", "active", "username", "site code"])


@pytest.fixture
def new_catchments():
    return [
        ["40.7128", "-74.0060", "New York", "5000", "yes", "", "new york"],
        ["20.7128", "-84.0060", "London", "4500", "No", "", "london"],
    ]


@pytest.fixture
def old_catchments(opportunity):
    mobile_users = MobileUserFactory.create_batch(2)
    catchments = set()
    for user in mobile_users:
        access = OpportunityAccessFactory.create(opportunity=opportunity, user=user)
        catchments.add(
            CatchmentAreaFactory.create(
                opportunity=opportunity, opportunity_access=access, latitude=28, longitude=45, radius=100, active=True
            )
        )
    return catchments


@pytest.mark.django_db
def test_bulk_update_catchments_missing_headers(opportunity):
    required_headers = ["latitude", "longitude", "area name", "radius", "active", "site code"]
    sample_data = ["40.7128", "-74.0060", "New York", "100", "yes", "new york"]  # Sample data for a complete row

    for missing_header in required_headers:
        incomplete_headers = [header for header in required_headers if header != missing_header]
        dataset = Dataset(headers=incomplete_headers)

        # Add a row with data, excluding the value for the missing header
        row_data = [value for header, value in zip(required_headers, sample_data) if header != missing_header]
        dataset.append(row_data)

        with pytest.raises(ImportException) as excinfo:
            _bulk_update_catchments(opportunity, dataset)

        expected_message = f"Missing required column(s): '{missing_header}'"
        assert str(excinfo.value) == expected_message, f"Expected: {expected_message}, but got {str(excinfo.value)}"


@pytest.mark.django_db
def test_bulk_update_catchments(opportunity, dataset, new_catchments, old_catchments):
    latitude_change = Decimal("10.5")
    longitude_change = Decimal("10.5")
    radius_change = 10
    name_change = "updated"

    for catchment in old_catchments:
        dataset.append(
            [
                str(catchment.latitude + latitude_change),
                str(catchment.longitude + longitude_change),
                f"{name_change} {catchment.name}",
                str(catchment.radius + radius_change),
                "yes",
                catchment.opportunity_access.user.username,
                catchment.site_code,
            ]
        )

    dataset.extend(new_catchments)

    import_status = _bulk_update_catchments(opportunity, dataset)

    assert import_status.seen_catchments == {
        str(catchment.id) for catchment in old_catchments
    }, "Mismatch in updated catchments"
    assert import_status.new_catchments == len(new_catchments), "Incorrect number of new catchments"

    for catchment in old_catchments:
        updated_catchment = CatchmentArea.objects.get(site_code=catchment.site_code)
        assert (
            updated_catchment.name == f"{name_change} {catchment.name}"
        ), f"Name not updated correctly for catchment {catchment.id}"
        assert (
            updated_catchment.radius == catchment.radius + radius_change
        ), f"Radius not updated correctly for catchment {catchment.id}"
        assert (
            updated_catchment.latitude == catchment.latitude + latitude_change
        ), f"Latitude not updated correctly for catchment {catchment.id}"
        assert (
            updated_catchment.longitude == catchment.longitude + longitude_change
        ), f"Longitude not updated correctly for catchment {catchment.id}"
        assert updated_catchment.active, f"Active status not updated correctly for catchment {catchment.id}"
