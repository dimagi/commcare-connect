import random
import re
from datetime import timedelta
from decimal import Decimal
from itertools import chain

import pytest
from django.utils import timezone
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
    VisitReviewStatus,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CatchmentAreaFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    PaymentFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.tests.helpers import validate_saved_fields
from commcare_connect.opportunity.utils.completed_work import update_work_payment_date
from commcare_connect.opportunity.visit_import import (
    ImportException,
    VisitData,
    _bulk_update_catchments,
    _bulk_update_completed_work_status,
    _bulk_update_payments,
    _bulk_update_visit_status,
    get_data_by_visit_id,
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
        _validate_saved_fields(access)


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
    _validate_saved_fields(access)


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
        _validate_saved_fields(access)


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
    _validate_saved_fields(access)


@pytest.mark.parametrize(
    "headers,rows,expected",
    [
        (
            ["visit id", "status", "rejected reason"],
            [[123, "approved", ""], ["abc", "rejected", ""]],
            {"123": VisitData(VisitValidationStatus.approved), "abc": VisitData(VisitValidationStatus.rejected)},
        ),
        (
            ["extra col", "visit id", "status", "rejected reason"],
            [["x", "1", "approved", ""], ["y", "2", "rejected", ""]],
            {"1": VisitData(VisitValidationStatus.approved), "2": VisitData(VisitValidationStatus.rejected)},
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
            get_data_by_visit_id(dataset)
    else:
        actual = get_data_by_visit_id(dataset)
        assert dict(actual) == expected


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


def prepare_opportunity_payment_test_data(opportunity):
    user = MobileUserFactory()
    access = OpportunityAccessFactory(opportunity=opportunity, user=user, accepted=True)

    payment_units = [
        PaymentUnitFactory(opportunity=opportunity, amount=100),
        PaymentUnitFactory(opportunity=opportunity, amount=150),
        PaymentUnitFactory(opportunity=opportunity, amount=200),
    ]

    for payment_unit in payment_units:
        DeliverUnitFactory.create_batch(2, payment_unit=payment_unit, app=opportunity.deliver_app, optional=False)

    completed_works = []
    for payment_unit in payment_units:
        completed_work = CompletedWorkFactory(
            opportunity_access=access,
            payment_unit=payment_unit,
            status=CompletedWorkStatus.approved.value,
            payment_date=None,
        )
        completed_works.append(completed_work)
        for deliver_unit in payment_unit.deliver_units.all():
            UserVisitFactory(
                opportunity=opportunity,
                user=user,
                deliver_unit=deliver_unit,
                status=VisitValidationStatus.approved.value,
                opportunity_access=access,
                completed_work=completed_work,
            )
    return user, access, payment_units, completed_works


@pytest.mark.django_db
def test_update_work_payment_date_partially(opportunity):
    user, access, payment_units, completed_works = prepare_opportunity_payment_test_data(opportunity)

    payment_dates = [
        timezone.now() - timedelta(5),
        timezone.now() - timedelta(3),
        timezone.now() - timedelta(1),
    ]
    for date in payment_dates:
        PaymentFactory(opportunity_access=access, amount=100, date_paid=date)

    update_work_payment_date(access)

    assert (
        get_assignable_completed_work_count(access)
        == CompletedWork.objects.filter(opportunity_access=access, payment_date__isnull=False).count()
    )


@pytest.mark.django_db
def test_update_work_payment_date_fully(opportunity):
    user, access, payment_units, completed_works = prepare_opportunity_payment_test_data(opportunity)

    payment_dates = [
        timezone.now() - timedelta(days=5),
        timezone.now() - timedelta(days=3),
        timezone.now() - timedelta(days=1),
    ]
    amounts = [100, 150, 200]
    for date, amount in zip(payment_dates, amounts):
        PaymentFactory(opportunity_access=access, amount=amount, date_paid=date)

    update_work_payment_date(access)

    assert CompletedWork.objects.filter(
        opportunity_access=access, payment_date__isnull=False
    ).count() == get_assignable_completed_work_count(access)


@pytest.mark.django_db
def test_update_work_payment_date_with_precise_dates(opportunity):
    user = MobileUserFactory()
    access = OpportunityAccessFactory(opportunity=opportunity, user=user, accepted=True)

    payment_units = [
        PaymentUnitFactory(opportunity=opportunity, amount=5),
        PaymentUnitFactory(opportunity=opportunity, amount=5),
    ]

    for payment_unit in payment_units:
        DeliverUnitFactory.create_batch(2, payment_unit=payment_unit, app=opportunity.deliver_app, optional=False)

    completed_work_1 = CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=payment_units[0],
        status=CompletedWorkStatus.approved.value,
        payment_date=None,
    )

    completed_work_2 = CompletedWorkFactory(
        opportunity_access=access,
        payment_unit=payment_units[1],
        status=CompletedWorkStatus.approved.value,
        payment_date=None,
    )

    create_user_visits_for_completed_work(opportunity, user, access, payment_units[0], completed_work_1)
    create_user_visits_for_completed_work(opportunity, user, access, payment_units[1], completed_work_2)

    now = timezone.now()

    payment_1 = PaymentFactory(opportunity_access=access, amount=7)
    payment_2 = PaymentFactory(opportunity_access=access, amount=3)

    payment_1.date_paid = now - timedelta(3)
    payment_2.date_paid = now - timedelta(1)
    payment_1.save()
    payment_2.save()

    payment_1.refresh_from_db()
    payment_2.refresh_from_db()

    update_work_payment_date(access)

    completed_work_1.refresh_from_db()
    completed_work_2.refresh_from_db()

    assert completed_work_1.payment_date == payment_1.date_paid

    assert completed_work_2.payment_date == payment_2.date_paid


def create_user_visits_for_completed_work(opportunity, user, access, payment_unit, completed_work):
    for deliver_unit in payment_unit.deliver_units.all():
        UserVisitFactory(
            opportunity=opportunity,
            user=user,
            deliver_unit=deliver_unit,
            status=VisitValidationStatus.approved.value,
            opportunity_access=access,
            completed_work=completed_work,
        )


def get_assignable_completed_work_count(access: OpportunityAccess) -> int:
    total_available_amount = sum(payment.amount for payment in Payment.objects.filter(opportunity_access=access))
    total_assigned_count = 0
    completed_works = CompletedWork.objects.filter(opportunity_access=access)
    for completed_work in completed_works:
        if total_available_amount >= completed_work.payment_accrued:
            total_available_amount -= completed_work.payment_accrued
            total_assigned_count += 1

    return total_assigned_count


@pytest.mark.parametrize("opportunity", [{"opp_options": {"managed": True}}], indirect=True)
@pytest.mark.parametrize("visit_status", [VisitValidationStatus.approved, VisitValidationStatus.rejected])
def test_network_manager_flagged_visit_review_status(mobile_user: User, opportunity: Opportunity, visit_status):
    assert opportunity.managed
    access = OpportunityAccess.objects.get(user=mobile_user, opportunity=opportunity)
    visits = UserVisitFactory.create_batch(
        5, opportunity=opportunity, status=VisitValidationStatus.pending, user=mobile_user, opportunity_access=access
    )
    dataset = Dataset(headers=["visit id", "status", "rejected reason", "justification"])
    dataset.extend([[visit.xform_id, visit_status.value, "", "justification"] for visit in visits])
    before_update = now()
    import_status = _bulk_update_visit_status(opportunity, dataset)
    after_update = now()
    assert not import_status.missing_visits
    updated_visits = UserVisit.objects.filter(opportunity=opportunity)
    for visit in updated_visits:
        assert visit.status == visit_status
        assert visit.status_modified_date is not None
        assert before_update <= visit.status_modified_date <= after_update
        if visit.status == VisitValidationStatus.approved:
            assert before_update <= visit.review_created_on <= after_update
            assert visit.review_status == VisitReviewStatus.pending
            assert visit.justification == "justification"


@pytest.mark.parametrize("opportunity", [{"opp_options": {"managed": True}}], indirect=True)
@pytest.mark.parametrize(
    "review_status, cw_status",
    [
        (VisitReviewStatus.pending, CompletedWorkStatus.pending),
        (VisitReviewStatus.agree, CompletedWorkStatus.approved),
        (VisitReviewStatus.disagree, CompletedWorkStatus.pending),
    ],
)
def test_review_completed_work_status(
    mobile_user_with_connect_link: User, opportunity: Opportunity, review_status, cw_status
):
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=opportunity.paymentunit_set.first())
    access = OpportunityAccess.objects.get(user=mobile_user_with_connect_link, opportunity=opportunity)
    UserVisitFactory.create_batch(
        2,
        opportunity_access=access,
        status=VisitValidationStatus.approved,
        review_status=review_status,
        review_created_on=now(),
        completed_work__status=CompletedWorkStatus.pending,
        completed_work__opportunity_access=access,
        completed_work__payment_unit=opportunity.paymentunit_set.first(),
        deliver_unit=deliver_unit,
    )
    assert access.payment_accrued == 0
    update_payment_accrued(opportunity, {mobile_user_with_connect_link.id})
    completed_works = CompletedWork.objects.filter(opportunity_access=access)
    payment_accrued = 0
    for cw in completed_works:
        assert cw.status == cw_status
        if cw.status == CompletedWorkStatus.approved:
            payment_accrued += cw.payment_accrued
    access.refresh_from_db()
    assert access.payment_accrued == payment_accrued
    _validate_saved_fields(access)


def _validate_saved_fields(opportunity_access: OpportunityAccess):
    for completed_work in opportunity_access.completedwork_set.all():
        validate_saved_fields(completed_work)
