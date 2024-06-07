import random
import re
from itertools import chain

import pytest
from tablib import Dataset

from commcare_connect.conftest import MobileUserFactory
from commcare_connect.opportunity.models import (
    CompletedWorkStatus,
    Opportunity,
    OpportunityAccess,
    Payment,
    UserVisit,
    VisitValidationStatus,
)
from commcare_connect.opportunity.tests.factories import (
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityAccessFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.visit_import import (
    ImportException,
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

    import_status = _bulk_update_visit_status(opportunity, dataset)
    assert not import_status.missing_visits
    after_status = set(UserVisit.objects.filter(opportunity=opportunity).values_list("status", flat=True))
    assert after_status == {VisitValidationStatus.approved.value}


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
