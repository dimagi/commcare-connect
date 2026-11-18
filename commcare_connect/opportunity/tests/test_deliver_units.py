import importlib

import pytest
from django.db import IntegrityError, connection, transaction

from commcare_connect.form_receiver.processor import get_or_create_deliver_unit
from commcare_connect.opportunity.models import DeliverUnit
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    DeliverUnitFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.opportunity.views import assignable_deliver_units, link_deliver_units

backfill_migration = importlib.import_module("commcare_connect.opportunity.migrations.0155_deliverunit_opportunity")


@pytest.fixture
def reused_app(opportunity):
    """``opportunity`` and a newer opportunity that reuses its deliver app."""
    previous_payment_unit = PaymentUnitFactory(opportunity=opportunity)
    previous_unit = DeliverUnitFactory(app=opportunity.deliver_app, slug="visit", payment_unit=previous_payment_unit)
    new_opportunity = OpportunityFactory(deliver_app=opportunity.deliver_app)
    return previous_unit, new_opportunity


@pytest.mark.django_db
class TestGetOrCreateForOpportunity:
    def test_returns_the_opportunitys_unit(self, opportunity):
        unit = DeliverUnitFactory(app=opportunity.deliver_app, slug="visit")

        assert DeliverUnit.objects.get_or_create_for_opportunity(opportunity, "visit") == (unit, False)

    def test_reused_app_gets_a_fresh_unit(self, reused_app):
        previous_unit, new_opportunity = reused_app

        unit, created = DeliverUnit.objects.get_or_create_for_opportunity(
            new_opportunity, "visit", defaults={"name": "Visit"}
        )

        assert created
        assert unit != previous_unit
        assert (unit.opportunity, unit.app, unit.payment_unit) == (new_opportunity, new_opportunity.deliver_app, None)
        previous_unit.refresh_from_db()
        assert previous_unit.opportunity_id != new_opportunity.id

    def test_adopts_a_unit_with_no_opportunity(self, opportunity):
        legacy = DeliverUnitFactory(app=opportunity.deliver_app, slug="visit", opportunity=None)

        unit, created = DeliverUnit.objects.get_or_create_for_opportunity(opportunity, "visit")

        assert (unit, created) == (legacy, False)
        assert unit.opportunity == opportunity

    def test_ignores_units_of_a_previous_deliver_app(self, opportunity):
        old_app_unit = DeliverUnitFactory(app=CommCareAppFactory(), slug="visit", opportunity=opportunity)

        unit, created = DeliverUnit.objects.get_or_create_for_opportunity(opportunity, "visit")

        assert created
        assert unit != old_app_unit


@pytest.mark.django_db
class TestUniquePerOpportunity:
    def test_rejects_a_second_unit_for_the_same_slug(self, opportunity):
        DeliverUnitFactory(app=opportunity.deliver_app, slug="visit")

        with pytest.raises(IntegrityError), transaction.atomic():
            DeliverUnitFactory(app=opportunity.deliver_app, slug="visit", opportunity=opportunity)

    def test_allows_the_same_slug_on_units_with_no_opportunity(self):
        app = CommCareAppFactory()

        DeliverUnitFactory.create_batch(2, app=app, slug="visit", opportunity=None)

        assert DeliverUnit.objects.filter(app=app, slug="visit").count() == 2


@pytest.mark.django_db
def test_form_for_reused_app_does_not_use_previous_opportunitys_unit(reused_app):
    previous_unit, new_opportunity = reused_app

    unit = get_or_create_deliver_unit(new_opportunity, {"@id": "visit", "name": "Visit"})

    assert unit != previous_unit
    assert unit.payment_unit is None


@pytest.mark.django_db
def test_assignable_deliver_units(opportunity):
    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    other_payment_unit = PaymentUnitFactory(opportunity=opportunity)
    unattached = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=None)
    own = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=other_payment_unit)
    on_legacy_payment_unit = DeliverUnitFactory(
        app=opportunity.deliver_app, payment_unit=PaymentUnitFactory(opportunity=OpportunityFactory())
    )
    DeliverUnitFactory(app=opportunity.deliver_app, opportunity=OpportunityFactory(), payment_unit=None)

    assert set(assignable_deliver_units(opportunity)) == {unattached, on_legacy_payment_unit}
    assert set(assignable_deliver_units(opportunity, payment_unit)) == {unattached, own, on_legacy_payment_unit}


@pytest.mark.django_db
def test_link_deliver_units_leaves_other_opportunities_units_alone(reused_app):
    previous_unit, new_opportunity = reused_app
    previous_payment_unit = previous_unit.payment_unit
    payment_unit = PaymentUnitFactory(opportunity=new_opportunity)

    link_deliver_units(payment_unit, [str(previous_unit.id)], [])

    previous_unit.refresh_from_db()
    assert previous_unit.payment_unit == previous_payment_unit


@pytest.mark.django_db
class TestBackfill:
    def _backfill(self, *units):
        DeliverUnit.objects.filter(id__in=[unit.id for unit in units]).update(opportunity=None)
        with connection.cursor() as cursor:
            cursor.execute(backfill_migration.BACKFILL_SQL)
        return [DeliverUnit.objects.get(id=unit.id).opportunity for unit in units]

    def test_app_used_by_one_opportunity(self, opportunity):
        unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=None)

        assert self._backfill(unit) == [opportunity]

    def test_shared_app_goes_to_payment_unit_opportunity(self, opportunity):
        unit = DeliverUnitFactory(
            app=opportunity.deliver_app, payment_unit=PaymentUnitFactory(opportunity=opportunity)
        )
        OpportunityFactory(deliver_app=opportunity.deliver_app)

        assert self._backfill(unit) == [opportunity]

    @pytest.mark.parametrize("payment_unit_on_app", [False, None])
    def test_shared_app_otherwise_goes_to_latest_opportunity(self, opportunity, payment_unit_on_app):
        # False: the payment unit's opportunity no longer uses the app. None: no payment unit.
        payment_unit = PaymentUnitFactory() if payment_unit_on_app is False else None
        unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
        latest = OpportunityFactory(deliver_app=opportunity.deliver_app)

        assert self._backfill(unit) == [latest]

    def test_app_used_by_no_opportunity_stays_null(self):
        unit = DeliverUnitFactory(payment_unit=None)

        assert self._backfill(unit) == [None]
