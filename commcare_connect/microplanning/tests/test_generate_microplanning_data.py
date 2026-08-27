import pytest

from commcare_connect.microplanning.const import SERVICE_DELIVERY_UNIT_SLUG
from commcare_connect.microplanning.management.commands.generate_microplanning_data import Command
from commcare_connect.opportunity.tests.factories import DeliverUnitFactory, PaymentUnitFactory


@pytest.mark.django_db
def test_reuses_existing_pairing(opportunity):
    """Deliver unit already owned by one of this opportunity's payment units keeps that pairing."""
    existing_pu = PaymentUnitFactory(opportunity=opportunity, name="Their Own Unit")
    du = DeliverUnitFactory(app=opportunity.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG, payment_unit=existing_pu)

    units = Command().ensure_payment_units(opportunity)

    pu, paired_du = units[0]
    assert paired_du.pk == du.pk
    assert pu.pk == existing_pu.pk
    du.refresh_from_db()
    assert du.payment_unit_id == existing_pu.pk


@pytest.mark.django_db
def test_repoints_foreign_pairing(opportunity):
    """Deliver unit on another opportunity's payment unit gets repointed at the seeded one."""
    foreign_pu = PaymentUnitFactory(name="Elsewhere")
    assert foreign_pu.opportunity_id != opportunity.id
    du = DeliverUnitFactory(app=opportunity.deliver_app, slug=SERVICE_DELIVERY_UNIT_SLUG, payment_unit=foreign_pu)

    pu, paired_du = Command().ensure_payment_units(opportunity)[0]

    assert paired_du.pk == du.pk
    assert pu.opportunity_id == opportunity.id
    du.refresh_from_db()
    assert du.payment_unit_id == pu.pk


@pytest.mark.django_db
def test_seeds_from_scratch(opportunity):
    units = Command().ensure_payment_units(opportunity)
    assert len(units) == 2
    for pu, du in units:
        assert du.payment_unit_id == pu.pk
        assert pu.opportunity_id == opportunity.id


@pytest.mark.django_db
def test_idempotent(opportunity):
    first = Command().ensure_payment_units(opportunity)
    second = Command().ensure_payment_units(opportunity)
    assert [(p.pk, d.pk) for p, d in first] == [(p.pk, d.pk) for p, d in second]
