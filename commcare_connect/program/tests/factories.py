from factory import Faker, LazyFunction, SubFactory
from factory.django import DjangoModelFactory

from commcare_connect.opportunity.models import Country, Currency
from commcare_connect.opportunity.tests.factories import DeliveryTypeFactory, OpportunityFactory
from commcare_connect.program.models import ManagedOpportunity, Program, ProgramApplication
from commcare_connect.users.tests.factories import OrganizationFactory


def _get_default_currency():
    return Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar"})[0]


def _get_default_country():
    currency = _get_default_currency()
    return Country.objects.get_or_create(
        code="USA", defaults={"name": "United States of America", "currency": currency}
    )[0]


class ProgramFactory(DjangoModelFactory):
    name = Faker("name")
    program_id = Faker("uuid4")
    description = Faker("text", max_nb_chars=200)
    delivery_type = SubFactory(DeliveryTypeFactory)
    budget = Faker("random_int", min=1000, max=100000)
    currency_fk = LazyFunction(_get_default_currency)
    country = LazyFunction(_get_default_country)
    start_date = Faker("date_this_decade", before_today=True, after_today=False)
    end_date = Faker("date_this_decade", before_today=False, after_today=True)
    organization = SubFactory(OrganizationFactory)

    class Meta:
        model = Program


class ManagedOpportunityFactory(OpportunityFactory):
    program = SubFactory(ProgramFactory)
    org_pay_per_visit = Faker("random_int", min=500, max=1000)

    class Meta:
        model = ManagedOpportunity


class ProgramApplicationFactory(DjangoModelFactory):
    program = SubFactory(ProgramFactory)
    organization = SubFactory(OrganizationFactory)
    date_created = Faker("date_this_decade", before_today=True, after_today=False)

    class Meta:
        model = ProgramApplication
