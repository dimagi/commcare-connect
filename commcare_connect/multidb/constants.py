from commcare_connect.opportunity.models import (
    Assessment,
    CommCareApp,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    DeliveryType,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    Payment,
    PaymentUnit,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.reports.models import UserAnalyticsData
from commcare_connect.users.models import ConnectIDUserLink, User

PUBLICATION_NAME = "tables_for_superset_pub"
SUBSCRIPTION_NAME = "tables_for_superset_sub"

# To add/remove more models, add/remove the model here and run setup_logical_replication command
# Additional step to remove, manually drop all rows from the replica after running the command
REPLICATION_ALLOWED_MODELS = [
    Assessment,
    CompletedModule,
    CompletedWork,
    ConnectIDUserLink,
    DeliverUnit,
    DeliveryType,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    OpportunityClaimLimit,
    Organization,
    CommCareApp,
    Payment,
    PaymentUnit,
    Program,
    User,
    UserVisit,
    UserAnalyticsData,
]
