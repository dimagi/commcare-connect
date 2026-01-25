import pytest
from django.db import IntegrityError

from commcare_connect.opportunity.deletion import OPPORTUNITY_DELETIONS, ModelDeletion, delete_opportunity
from commcare_connect.opportunity.models import LabsRecord, Opportunity
from commcare_connect.opportunity.tests import factories
from commcare_connect.program.tests.factories import ManagedOpportunityFactory


@pytest.mark.django_db
@pytest.mark.parametrize("opportunity_factory", [factories.OpportunityFactory, ManagedOpportunityFactory])
def test_delete_opportunity_clears_registered_models(opportunity_factory):
    opportunity = opportunity_factory()
    opportunity_id = opportunity.id
    access = factories.OpportunityAccessFactory(opportunity=opportunity)
    payment_unit = factories.PaymentUnitFactory(opportunity=opportunity)
    deliver_unit = factories.DeliverUnitFactory(payment_unit=payment_unit)

    factories.CompletedModuleFactory(opportunity=opportunity, opportunity_access=access)
    factories.AssessmentFactory(opportunity=opportunity, opportunity_access=access)
    factories.PaymentFactory(opportunity_access=access, payment_unit=payment_unit)
    factories.CatchmentAreaFactory(opportunity=opportunity, opportunity_access=None)
    factories.CatchmentAreaFactory(opportunity=opportunity, opportunity_access=access)
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
    )
    LabsRecord.objects.create(
        opportunity=opportunity,
        organization=opportunity.organization,
        experiment="cleanup",
        type="note",
        data={},
    )

    delete_opportunity(opportunity)

    for deletion in OPPORTUNITY_DELETIONS:
        assert not deletion.model.objects.filter(**{deletion._opp_id_filter: opportunity_id}).exists()
    assert not Opportunity.objects.filter(pk=opportunity_id).exists()


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

    with pytest.raises(IntegrityError):
        delete_opportunity(opportunity)

    assert Opportunity.objects.filter(pk=opportunity_id).exists()
    assert opportunity.completedmodule_set.exists()
