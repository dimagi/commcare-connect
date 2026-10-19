from datetime import timedelta

from django.utils.timezone import now
from factory import Faker, LazyFunction, SubFactory
from factory.django import DjangoModelFactory

from commcare_connect.program.tests.factories import _get_default_currency
from commcare_connect.solicitation.models import Solicitation, SolicitationApplication, SolicitationInvitation
from commcare_connect.users.tests.factories import OrganizationFactory


class SolicitationFactory(DjangoModelFactory):
    title = Faker("sentence", nb_words=4)
    type = Solicitation.Type.RFP
    description = Faker("text", max_nb_chars=200)
    currency = LazyFunction(_get_default_currency)
    application_deadline = LazyFunction(lambda: now() + timedelta(days=30))
    contact_email = Faker("email")
    organization = SubFactory(OrganizationFactory)

    class Meta:
        model = Solicitation


class SolicitationApplicationFactory(DjangoModelFactory):
    solicitation = SubFactory(SolicitationFactory)
    organization = SubFactory(OrganizationFactory)
    submitter_name = Faker("name")
    submitter_email = Faker("email")

    class Meta:
        model = SolicitationApplication


class SolicitationInvitationFactory(DjangoModelFactory):
    solicitation = SubFactory(SolicitationFactory)
    organization = SubFactory(OrganizationFactory)

    class Meta:
        model = SolicitationInvitation
