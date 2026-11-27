from datetime import timedelta

from django.utils.timezone import now
from factory import Faker, LazyAttribute, LazyFunction, Sequence, SubFactory, Trait
from factory.django import DjangoModelFactory

from commcare_connect.program.tests.factories import _get_default_currency
from commcare_connect.solicitation.models import (
    Solicitation,
    SolicitationApplication,
    SolicitationEvaluationCriterion,
    SolicitationInvitation,
    SolicitationQuestion,
    SolicitationReviewer,
)
from commcare_connect.users.tests.factories import MembershipFactory, OrganizationFactory


class SolicitationFactory(DjangoModelFactory):
    title = Faker("sentence", nb_words=4)
    type = Solicitation.Type.RFP
    description = Faker("text", max_nb_chars=200)
    currency = LazyFunction(_get_default_currency)
    application_deadline = LazyFunction(lambda: now() + timedelta(days=30))
    contact_email = Faker("email")
    organization = SubFactory(OrganizationFactory)

    class Params:
        # Past its deadline; with status=ACTIVE, not yet closed by update_solicitation_statuses.
        deadline_passed = Trait(application_deadline=LazyFunction(lambda: now() - timedelta(days=1)))
        # Goes live tomorrow, well before the default deadline.
        scheduled = Trait(
            status=Solicitation.Status.SCHEDULED, go_live_date=LazyFunction(lambda: now() + timedelta(days=1))
        )

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


class SolicitationReviewerFactory(DjangoModelFactory):
    solicitation = SubFactory(SolicitationFactory)
    # Reviewers are picked from the posting organization's members.
    user = LazyAttribute(lambda o: MembershipFactory(organization=o.solicitation.organization).user)

    class Meta:
        model = SolicitationReviewer


class SolicitationEvaluationCriterionFactory(DjangoModelFactory):
    solicitation = SubFactory(SolicitationFactory)
    label = Sequence(lambda n: f"Criterion {n}")
    weight = 50

    class Meta:
        model = SolicitationEvaluationCriterion


class SolicitationQuestionFactory(DjangoModelFactory):
    solicitation = SubFactory(SolicitationFactory)
    text = Faker("sentence")
    question_type = SolicitationQuestion.QuestionType.TEXT

    class Meta:
        model = SolicitationQuestion
