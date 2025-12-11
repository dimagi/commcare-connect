import importlib

import pytest
from django.apps import apps as django_apps

from commcare_connect.opportunity.models import Opportunity, OpportunityClaimLimit, UserVisit
from commcare_connect.opportunity.tests.factories import (
    CompletedModuleFactory,
    CompletedWorkFactory,
    DeliverUnitFactory,
    LearnModuleFactory,
    OpportunityAccessFactory,
    OpportunityClaimFactory,
    OpportunityClaimLimitFactory,
    OpportunityFactory,
    PaymentUnitFactory,
    UserVisitFactory,
)
from commcare_connect.opportunity.visit_import import update_payment_accrued
from commcare_connect.users.models import User
from commcare_connect.users.tests.factories import MobileUserFactory
from commcare_connect.utils.flags import Flags


@pytest.mark.django_db
def test_learn_progress(opportunity: Opportunity):
    learn_modules = LearnModuleFactory.create_batch(2, app=opportunity.learn_app)
    access_1, access_2 = OpportunityAccessFactory.create_batch(2, opportunity=opportunity)
    for learn_module in learn_modules:
        CompletedModuleFactory(module=learn_module, opportunity_access=access_1)
    assert access_1.learn_progress == 100
    assert access_2.learn_progress == 0


@pytest.mark.django_db
@pytest.mark.parametrize("opportunity", [{}, {"opp_options": {"managed": True}}], indirect=True)
def test_opportunity_stats(opportunity: Opportunity, user: User):
    payment_unit_sub = PaymentUnitFactory.create(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    payment_unit1 = PaymentUnitFactory.create(
        opportunity=opportunity,
        max_total=100,
        max_daily=10,
        amount=3,
        parent_payment_unit=payment_unit_sub,
    )
    payment_unit2 = PaymentUnitFactory.create(
        opportunity=opportunity, max_total=100, max_daily=10, amount=5, parent_payment_unit=None
    )
    assert set(list(opportunity.paymentunit_set.values_list("id", flat=True))) == {
        payment_unit1.id,
        payment_unit2.id,
        payment_unit_sub.id,
    }
    payment_units = [payment_unit_sub, payment_unit1, payment_unit2]
    budget_per_user = sum(pu.max_total * pu.amount for pu in payment_units)
    org_pay = 0
    if opportunity.managed:
        org_pay = opportunity.managedopportunity.org_pay_per_visit
        budget_per_user += sum(pu.max_total * org_pay for pu in payment_units)
    opportunity.total_budget = budget_per_user * 3

    payment_units = [payment_unit1, payment_unit2, payment_unit_sub]
    assert opportunity.budget_per_user == sum([p.amount * p.max_total for p in payment_units])
    assert opportunity.number_of_users == 3
    assert opportunity.allotted_visits == sum([pu.max_total for pu in payment_units]) * opportunity.number_of_users
    assert opportunity.max_visits_per_user == sum([pu.max_total for pu in payment_units])
    assert opportunity.daily_max_visits_per_user == sum([pu.max_daily for pu in payment_units])
    assert opportunity.budget_per_visit == sum([pu.amount for pu in payment_units])

    access = OpportunityAccessFactory(user=user, opportunity=opportunity)
    claim = OpportunityClaimFactory(opportunity_access=access)

    ocl1 = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit1)
    ocl2 = OpportunityClaimLimitFactory(opportunity_claim=claim, payment_unit=payment_unit2)

    assert opportunity.claimed_budget == (ocl1.max_visits * (payment_unit1.amount + org_pay)) + (
        ocl2.max_visits * (payment_unit2.amount + org_pay)
    )
    assert opportunity.remaining_budget == opportunity.total_budget - opportunity.claimed_budget


@pytest.mark.django_db
def test_claim_limits(opportunity: Opportunity):
    payment_unit_sub = PaymentUnitFactory(opportunity=opportunity, parent_payment_unit=None)
    payment_units = PaymentUnitFactory.create_batch(2, opportunity=opportunity, parent_payment_unit=None) + [
        payment_unit_sub
    ]
    payment_unit_sub.parent_payment_unit = payment_units[0]
    budget_per_user = sum([p.max_total * p.amount for p in payment_units])
    # budget not enough for more than 2 users
    opportunity.total_budget = budget_per_user * 1.5
    mobile_users = MobileUserFactory.create_batch(3)
    for mobile_user in mobile_users:
        access = OpportunityAccessFactory(user=mobile_user, opportunity=opportunity, accepted=True)
        claim = OpportunityClaimFactory(opportunity_access=access)
        OpportunityClaimLimit.create_claim_limits(opportunity, claim)

    assert opportunity.claimed_budget <= int(opportunity.total_budget)
    assert opportunity.claimed_visits <= int(opportunity.allotted_visits)
    assert opportunity.remaining_budget < payment_units[0].amount + payment_units[1].amount

    def limit_count(user):
        return OpportunityClaimLimit.objects.filter(opportunity_claim__opportunity_access__user=user).count()

    # enough for 1st user
    assert limit_count(mobile_users[0]) == 3
    # partially enough for 2nd user, depending on paymentunit.amount
    assert limit_count(mobile_users[1]) in [2, 3]
    # Not enough for 3rd user at all
    assert limit_count(mobile_users[2]) == 0


@pytest.mark.django_db
def test_access_visit_count(opportunity: Opportunity):
    access = OpportunityAccessFactory(opportunity=opportunity)
    assert access.visit_count == 0

    payment_unit = PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = DeliverUnitFactory(app=opportunity.deliver_app, payment_unit=payment_unit)
    completed_work = CompletedWorkFactory(payment_unit=payment_unit, opportunity_access=access)
    UserVisitFactory(
        completed_work=completed_work, deliver_unit=deliver_unit, user=access.user, opportunity=access.opportunity
    )
    update_payment_accrued(opportunity, [access.user])
    assert access.visit_count == 1


@pytest.mark.django_db
def test_populate_currency_and_country_fk():
    migration_module = importlib.import_module(
        "commcare_connect.opportunity.migrations.0088_currency_country_opportunity_currency_fk"
    )
    populate_currency_and_country_fk = migration_module.populate_currency_and_country_fk

    OpportunityModel = django_apps.get_model("opportunity", "Opportunity")
    CurrencyModel = django_apps.get_model("opportunity", "Currency")
    CountryModel = django_apps.get_model("opportunity", "Country")

    # Ensure currency / country rows exist (loaded via migration)
    single_country_currency = "KES"  # Kenya only
    multi_country_currency = "USD"  # Multiple ISO countries share USD
    valid_currency_only = "INR"

    assert CurrencyModel.objects.filter(code=valid_currency_only).exists()
    assert CountryModel.objects.filter(code="KEN", currency_id=single_country_currency).exists()
    assert CurrencyModel.objects.filter(code=multi_country_currency).exists()

    invalid_currency_code = "ZZZ"
    # Create opportunities that fall in various categories
    opp_valid = OpportunityFactory(currency=valid_currency_only, currency_fk=None, country=None)
    opp_single = OpportunityFactory(currency=single_country_currency, currency_fk=None, country=None)
    opp_multi = OpportunityFactory(currency=multi_country_currency, currency_fk=None, country=None)
    opp_invalid = OpportunityFactory(currency=invalid_currency_code, currency_fk=None, country=None)

    OpportunityModel.objects.filter(id__in=[opp_valid.id, opp_single.id, opp_multi.id, opp_invalid.id]).update(
        currency_fk=None, country=None
    )

    initial_country_count = CountryModel.objects.count()
    initial_currency_count = CurrencyModel.objects.count()

    populate_currency_and_country_fk(django_apps, schema_editor=None)

    for opp in [opp_valid, opp_single, opp_multi, opp_invalid]:
        opp.refresh_from_db()

    # valid currency should map to fk
    assert opp_valid.currency_fk.code == valid_currency_only

    # single-country currency auto-sets the country fk
    assert opp_single.currency_fk.code == single_country_currency
    assert opp_single.country_id == CountryModel.objects.get(code="KEN").pk

    # multi-country currencies should stay NULL for country
    assert opp_multi.currency_fk.code == multi_country_currency
    assert opp_multi.country_id is None

    # invalid currencies create placeholder currencies is_valid=False
    assert CurrencyModel.objects.filter(code=invalid_currency_code, is_valid=False).exists()
    assert opp_invalid.currency_fk.code == invalid_currency_code
    assert opp_invalid.country_id is None
    assert CountryModel.objects.count() == initial_country_count
    assert CurrencyModel.objects.count() == initial_currency_count + 1


@pytest.mark.parametrize(
    "query_flags, expected_keys",
    [
        ([Flags.DUPLICATE.value], {"duplicate"}),
        ([Flags.DUPLICATE.value, Flags.GPS.value], {"duplicate", "gps"}),
        ([], {"duplicate", "gps", "clean"}),
    ],
)
def test_uservisit_queryset_with_any_flags(query_flags, expected_keys):
    visits = {
        "duplicate": UserVisitFactory(
            flagged=True,
            flag_reason={"flags": [(Flags.DUPLICATE.value, "Duplicate submission")]},
        ),
        "gps": UserVisitFactory(
            flagged=True,
            flag_reason={"flags": [(Flags.GPS.value, "GPS missing")]},
        ),
        "clean": UserVisitFactory(
            flagged=False,
            flag_reason=None,
        ),
    }

    queryset = UserVisit.objects.with_any_flags(query_flags)
    result_ids = {visit_id for visit_id in queryset.values_list("id", flat=True)}
    expected_ids = {visits[key].id for key in expected_keys}

    assert result_ids == expected_ids
