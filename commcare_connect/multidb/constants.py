from commcare_connect.audit.models import AuditReport, AuditReportEntry
from commcare_connect.commcarehq.models import HQServer
from commcare_connect.flags.models import Flag
from commcare_connect.microplanning.models import WorkArea, WorkAreaGroup
from commcare_connect.opportunity.models import (
    Assessment,
    AssignedTask,
    BlobMeta,
    CatchmentArea,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    Country,
    CredentialConfiguration,
    Currency,
    DeliverUnit,
    DeliverUnitFlagRules,
    DeliveryType,
    ExchangeRate,
    FormJsonValidationRules,
    HQApiKey,
    LabsRecord,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    TaskType,
    UserInvite,
    UserVisit,
)
from commcare_connect.organization.models import LLOEntity, Organization, UserOrganizationMembership
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication
from commcare_connect.reports.models import UserAnalyticsData
from commcare_connect.users.models import ConnectIDUserLink, User, UserCredential

PUBLICATION_NAME = "tables_for_superset_pub"
SUBSCRIPTION_NAME = "tables_for_superset_sub"

# Models whose tables are replicated to the secondary (Superset) database.
# Every local app model must appear in exactly ONE of the two lists below;
# a Django system check (see checks.py) fails otherwise. Adding a sensitive
# model? Put it in REPLICATION_EXCLUDED_MODELS.
REPLICATION_INCLUDED_MODELS = [
    Assessment,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    ConnectIDUserLink,
    Country,
    Currency,
    DeliverUnit,
    DeliverUnitFlagRules,
    DeliveryType,
    LearnModule,
    LLOEntity,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    OpportunityVerificationFlags,
    Organization,
    Payment,
    PaymentInvoice,
    PaymentUnit,
    Program,
    User,
    UserAnalyticsData,
    UserCredential,
    UserVisit,
]

# Local app models deliberately NOT replicated (PII, secrets, operational
# noise, or simply not needed in Superset).
REPLICATION_EXCLUDED_MODELS = [
    AuditReport,
    AuditReportEntry,
    HQServer,
    Flag,
    WorkArea,
    WorkAreaGroup,
    AssignedTask,
    BlobMeta,
    CatchmentArea,
    CredentialConfiguration,
    ExchangeRate,
    FormJsonValidationRules,
    HQApiKey,
    LabsRecord,
    TaskType,
    UserInvite,
    UserOrganizationMembership,
    ManagedOpportunity,
    ProgramApplication,
]
