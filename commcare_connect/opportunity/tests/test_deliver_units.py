import importlib

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, connection, transaction

from commcare_connect.form_receiver.processor import get_or_create_deliver_unit
from commcare_connect.opportunity.models import DeliverUnit, DeliverUnitFlagRules, FormJsonValidationRules
from commcare_connect.opportunity.tests.factories import (
    CommCareAppFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.views import link_deliver_units

split_migration = importlib.import_module("commcare_connect.opportunity.migrations.0157_split_shared_deliver_units")


@pytest.fixture
def nullable_deliver_unit_opportunity(db):
    """Allow units with no opportunity, as before 0158, for the length of the test's transaction."""
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE opportunity_deliverunit ALTER COLUMN opportunity_id DROP NOT NULL")


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
    DeliverUnitFactory(app=opportunity.deliver_app, opportunity=OpportunityFactory(), payment_unit=None)

    assert set(DeliverUnit.objects.assignable(opportunity)) == {unattached}
    assert set(DeliverUnit.objects.assignable(opportunity, payment_unit)) == {unattached, own}


@pytest.mark.django_db
def test_link_deliver_units_leaves_other_opportunities_units_alone(reused_app):
    previous_unit, new_opportunity = reused_app
    previous_payment_unit = previous_unit.payment_unit
    payment_unit = PaymentUnitFactory(opportunity=new_opportunity)

    link_deliver_units(payment_unit, [str(previous_unit.id)], [])

    previous_unit.refresh_from_db()
    assert previous_unit.payment_unit == previous_payment_unit


@pytest.mark.usefixtures("nullable_deliver_unit_opportunity")
class TestSplitSharedUnits:
    """Migration 0157: each opportunity that uses a unit it doesn't own gets its own copy."""

    @pytest.fixture
    def shared(self, opportunity):
        """``opportunity``'s unit, on an app that ``other`` reused before units belonged to an opportunity."""
        unit = DeliverUnitFactory(
            opportunity=opportunity,
            app=opportunity.deliver_app,
            slug="visit",
            display_name="Visit",
            optional=True,
            payment_unit=PaymentUnitFactory(opportunity=opportunity),
        )
        other = OpportunityFactory(deliver_app=opportunity.deliver_app, active=False)
        return unit, other

    def _split(self):
        split_migration.split_shared_deliver_units(django_apps, None)

    def _copy(self, unit, opportunity):
        return DeliverUnit.objects.get(opportunity=opportunity, app=unit.app, slug=unit.slug)

    def test_visits_move_to_a_copy_with_the_payment_unit_their_work_used(self, shared):
        unit, other = shared
        other_payment_unit = PaymentUnitFactory(opportunity=other)
        own_visit = UserVisitFactory(opportunity=unit.opportunity, deliver_unit=unit)
        other_visit = UserVisitFactory(
            opportunity=other,
            deliver_unit=unit,
            completed_work=CompletedWorkFactory(payment_unit=other_payment_unit),
        )

        self._split()

        copy = self._copy(unit, other)
        assert (copy.name, copy.display_name, copy.optional) == (unit.name, unit.display_name, unit.optional)
        assert copy.payment_unit == other_payment_unit
        other_visit.refresh_from_db()
        own_visit.refresh_from_db()
        assert (other_visit.deliver_unit, own_visit.deliver_unit) == (copy, unit)

    def test_overwritten_payment_unit_goes_back_to_its_opportunity(self, shared):
        # The bug: the unit ended up on another opportunity's payment unit
        unit, other = shared
        other_payment_unit = PaymentUnitFactory(opportunity=other)
        DeliverUnit.objects.filter(pk=unit.pk).update(payment_unit=other_payment_unit)

        self._split()

        assert self._copy(unit, other).payment_unit == other_payment_unit
        unit.refresh_from_db()
        assert (unit.payment_unit, unit.optional) == (None, False)

    @pytest.mark.parametrize("copy_has_rule", [False, True])
    def test_flag_rules_move_to_the_copy(self, shared, copy_has_rule):
        unit, other = shared
        rule = DeliverUnitFlagRules.objects.create(opportunity=other, deliver_unit=unit, duration=1)
        if copy_has_rule:
            existing_copy = DeliverUnitFactory(opportunity=other, app=unit.app, slug=unit.slug, payment_unit=None)
            DeliverUnitFlagRules.objects.create(opportunity=other, deliver_unit=existing_copy, duration=2)

        self._split()

        rules = DeliverUnitFlagRules.objects.filter(opportunity=other)
        assert [(r.deliver_unit, r.duration) for r in rules] == [(self._copy(unit, other), 2 if copy_has_rule else 1)]
        assert copy_has_rule or rules.get().pk == rule.pk

    def test_form_json_rules_move_to_the_copy(self, shared):
        unit, other = shared
        rule = FormJsonValidationRules.objects.create(
            opportunity=other, name="rule", question_path="/data/q", question_value="yes"
        )
        rule.deliver_unit.set([unit])

        self._split()

        assert list(rule.deliver_unit.all()) == [self._copy(unit, other)]

    def test_existing_unit_of_the_opportunity_is_reused(self, shared):
        # The opportunity reused the app after 0156, so it already has its own unit for the slug
        unit, other = shared
        existing_copy = DeliverUnitFactory(opportunity=other, app=unit.app, slug=unit.slug, payment_unit=None)
        other_payment_unit = PaymentUnitFactory(opportunity=other)
        visit = UserVisitFactory(
            opportunity=other, deliver_unit=unit, completed_work=CompletedWorkFactory(payment_unit=other_payment_unit)
        )

        self._split()

        visit.refresh_from_db()
        existing_copy.refresh_from_db()
        assert visit.deliver_unit == existing_copy
        assert existing_copy.payment_unit == other_payment_unit

    def test_units_with_no_opportunity_are_split_then_deleted(self, opportunity):
        used = DeliverUnitFactory(opportunity=None, app=opportunity.deliver_app, slug="visit", payment_unit=None)
        visit = UserVisitFactory(opportunity=opportunity, deliver_unit=used)
        unused = DeliverUnitFactory(opportunity=None, app=opportunity.deliver_app, slug="other", payment_unit=None)

        self._split()

        visit.refresh_from_db()
        assert visit.deliver_unit == self._copy(used, opportunity)
        assert not DeliverUnit.objects.filter(pk__in=[used.pk, unused.pk]).exists()

    def test_unit_used_only_by_its_owner_is_unchanged(self, shared):
        unit, _ = shared
        UserVisitFactory(opportunity=unit.opportunity, deliver_unit=unit)

        self._split()

        assert list(DeliverUnit.objects.filter(app=unit.app)) == [unit]
        unit.refresh_from_db()
        assert unit.payment_unit.opportunity == unit.opportunity
