from commcare_connect.opportunity.models import (
    Assessment,
    CompletedModule,
    CompletedWork,
    DeliverUnit,
    DeliveryType,
    LearnModule,
    Opportunity,
    OpportunityAccess,
    OpportunityClaim,
    Payment,
    PaymentUnit,
    UserVisit,
)
from commcare_connect.organization.models import Organization
from commcare_connect.program.models import Program
from commcare_connect.users.models import ConnectIDUserLink, User

PUBLICATION_NAME = "tables_for_superset_pub"
SUBSCRIPTION_NAME = "tables_for_superset_sub"

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
    Organization,
    Payment,
    PaymentUnit,
    Program,
    User,
    UserVisit,
]
