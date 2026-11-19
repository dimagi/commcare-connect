import importlib

import pytest
from django.db import IntegrityError, connection, transaction

from commcare_connect.form_receiver.processor import get_or_create_deliver_unit
from commcare_connect.opportunity.forms import DeliverUnitFlagsForm, FormJsonValidationRulesForm
from commcare_connect.opportunity.models import DeliverUnit, DeliverUnitFlagRules, FormJsonValidationRules
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    DeliverUnitFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.opportunity.views import link_deliver_units

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

    def test_adopts_one_unit_with_no_opportunity(self, opportunity):
        # Several can match, e.g. when two opportunities that shared the app were deleted
        first, second = DeliverUnitFactory.create_batch(2, app=opportunity.deliver_app, slug="visit", opportunity=None)

        unit, created = DeliverUnit.objects.get_or_create_for_opportunity(opportunity, "visit")

        assert (unit, created) == (first, False)
        assert unit.opportunity == opportunity
        second.refresh_from_db()
        assert second.opportunity is None

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
def test_assignable(opportunity):
    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    other_payment_unit = PaymentUnitFactory(opportunity=opportunity)
    unattached = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=None)
    own = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=other_payment_unit)
    on_legacy_payment_unit = DeliverUnitFactory(
        app=opportunity.deliver_app, payment_unit=PaymentUnitFactory(opportunity=OpportunityFactory())
    )
    DeliverUnitFactory(app=opportunity.deliver_app, opportunity=OpportunityFactory(), payment_unit=None)

    assert set(DeliverUnit.objects.assignable(opportunity)) == {unattached, on_legacy_payment_unit}
    assert set(DeliverUnit.objects.assignable(opportunity, payment_unit)) == {
        unattached,
        own,
        on_legacy_payment_unit,
    }


@pytest.mark.django_db
class TestRulesOnAnotherOpportunitysUnit:
    """A rule set up before CCCT-3057 can target a unit that the 0155 backfill gave to another opportunity."""

    def test_flag_rule_still_validates(self, opportunity):
        other_opportunitys_unit = DeliverUnitFactory(app=opportunity.deliver_app, opportunity=OpportunityFactory())
        rule = DeliverUnitFlagRules.objects.create(opportunity=opportunity, deliver_unit=other_opportunitys_unit)

        form = DeliverUnitFlagsForm(data={"duration": 5}, instance=rule, opportunity=opportunity)

        assert form.is_valid(), form.errors

    def test_form_json_rule_keeps_its_target(self, opportunity):
        own_unit = DeliverUnitFactory(app=opportunity.deliver_app)
        other_opportunitys_unit = DeliverUnitFactory(app=opportunity.deliver_app, opportunity=OpportunityFactory())
        rule = FormJsonValidationRules.objects.create(
            opportunity=opportunity, name="rule", question_path="/data/q", question_value="yes"
        )
        rule.deliver_unit.set([own_unit, other_opportunitys_unit])

        form = FormJsonValidationRulesForm(instance=rule, opportunity=opportunity)

        assert set(form.fields["deliver_unit"].queryset) == {own_unit, other_opportunitys_unit}


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

    def test_shared_app_unit_with_no_payment_unit_goes_to_running_opportunity(self, opportunity):
        unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=None)
        OpportunityFactory(deliver_app=opportunity.deliver_app, active=False)

        assert self._backfill(unit) == [opportunity]

    def test_duplicate_slugs_give_the_opportunity_to_one_unit(self, opportunity):
        payment_unit = PaymentUnitFactory(opportunity=opportunity)
        # Possible under the old schema, which had no unique constraint
        unconfigured = DeliverUnitFactory(
            app=opportunity.deliver_app, slug="visit", payment_unit=None, opportunity=None
        )
        configured = DeliverUnitFactory(
            app=opportunity.deliver_app, slug="visit", payment_unit=payment_unit, opportunity=None
        )

        assert self._backfill(unconfigured, configured) == [None, opportunity]

    def test_app_used_by_no_opportunity_stays_null(self):
        unit = DeliverUnitFactory(payment_unit=None)

        assert self._backfill(unit) == [None]
