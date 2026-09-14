import pytest
from django.db import IntegrityError

from commcare_connect.microplanning.tests.factories import WorkAreaFactory
from commcare_connect.opportunity.deletion import OPPORTUNITY_DELETIONS, ModelDeletion, delete_opportunity
from commcare_connect.opportunity.models import CompletedWorkInvoice, LabsRecord, Opportunity, Payment
from commcare_connect.opportunity.tests import factories


@pytest.mark.django_db
@pytest.mark.parametrize("opportunity_factory", [factories.OpportunityFactory])
def test_delete_opportunity_clears_registered_models(opportunity_factory):
    opportunity = opportunity_factory()
    opportunity_id = opportunity.id
    access = factories.OpportunityAccessFactory(opportunity=opportunity)
    payment_unit = factories.PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = factories.DeliverUnitFactory(payment_unit=payment_unit)

    factories.CompletedModuleFactory(opportunity=opportunity, opportunity_access=access)
    factories.AssessmentFactory(opportunity=opportunity, opportunity_access=access)
    factories.PaymentFactory(opportunity_access=access, payment_unit=payment_unit)
    factories.PaymentInvoiceFactory(opportunity=opportunity)
    factories.OpportunityVerificationFlagsFactory(opportunity=opportunity)
    factories.UserInviteFactory(opportunity=opportunity, opportunity_access=access)
    factories.FormJsonValidationRulesFactory(opportunity=opportunity)
    factories.DeliverUnitFlagRulesFactory(opportunity=opportunity, deliver_unit=deliver_unit)
    factories.CredentialConfigurationFactory(opportunity=opportunity)
    factories.UserCredentialFactory(opportunity=opportunity)
    completed_work = factories.CompletedWorkFactory(opportunity_access=access, payment_unit=payment_unit)
    factories.UserVisitFactory(
        opportunity=opportunity,
        opportunity_access=access,
        deliver_unit=deliver_unit,
        completed_work=completed_work,
        work_area=WorkAreaFactory(opportunity=opportunity),
    )

    LabsRecord.objects.create(
        opportunity=opportunity,
        organization=opportunity.organization,
        experiment="cleanup",
        type="note",
        data={},
    )

    result = delete_opportunity(opportunity)

    assert result is True
    for deletion in OPPORTUNITY_DELETIONS:
        assert not deletion.model.objects.filter(**{deletion._opp_id_filter: opportunity_id}).exists()
    assert not Opportunity.objects.filter(pk=opportunity_id).exists()


@pytest.mark.django_db
def test_delete_opportunity_detaches_deliver_unit_still_used_by_another_opportunity():
    # Opp A: the DeliverUnit's original owner, still has visit history against it.
    opp_a = factories.OpportunityFactory()
    opp_a_access = factories.OpportunityAccessFactory(opportunity=opp_a)
    opp_a_payment_unit = factories.PaymentUnitFactory(opportunity=opp_a)
    shared_deliver_unit = factories.DeliverUnitFactory(payment_unit=opp_a_payment_unit)
    factories.UserVisitFactory(
        opportunity=opp_a,
        opportunity_access=opp_a_access,
        deliver_unit=shared_deliver_unit,
    )
    opp_a.active = False
    opp_a.save()

    # Opp B: reused the same DeliverUnit for its own PaymentUnit (see add_payment_unit/
    # edit_payment_unit, which lets a new opportunity claim a DeliverUnit from an inactive
    # opportunity's PaymentUnit by reassigning the FK rather than creating a new DeliverUnit row).
    # Opp B is the one being deleted here.
    opp_b = factories.OpportunityFactory()
    opp_b_payment_unit = factories.PaymentUnitFactory(opportunity=opp_b)
    shared_deliver_unit.payment_unit = opp_b_payment_unit
    shared_deliver_unit.save()

    result = delete_opportunity(opp_b)

    # Opp B is still deleted successfully, as it has no visits of its own...
    assert result is True
    assert not Opportunity.objects.filter(pk=opp_b.pk).exists()
    # ...but the shared DeliverUnit is detached (not deleted), since Opp A's visit history still
    # needs it to exist.
    shared_deliver_unit.refresh_from_db()
    assert shared_deliver_unit.payment_unit_id is None
    assert opp_a.uservisit_set.filter(deliver_unit=shared_deliver_unit).exists()


@pytest.mark.django_db
def test_delete_opportunity_with_nm_payment_linked_to_invoice():
    opportunity = factories.OpportunityFactory()
    invoice = factories.PaymentInvoiceFactory(opportunity=opportunity)
    payment = factories.PaymentFactory(opportunity_access=None, payment_unit=None, invoice=invoice)

    result = delete_opportunity(opportunity)

    assert result is True
    assert not Payment.objects.filter(pk=payment.pk).exists()


@pytest.mark.django_db
def test_delete_opportunity_is_atomic(monkeypatch):
    opportunity = factories.OpportunityFactory()
    opportunity_id = opportunity.id
    access = factories.OpportunityAccessFactory(opportunity=opportunity)
    factories.CompletedModuleFactory(opportunity=opportunity, opportunity_access=access)

    original_delete = ModelDeletion.delete

    def fake_delete(self, opportunity_id):
        if self.model_name == "CompletedModule":
            return original_delete(self, opportunity_id)
        raise IntegrityError("some error")

    monkeypatch.setattr(ModelDeletion, "delete", fake_delete)

    result = delete_opportunity(opportunity)

    assert result is False
    assert Opportunity.objects.filter(pk=opportunity_id).exists()
    assert opportunity.completedmodule_set.exists()


@pytest.mark.django_db
def test_delete_opportunity_removes_snapshot_rows():
    access = factories.OpportunityAccessFactory()
    opportunity = access.opportunity
    invoice = factories.PaymentInvoiceFactory(opportunity=opportunity)
    work = factories.CompletedWorkFactory(opportunity_access=access)
    factories.CompletedWorkInvoiceFactory(invoice=invoice, completed_work=work)

    assert delete_opportunity(opportunity) is True
    assert not CompletedWorkInvoice.objects.exists()
