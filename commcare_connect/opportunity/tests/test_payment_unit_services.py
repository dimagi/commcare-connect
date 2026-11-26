import datetime

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from waffle.testutils import override_switch

from commcare_connect.flags.switch_names import OPTIONAL_DAILY_LIMIT
from commcare_connect.opportunity.models import OpportunityClaimLimit
from commcare_connect.opportunity.services.payment_units import (
    PaymentUnitInput,
    create_payment_units,
    update_payment_unit,
)
from commcare_connect.opportunity.tests.factories import (
    DeliverUnitFactory,
    OpportunityClaimFactory,
    OpportunityFactory,
    PaymentUnitFactory,
)
from commcare_connect.users.tests.factories import MembershipFactory, UserFactory


@pytest.fixture
def deliver_units(pm_opportunity):
    return DeliverUnitFactory.create_batch(2, app=pm_opportunity.deliver_app, payment_unit=None)


@pytest.mark.django_db
class TestAccess:
    @pytest.mark.parametrize(
        "relationship, role, allowed",
        [
            ("program_org", "admin", True),
            ("supervisor", "admin", True),
            ("funder", "admin", True),
            ("program_org", "member", False),
            # Program-manager actions need ADMIN through an org other than the delivering one (ADR-0003).
            ("delivery", "admin", False),
            ("watcher", "admin", False),
            ("unrelated", "admin", False),
        ],
    )
    def test_only_program_managers_may_create(
        self, opp_orgs, pm_opportunity, deliver_units, relationship, role, allowed
    ):
        user = UserFactory()
        MembershipFactory(user=user, organization=opp_orgs[relationship], role=role)

        if allowed:
            create_payment_units(user, pm_opportunity, [_input(required=[deliver_units[0].id])])
            assert pm_opportunity.paymentunit_set.count() == 1
        else:
            with pytest.raises(PermissionDenied):
                create_payment_units(user, pm_opportunity, [_input(required=[deliver_units[0].id])])

    def test_delivering_org_admin_may_not_update(self, pm_opportunity, org_user_admin):
        payment_unit = PaymentUnitFactory(opportunity=pm_opportunity)

        with pytest.raises(PermissionDenied):
            update_payment_unit(org_user_admin, payment_unit, _input())


@pytest.mark.django_db
class TestCreate:
    def test_links_deliver_units_and_children(self, pm_opportunity, program_manager_org_user_admin, deliver_units):
        required, optional = deliver_units
        child = PaymentUnitFactory(opportunity=pm_opportunity)

        (payment_unit,) = create_payment_units(
            program_manager_org_user_admin,
            pm_opportunity,
            [
                _input(
                    required=[required.id],
                    optional=[optional.id],
                    child_payment_units=[child.id],
                    deliver_unit_display_names={required.id: "ANC visit"},
                )
            ],
        )

        required.refresh_from_db()
        optional.refresh_from_db()
        child.refresh_from_db()
        assert (required.payment_unit, required.optional, required.display_name) == (payment_unit, False, "ANC visit")
        assert (optional.payment_unit, optional.optional) == (payment_unit, True)
        assert child.parent_payment_unit == payment_unit

    def test_gives_existing_claims_a_limit(self, pm_opportunity, program_manager_org_user_admin, deliver_units):
        pm_opportunity.total_budget = 100_000
        pm_opportunity.save()
        claim = OpportunityClaimFactory(opportunity_access__opportunity=pm_opportunity)

        (payment_unit,) = create_payment_units(
            program_manager_org_user_admin, pm_opportunity, [_input(required=[deliver_units[0].id])]
        )

        assert OpportunityClaimLimit.objects.filter(opportunity_claim=claim, payment_unit=payment_unit).exists()

    def test_creates_none_when_one_is_invalid(self, pm_opportunity, program_manager_org_user_admin, deliver_units):
        units = [
            _input(name="Valid", required=[deliver_units[0].id]),
            _input(
                name="Invalid",
                required=[deliver_units[1].id],
                start_date=datetime.date(2026, 2, 1),
                end_date=datetime.date(2026, 1, 1),
            ),
        ]

        with pytest.raises(ValidationError):
            create_payment_units(program_manager_org_user_admin, pm_opportunity, units)

        assert not pm_opportunity.paymentunit_set.exists()

    def test_rejects_deliver_unit_in_two_new_units(
        self, pm_opportunity, program_manager_org_user_admin, deliver_units
    ):
        units = [_input(name="A", required=[deliver_units[0].id]), _input(name="B", required=[deliver_units[0].id])]

        with pytest.raises(ValidationError) as error:
            create_payment_units(program_manager_org_user_admin, pm_opportunity, units)

        assert "required_deliver_units" in error.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    "case, field",
    [
        ("end_before_start", "end_date"),
        ("required_and_optional", "optional_deliver_units"),
        ("other_opportunitys_deliver_unit", "required_deliver_units"),
        ("assigned_deliver_unit", "required_deliver_units"),
        ("child_with_parent", "child_payment_units"),
        ("other_opportunitys_child", "child_payment_units"),
    ],
)
def test_rejects_invalid_unit(pm_opportunity, program_manager_org_user_admin, deliver_units, case, field):
    free = deliver_units[0]
    other_opportunity = OpportunityFactory()
    unit = {
        "end_before_start": _input(
            required=[free.id], start_date=datetime.date(2026, 2, 1), end_date=datetime.date(2026, 1, 1)
        ),
        "required_and_optional": _input(required=[free.id], optional=[free.id]),
        "other_opportunitys_deliver_unit": _input(
            required=[DeliverUnitFactory(app=other_opportunity.deliver_app, payment_unit=None).id]
        ),
        "assigned_deliver_unit": _input(
            required=[DeliverUnitFactory(app=pm_opportunity.deliver_app).id]  # factory gives it a payment unit
        ),
        "child_with_parent": _input(
            required=[free.id],
            child_payment_units=[
                PaymentUnitFactory(
                    opportunity=pm_opportunity,
                    parent_payment_unit=PaymentUnitFactory(opportunity=pm_opportunity),
                ).id
            ],
        ),
        "other_opportunitys_child": _input(
            required=[free.id], child_payment_units=[PaymentUnitFactory(opportunity=other_opportunity).id]
        ),
    }[case]

    with pytest.raises(ValidationError) as error:
        create_payment_units(program_manager_org_user_admin, pm_opportunity, [unit])

    assert field in error.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize("switch_active", [True, False])
def test_no_daily_limit_needs_switch(pm_opportunity, program_manager_org_user_admin, deliver_units, switch_active):
    unit = _input(required=[deliver_units[0].id], max_daily=None)

    with override_switch(OPTIONAL_DAILY_LIMIT, active=switch_active):
        if switch_active:
            (payment_unit,) = create_payment_units(program_manager_org_user_admin, pm_opportunity, [unit])
            assert payment_unit.max_daily is None
        else:
            with pytest.raises(ValidationError) as error:
                create_payment_units(program_manager_org_user_admin, pm_opportunity, [unit])
            assert "max_daily" in error.value.message_dict


@pytest.mark.django_db
class TestBudget:
    """The per-user delivery limit is allocated from a shared budget pool per payment unit.

    Adding or enlarging a payment unit lowers the derived ``budgeted_workers``; if it drops below the count of
    workers who have already claimed, some workers silently receive a reduced limit (or none). The services must
    reject such changes.
    """

    @pytest.mark.parametrize(
        "num_existing, total_budget, num_claimants, edit_existing, new_max_total, expect_valid",
        [
            # Budget covers 3 users with only the existing unit; adding a second unit needs double.
            pytest.param(1, 1500, 3, False, 100, False, id="reject_new_over_budget"),
            pytest.param(1, 3000, 3, False, 100, True, id="accept_new_within_budget"),
            # Budget that would fail with claimants, proving the no-claimants early return fires.
            pytest.param(1, 1500, 0, False, 100, True, id="skip_when_no_claimants"),
            # Doubling an existing unit's max_total pushes budgeted_workers below the 3 claimants.
            pytest.param(2, 3000, 3, True, 200, False, id="reject_enlarge_over_budget"),
        ],
    )
    def test_budget_covers_claimants(
        self,
        pm_opportunity,
        program_manager_org_user_admin,
        num_existing,
        total_budget,
        num_claimants,
        edit_existing,
        new_max_total,
        expect_valid,
    ):
        units = PaymentUnitFactory.create_batch(
            num_existing, opportunity=pm_opportunity, max_total=100, amount=5, org_amount=0
        )
        pm_opportunity.total_budget = total_budget
        pm_opportunity.save()
        OpportunityClaimFactory.create_batch(
            num_claimants, opportunity_access__opportunity=pm_opportunity, opportunity_access__accepted=True
        )
        unit = _input(max_total=new_max_total, amount=5, org_amount=0)

        def change():
            if edit_existing:
                update_payment_unit(program_manager_org_user_admin, units[0], unit)
            else:
                create_payment_units(program_manager_org_user_admin, pm_opportunity, [unit])

        if expect_valid:
            change()
        else:
            with pytest.raises(ValidationError, match="budget cannot give the full limit"):
                change()


@pytest.mark.django_db
def test_update_replaces_deliver_units_and_children(pm_opportunity, program_manager_org_user_admin, deliver_units):
    kept, added = deliver_units
    payment_unit = PaymentUnitFactory(opportunity=pm_opportunity)
    removed = DeliverUnitFactory(app=pm_opportunity.deliver_app, payment_unit=payment_unit, optional=True)
    kept.payment_unit = payment_unit
    kept.save()
    old_child = PaymentUnitFactory(opportunity=pm_opportunity, parent_payment_unit=payment_unit)
    new_child = PaymentUnitFactory(opportunity=pm_opportunity)

    update_payment_unit(
        program_manager_org_user_admin,
        payment_unit,
        _input(name="Renamed", required=[kept.id], optional=[added.id], child_payment_units=[new_child.id]),
    )

    for unit in (kept, added, removed, old_child, new_child):
        unit.refresh_from_db()
    payment_unit.refresh_from_db()
    assert payment_unit.name == "Renamed"
    assert (kept.payment_unit, kept.optional) == (payment_unit, False)
    assert (added.payment_unit, added.optional) == (payment_unit, True)
    assert (removed.payment_unit, removed.optional) == (None, False)
    assert old_child.parent_payment_unit is None
    assert new_child.parent_payment_unit == payment_unit


def _input(*, required=(), optional=(), **overrides):
    values = {
        "name": "Visit",
        "amount": 5,
        "org_amount": 1,
        "max_total": 10,
        "max_daily": 2,
        "required_deliver_units": list(required),
        "optional_deliver_units": list(optional),
        **overrides,
    }
    return PaymentUnitInput(**values)
