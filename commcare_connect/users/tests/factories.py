from django.contrib.auth import get_user_model
from factory import Faker, RelatedFactory, SubFactory
from factory.django import DjangoModelFactory, Password

from commcare_connect.commcarehq.tests.factories import HQServerFactory
from commcare_connect.organization.models import Organization, UserOrganizationMembership
from commcare_connect.users.models import ConnectIDUserLink


class UserFactory(DjangoModelFactory):
    email = Faker("email")
    name = Faker("name")
    password = Password(
        Faker(
            "password",
            length=42,
            special_chars=True,
            digits=True,
            upper_case=True,
            lower_case=True,
        )
    )

    class Meta:
        model = get_user_model()
        django_get_or_create = ["email"]


class ConnectIdUserLinkFactory(DjangoModelFactory):
    user = SubFactory(UserFactory)
    commcare_username = Faker("word")
    hq_server = SubFactory(HQServerFactory)

    class Meta:
        model = ConnectIDUserLink
        django_get_or_create = ["commcare_username"]


class MobileUserFactory(DjangoModelFactory):
    username = Faker("word")
    name = Faker("name")

    class Meta:
        model = get_user_model()
        django_get_or_create = ["username"]


class OrganizationFactory(DjangoModelFactory):
    name = Faker("company")

    class Meta:
        model = Organization


class MembershipFactory(DjangoModelFactory):
    class Meta:
        model = UserOrganizationMembership

    user = SubFactory(UserFactory)
    organization = SubFactory(OrganizationFactory)
    role = "admin"


class OrgWithUsersFactory(OrganizationFactory):
    admin = RelatedFactory(MembershipFactory, "organization", role="admin")
    member = RelatedFactory(MembershipFactory, "organization", role="member")

    class Meta:
        skip_postgeneration_save = True


class ProgramManagerOrganisationFactory(DjangoModelFactory):
    name = Faker("company")
    program_manager = True

    class Meta:
        model = Organization


class ProgramManagerOrgWithUsersFactory(ProgramManagerOrganisationFactory):
    admin = RelatedFactory(MembershipFactory, "organization", role="admin")
    member = RelatedFactory(MembershipFactory, "organization", role="member")

    class Meta:
        skip_postgeneration_save = True
