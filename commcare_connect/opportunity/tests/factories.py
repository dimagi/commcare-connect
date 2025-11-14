from datetime import timezone

from factory import DictFactory, Faker, LazyAttribute, SelfAttribute, SubFactory
from factory.django import DjangoModelFactory

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.opportunity.models import VisitValidationStatus
from commcare_connect.users.tests.factories import OrganizationFactory


class ApplicationFactory(DictFactory):
    id = Faker("pystr")
    name = Faker("name")
    domain = Faker("name")


class CommCareAppFactory(DjangoModelFactory):
    organization = SubFactory(OrganizationFactory)
    cc_domain = Faker("name")
    cc_app_id = Faker("uuid4")
    name = Faker("name")
    description = Faker("text")
    passing_score = Faker("pyint", min_value=50, max_value=100, step=5)
    hq_server = SubFactory(HQServerFactory)

    class Meta:
        model = "opportunity.CommCareApp"


class HQApiKeyFactory(DjangoModelFactory):
    api_key = Faker("uuid4")
    user = SubFactory("commcare_connect.users.tests.factories.UserFactory")
    hq_server = SubFactory(HQServerFactory)

    class Meta:
        model = "opportunity.HQApiKey"


class DeliveryTypeFactory(DjangoModelFactory):
    name = Faker("name")
    slug = Faker("pystr")

    class Meta:
        model = "opportunity.DeliveryType"


class OpportunityFactory(DjangoModelFactory):
    organization = SubFactory(OrganizationFactory)
    name = Faker("name")
    description = Faker("text")
    short_description = Faker("pystr", max_chars=50)
    active = True
    learn_app = SubFactory(CommCareAppFactory, organization=SelfAttribute("..organization"))
    deliver_app = SubFactory(CommCareAppFactory, organization=SelfAttribute("..organization"))
    start_date = Faker("past_date")
    end_date = Faker("future_date")
    total_budget = Faker("pyint", min_value=1000, max_value=10000)
    api_key = SubFactory(HQApiKeyFactory)
    delivery_type = SubFactory(DeliveryTypeFactory)
    currency = "USD"
    hq_server = SubFactory(HQServerFactory)

    class Meta:
        model = "opportunity.Opportunity"


class OpportunityAccessFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    user = SubFactory("commcare_connect.users.tests.factories.MobileUserFactory")

    class Meta:
        model = "opportunity.OpportunityAccess"
        django_get_or_create = ["opportunity", "user"]


class OpportunityVerificationFlagsFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    form_submission_start = None  # Default to None
    form_submission_end = None  # Default to None

    class Meta:
        model = "opportunity.OpportunityVerificationFlags"


class LearnModuleFactory(DjangoModelFactory):
    app = SubFactory(CommCareAppFactory)
    slug = Faker("pystr")
    name = Faker("name")
    description = Faker("text")
    time_estimate = Faker("pyint", min_value=1, max_value=10)

    class Meta:
        model = "opportunity.LearnModule"


class PaymentUnitFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    name = Faker("name")
    description = Faker("text")
    amount = Faker("pyint", min_value=1, max_value=10)
    max_daily = Faker("pyint", min_value=1, max_value=10)
    max_total = LazyAttribute(lambda o: o.max_daily * 2)

    # parent_payment_unit = SubFactory("commcare_connect.opportunity.tests.factories.PaymentUnitFactory")

    class Meta:
        model = "opportunity.PaymentUnit"


class DeliverUnitFactory(DjangoModelFactory):
    app = SubFactory(CommCareAppFactory)
    slug = Faker("pystr")
    name = Faker("name")
    payment_unit = SubFactory(PaymentUnitFactory)

    class Meta:
        model = "opportunity.DeliverUnit"


class CompletedWorkFactory(DjangoModelFactory):
    opportunity_access = SubFactory(OpportunityAccessFactory)
    payment_unit = SubFactory(PaymentUnitFactory)
    entity_id = Faker("uuid4")
    entity_name = Faker("name")

    class Meta:
        model = "opportunity.CompletedWork"


class UserVisitFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    user = SubFactory("commcare_connect.users.tests.factories.UserFactory")
    opportunity_access = SubFactory(OpportunityAccessFactory)
    deliver_unit = SubFactory(DeliverUnitFactory)
    status = Faker("enum", enum_cls=VisitValidationStatus)
    visit_date = Faker("date_time", tzinfo=timezone.utc)
    form_json = Faker("pydict", value_types=[str, int, float, bool])
    xform_id = Faker("uuid4")
    completed_work = SubFactory(CompletedWorkFactory)

    class Meta:
        model = "opportunity.UserVisit"


class OpportunityClaimFactory(DjangoModelFactory):
    opportunity_access = SubFactory(OpportunityAccessFactory)
    end_date = Faker("date")
    date_claimed = Faker("date")

    class Meta:
        model = "opportunity.OpportunityClaim"


class OpportunityClaimLimitFactory(DjangoModelFactory):
    opportunity_claim = SubFactory(OpportunityClaimFactory)
    payment_unit = SubFactory(PaymentUnitFactory)
    max_visits = Faker("pyint", min_value=1, max_value=100)

    class Meta:
        model = "opportunity.OpportunityClaimLimit"


class CompletedModuleFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    user = SubFactory("commcare_connect.users.tests.factories.UserFactory")
    opportunity_access = SubFactory(OpportunityAccessFactory)
    date = Faker("date_time", tzinfo=timezone.utc)
    module = SubFactory(LearnModuleFactory, app=SelfAttribute("..opportunity.learn_app"))
    duration = Faker("time_delta")

    class Meta:
        model = "opportunity.CompletedModule"


class AssessmentFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    user = SubFactory("commcare_connect.users.tests.factories.UserFactory")
    opportunity_access = SubFactory(OpportunityAccessFactory)
    app = SubFactory(CommCareAppFactory)
    passed = True
    score = Faker("pyint", min_value=75, max_value=100)
    passing_score = Faker("pyint", min_value=1, max_value=50)
    date = Faker("date_time", tzinfo=timezone.utc)

    class Meta:
        model = "opportunity.Assessment"


class UserInviteFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    phone_number = Faker("word")
    message_sid = Faker("word")
    opportunity_access = SubFactory(OpportunityAccessFactory)

    class Meta:
        model = "opportunity.UserInvite"


class DeliverUnitFlagRulesFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    deliver_unit = SubFactory(DeliverUnitFactory)

    class Meta:
        model = "opportunity.DeliverUnitFlagRules"


class FormJsonValidationRulesFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    name = Faker("word")

    class Meta:
        model = "opportunity.FormJsonValidationRules"


class CatchmentAreaFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    opportunity_access = SubFactory(OpportunityAccessFactory)
    latitude = Faker("latitude")
    longitude = Faker("longitude")
    radius = Faker("random_int", min=500, max=2000)
    active = Faker("boolean")
    name = Faker("city")
    site_code = Faker("pystr")

    class Meta:
        model = "opportunity.CatchmentArea"


class DeliveryTypeFactory(DjangoModelFactory):
    name = Faker("name")
    description = Faker("text", max_nb_chars=200)

    class Meta:
        model = "opportunity.DeliveryType"


class PaymentFactory(DjangoModelFactory):
    opportunity_access = SubFactory(OpportunityAccessFactory)
    amount = Faker("pydecimal", min_value=1, max_value=10000)
    date_paid = Faker("date_time", tzinfo=timezone.utc)

    class Meta:
        model = "opportunity.Payment"


class PaymentInvoiceFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)
    amount = Faker("pydecimal", min_value=0, max_value=1000)
    date = Faker("date_time", tzinfo=timezone.utc)
    invoice_number = Faker("pystr")

    class Meta:
        model = "opportunity.PaymentInvoice"


class CredentialConfigurationFactory(DjangoModelFactory):
    opportunity = SubFactory(OpportunityFactory)

    class Meta:
        model = "opportunity.CredentialConfiguration"


class UserCredentialFactory(DjangoModelFactory):
    user = SubFactory("commcare_connect.users.tests.factories.UserFactory")
    opportunity = SubFactory(OpportunityFactory)
    delivery_type = SubFactory(DeliveryTypeFactory)

    class Meta:
        model = "users.UserCredential"
