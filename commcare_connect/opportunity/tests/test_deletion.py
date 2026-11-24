import pytest
from django.db import IntegrityError

from commcare_connect.microplanning.tests.factories import WorkAreaFactory
from commcare_connect.opportunity.deletion import OPPORTUNITY_DELETIONS, ModelDeletion, delete_opportunity
from commcare_connect.opportunity.models import CompletedWorkInvoice, DeliverUnit, LabsRecord, Opportunity, Payment
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
def test_delete_opportunity_leaves_another_opportunitys_units_on_the_same_app():
    opp_a = factories.OpportunityFactory(active=False)
    opp_a_unit = factories.DeliverUnitFactory(
        opportunity=opp_a, app=opp_a.deliver_app, payment_unit=factories.PaymentUnitFactory(opportunity=opp_a)
    )
    opp_a_visit = factories.UserVisitFactory(opportunity=opp_a, deliver_unit=opp_a_unit)
    # Opp B reuses the app, so it has its own unit with the same slug
    opp_b = factories.OpportunityFactory(deliver_app=opp_a.deliver_app)
    opp_b_unit = factories.DeliverUnitFactory(
        opportunity=opp_b, app=opp_a.deliver_app, slug=opp_a_unit.slug, payment_unit=None
    )

    assert delete_opportunity(opp_b) is True

    assert not DeliverUnit.objects.filter(pk=opp_b_unit.pk).exists()
    opp_a_visit.refresh_from_db()
    assert opp_a_visit.deliver_unit == opp_a_unit


@pytest.mark.django_db
def test_delete_opportunity_with_a_visit_that_has_no_access():
    opportunity = factories.OpportunityFactory()
    deliver_unit = factories.DeliverUnitFactory(opportunity=opportunity, app=opportunity.deliver_app)
    factories.UserVisitFactory(opportunity=opportunity, opportunity_access=None, deliver_unit=deliver_unit)

    assert delete_opportunity(opportunity) is True
    assert not DeliverUnit.objects.filter(pk=deliver_unit.pk).exists()


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
