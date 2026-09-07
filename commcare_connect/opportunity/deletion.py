import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property

import sentry_sdk
from django.apps import apps
from django.db import transaction

from commcare_connect.opportunity.models import DeliverUnit, Opportunity

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
    ModelDeletion("opportunity", "CompletedWorkInvoice", "invoice__opportunity"),
    ModelDeletion("opportunity", "CompletedWorkInvoice", "completed_work__opportunity_access__opportunity"),
    ModelDeletion("opportunity", "CompletedModule", "opportunity"),
    ModelDeletion("opportunity", "Assessment", "opportunity"),
    ModelDeletion("opportunity", "Payment", "opportunity_access__opportunity"),
    ModelDeletion("opportunity", "Payment", "payment_unit__opportunity"),
    ModelDeletion("opportunity", "Payment", "invoice__opportunity"),
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


def delete_opportunity(opportunity_or_id):
    if isinstance(opportunity_or_id, Opportunity):
        opportunity = opportunity_or_id
    else:
        opportunity = Opportunity.objects.get(pk=opportunity_or_id)
    opportunity_id = opportunity.pk
    total_deleted = 0
    try:
        with transaction.atomic():
            for deletion in OPPORTUNITY_DELETIONS:
                if deletion.app_label == "opportunity" and deletion.model_name == "DeliverUnit":
                    # A DeliverUnit can be reassigned to a different opportunity's PaymentUnit while
                    # its deliver_app is reused (see add_payment_unit/edit_payment_unit), so by now
                    # (this opportunity's own UserVisits were already cascade-deleted via its
                    # OpportunityAccess above) any DeliverUnit still referenced by a UserVisit
                    # belongs to another, still-active opportunity. Deleting it would violate that
                    # other opportunity's data, so detach it from this opportunity instead.
                    detached = DeliverUnit.objects.filter(
                        payment_unit__opportunity_id=opportunity_id, uservisit__isnull=False
                    ).update(payment_unit=None)
                    if detached:
                        logger.info("Detached %s DeliverUnit(s) still referenced by another opportunity", detached)

                deleted = deletion.delete(opportunity_id)
                total_deleted += deleted
                logger.info(
                    "Deleted %s rows from %s",
                    deleted,
                    deletion.model._meta.label,
                )
            logger.info("Deleted %s total rows tied to Opportunity %s", total_deleted, opportunity_id)
            opportunity.delete()
            return True
    except Exception:
        sentry_sdk.capture_exception()
        logger.exception("Failed to delete Opportunity %s", opportunity_id)
        return False
