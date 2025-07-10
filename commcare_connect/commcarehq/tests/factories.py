from factory import Faker, SubFactory
from factory.django import DjangoModelFactory


class OauthApplicationFactory(DjangoModelFactory):
    name = Faker("name")
    client_id = Faker("uuid4")
    client_secret = Faker("uuid4")

    class Meta:
        model = "oauth2_provider.Application"


class HQServerFactory(DjangoModelFactory):
    name = Faker("name")
    url = Faker("url")
    oauth_application = SubFactory(OauthApplicationFactory)

    class Meta:
        model = "commcarehq.HQServer"
        django_get_or_create = ["url"]
