import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property

from django.apps import apps

from commcare_connect.opportunity.models import Opportunity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelDeletion:
    app_label: str
    model_name: str
    lookup: str

    @cached_property
    def model(self):
        return apps.get_model(self.app_label, self.model_name)

    @cached_property
    def _opp_id_filter(self):
        if self.lookup.endswith("__id") or self.lookup.endswith("_id"):
            return self.lookup
        return f"{self.lookup}__id"

    def queryset(self, opportunity_id):
        return self.model.objects.filter(**{self._opp_id_filter: opportunity_id})

    def delete(self, opportunity_id):
        deleted, _ = self.queryset(opportunity_id).delete()
        return deleted


# Listed in the order of dependencies
OPPORTUNITY_DELETIONS: Sequence[ModelDeletion] = (
    ModelDeletion("opportunity", "CompletedModule", "opportunity"),
    ModelDeletion("opportunity", "Assessment", "opportunity"),
    ModelDeletion("opportunity", "Payment", "opportunity_access__opportunity"),
    ModelDeletion("opportunity", "Payment", "payment_unit__opportunity"),
    ModelDeletion("opportunity", "CatchmentArea", "opportunity"),
    ModelDeletion("opportunity", "CatchmentArea", "opportunity_access__opportunity"),
    ModelDeletion("opportunity", "OpportunityAccess", "opportunity"),
    ModelDeletion("opportunity", "DeliverUnit", "payment_unit__opportunity"),
    ModelDeletion("opportunity", "PaymentUnit", "opportunity"),
    ModelDeletion("opportunity", "PaymentInvoice", "opportunity"),
    ModelDeletion("opportunity", "OpportunityVerificationFlags", "opportunity"),
    ModelDeletion("opportunity", "UserInvite", "opportunity"),
    ModelDeletion("opportunity", "FormJsonValidationRules", "opportunity"),
    ModelDeletion("opportunity", "DeliverUnitFlagRules", "opportunity"),
    ModelDeletion("opportunity", "CredentialConfiguration", "opportunity"),
    ModelDeletion("users", "UserCredential", "opportunity"),
    ModelDeletion("opportunity", "LabsRecord", "opportunity"),
)


def delete_opportunity_data(opportunity_or_id):
    if isinstance(opportunity_or_id, Opportunity):
        opportunity_id = opportunity_or_id.pk
    else:
        opportunity_id = opportunity_or_id
    total_deleted = 0
    for deletion in OPPORTUNITY_DELETIONS:
        deleted = deletion.delete(opportunity_id)
        total_deleted += deleted
        logger.info(
            "Deleted %s rows from %s",
            deleted,
            deletion.model._meta.label,
        )
    logger.info("Deleted %s total rows tied to Opportunity %s", total_deleted, opportunity_id)


def delete_opportunity(opportunity_or_id):
    if isinstance(opportunity_or_id, Opportunity):
        opportunity = opportunity_or_id
    else:
        opportunity = Opportunity.objects.get(pk=opportunity_or_id)
    delete_opportunity_data(opportunity.pk)
    opportunity.delete()
