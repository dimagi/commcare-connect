"""Models for the opportunity app, split by domain into the modules of this package.

Everything is re-exported here, so import from ``commcare_connect.opportunity.models`` rather than from
a submodule. Submodules import each other at module level only along foreign keys, which keeps those imports
acyclic; a method that needs a model from a module further down that chain imports it locally.

The event models that ``pghistory.track`` generates (``OpportunityActiveEvent``,
``PaymentInvoiceStatusEvent`` and the rest) are attached to this package by pghistory itself, so they
need no import here.
"""

from commcare_connect.opportunity.models.access import (
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    UserInvite,
    UserInviteStatus,
)
from commcare_connect.opportunity.models.assigned_tasks import (
    AssignedTask,
    AssignedTaskStatus,
    TaskType,
    TaskTypeModeChoices,
)
from commcare_connect.opportunity.models.base import CHOICE_FIELD_MAX_LENGTH, XFormBaseModel
from commcare_connect.opportunity.models.commcare_app import CommCareApp, HQApiKey
from commcare_connect.opportunity.models.completed_work import CompletedWork, CompletedWorkStatus
from commcare_connect.opportunity.models.invoices import (
    CompletedWorkInvoice,
    InvoiceFinanceRequest,
    InvoiceFinanceRequestStatus,
    InvoiceStatus,
    Payment,
    PaymentInvoice,
)
from commcare_connect.opportunity.models.labs import LabsRecord
from commcare_connect.opportunity.models.learn import Assessment, CompletedModule, LearnModule
from commcare_connect.opportunity.models.opportunity import (
    CredentialConfiguration,
    Opportunity,
    OpportunityStatus,
)
from commcare_connect.opportunity.models.payment_units import SYSTEM_USER, DeliverUnit, PaymentUnit, PaymentUnitRate
from commcare_connect.opportunity.models.reference import Country, Currency, DeliveryType, ExchangeRate
from commcare_connect.opportunity.models.verification import (
    DeliverUnitFlagRules,
    FormJsonValidationRules,
    OpportunityVerificationFlags,
)
from commcare_connect.opportunity.models.visits import (
    AudioAttachment,
    BlobMeta,
    OverLimitReasonChoices,
    UserVisit,
    VisitReviewStatus,
    VisitValidationStatus,
)

__all__ = [
    "CHOICE_FIELD_MAX_LENGTH",
    "SYSTEM_USER",
    "Assessment",
    "AssignedTask",
    "AssignedTaskStatus",
    "AudioAttachment",
    "BlobMeta",
    "CommCareApp",
    "CompletedModule",
    "CompletedWork",
    "CompletedWorkInvoice",
    "CompletedWorkStatus",
    "Country",
    "CredentialConfiguration",
    "Currency",
    "DeliverUnit",
    "DeliverUnitFlagRules",
    "DeliveryType",
    "ExchangeRate",
    "FormJsonValidationRules",
    "HQApiKey",
    "InvoiceFinanceRequest",
    "InvoiceFinanceRequestStatus",
    "InvoiceStatus",
    "LabsRecord",
    "LearnModule",
    "Opportunity",
    "OpportunityAccess",
    "OpportunityClaim",
    "OpportunityClaimLimit",
    "OpportunityStatus",
    "OpportunityVerificationFlags",
    "OverLimitReasonChoices",
    "Payment",
    "PaymentInvoice",
    "PaymentUnit",
    "PaymentUnitRate",
    "TaskType",
    "TaskTypeModeChoices",
    "UserInvite",
    "UserInviteStatus",
    "UserVisit",
    "VisitReviewStatus",
    "VisitValidationStatus",
    "XFormBaseModel",
]
