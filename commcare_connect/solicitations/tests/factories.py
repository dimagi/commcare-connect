import random
from datetime import timedelta

import factory
from django.utils import timezone
from faker import Faker

from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.solicitations.models import (
    Solicitation,
    SolicitationQuestion,
    SolicitationResponse,
    SolicitationReview,
)
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory

fake = Faker()

# Template data moved from management command
SOLICITATION_TEMPLATES = {
    "eoi": {
        "title_suffix": "Expression of Interest",
        "description": (
            "We are seeking qualified local organizations to partner with us in delivering {focus} "
            "services to underserved communities. This Expression of Interest will help us identify "
            "potential partners for our upcoming program."
        ),
        "questions": [
            "Describe your organization's experience working with {target_population}",
            "What is your organization's approach to community engagement?",
            "How many field workers can your organization deploy?",
            "Upload your organization's registration certificate",
            "What geographical areas does your organization currently serve?",
        ],
    },
    "rfp": {
        "title_suffix": "Request for Proposals",
        "description": (
            "Following our Expression of Interest process, we invite selected organizations to submit "
            "detailed proposals for implementing {focus} interventions. This RFP outlines specific "
            "requirements and deliverables."
        ),
        "questions": [
            "Provide a detailed implementation timeline",
            "Submit your detailed budget breakdown",
            "Describe your quality assurance processes",
            "Upload supporting documentation (licenses, certifications, etc.)",
            "How will you measure and report on program outcomes?",
            "What risks do you anticipate and how will you mitigate them?",
        ],
    },
}

FOCUS_AREAS = ["maternal health", "child nutrition", "vaccination campaigns", "community health screening"]
TARGET_POPULATIONS = ["pregnant women", "children under 5", "rural communities", "vulnerable families"]


def get_question_type_from_text(question_text):
    """Smart question type detection from text content"""
    text_lower = question_text.lower()
    if "upload" in text_lower or "submit" in text_lower:
        return SolicitationQuestion.Type.FILE
    elif "how many" in text_lower or "number" in text_lower:
        return SolicitationQuestion.Type.NUMBER
    else:
        return SolicitationQuestion.Type.TEXTAREA


class SolicitationFactory(factory.django.DjangoModelFactory):
    """Factory that creates realistic solicitations with proper templates"""

    class Meta:
        model = Solicitation

    solicitation_type = factory.Iterator([Solicitation.Type.EOI, Solicitation.Type.RFP])
    status = Solicitation.Status.ACTIVE
    is_publicly_listed = True
    program = factory.SubFactory(ProgramFactory)
    created_by = factory.Faker("email")
    modified_by = factory.Faker("email")
    application_deadline = factory.LazyFunction(lambda: timezone.now().date() + timedelta(days=30))
    expected_start_date = factory.LazyFunction(lambda: timezone.now().date() + timedelta(days=60))
    expected_end_date = factory.LazyFunction(lambda: timezone.now().date() + timedelta(days=365))

    @factory.lazy_attribute
    def title(self):
        focus_area = random.choice(FOCUS_AREAS)
        template = SOLICITATION_TEMPLATES[self.solicitation_type]
        return f"Sample {focus_area.title()} - {template['title_suffix']}"

    @factory.lazy_attribute
    def description(self):
        focus_area = random.choice(FOCUS_AREAS)
        target_population = random.choice(TARGET_POPULATIONS)
        template = SOLICITATION_TEMPLATES[self.solicitation_type]
        return template["description"].format(focus=focus_area, target_population=target_population)

    @factory.lazy_attribute
    def target_population(self):
        return random.choice(TARGET_POPULATIONS)

    @factory.lazy_attribute
    def scope_of_work(self):
        focus_area = random.choice(FOCUS_AREAS)
        return (
            f"Implement {focus_area} interventions including community outreach, "
            f"service delivery, and data collection"
        )

    @factory.lazy_attribute
    def estimated_scale(self):
        target_population = random.choice(TARGET_POPULATIONS)
        return f"{fake.random_int(min=1000, max=50000)} {target_population}"


class EOIFactory(SolicitationFactory):
    """EOI with proper template"""

    solicitation_type = Solicitation.Type.EOI


class RFPFactory(SolicitationFactory):
    """RFP with proper template"""

    solicitation_type = Solicitation.Type.RFP


class SolicitationQuestionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SolicitationQuestion

    solicitation = factory.SubFactory(SolicitationFactory)
    question_text = factory.Faker("sentence", nb_words=8)
    question_type = factory.Iterator(
        [
            SolicitationQuestion.Type.TEXT,
            SolicitationQuestion.Type.TEXTAREA,
            SolicitationQuestion.Type.NUMBER,
            SolicitationQuestion.Type.FILE,
        ]
    )
    is_required = True
    order = factory.Sequence(lambda n: n + 1)


class SolicitationResponseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SolicitationResponse

    solicitation = factory.SubFactory(SolicitationFactory)
    organization = factory.SubFactory(OrganizationFactory)
    submitted_by = factory.SubFactory(UserFactory)
    responses = factory.Dict(
        {
            "question_1": factory.Faker("text", max_nb_chars=200),
            "question_2": factory.Faker("text", max_nb_chars=200),
            "question_3": factory.Faker("random_int", min=1, max=100),
        }
    )
    status = SolicitationResponse.Status.SUBMITTED


class SolicitationWithQuestionsFactory(SolicitationFactory):
    """Factory that creates solicitation with template-based questions"""

    @factory.post_generation
    def questions(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            # Use provided questions
            for question_data in extracted:
                SolicitationQuestionFactory(solicitation=self, **question_data)
        else:
            # Use template questions
            focus_area = random.choice(FOCUS_AREAS)
            target_population = random.choice(TARGET_POPULATIONS)
            template = SOLICITATION_TEMPLATES[self.solicitation_type]
            for idx, question_text in enumerate(template["questions"]):
                formatted_text = question_text.format(target_population=target_population, focus=focus_area)
                SolicitationQuestionFactory(
                    solicitation=self,
                    question_text=formatted_text,
                    question_type=get_question_type_from_text(formatted_text),
                    order=idx + 1,
                    is_required=True,
                )


class SolicitationWithResponsesFactory(SolicitationWithQuestionsFactory):
    """Factory that creates solicitation with realistic responses"""

    @factory.post_generation
    def responses(self, create, extracted, num_responses=3, **kwargs):
        if not create:
            return

        # Create responding organizations and responses
        for i in range(num_responses):
            org = OrganizationFactory(name=f"Sample Responding Organization {i+1}", slug=f"sample-responder-{i+1}")
            user = UserFactory()

            # Generate responses to all questions
            question_responses = {}
            for question in self.questions.all():
                if question.question_type == SolicitationQuestion.Type.FILE:
                    question_responses[f"question_{question.id}"] = "sample_document.pdf"
                else:
                    question_responses[f"question_{question.id}"] = fake.text(max_nb_chars=300)

            SolicitationResponseFactory(
                solicitation=self,
                organization=org,
                submitted_by=user,
                responses=question_responses,
                status=random.choice(
                    [
                        SolicitationResponse.Status.SUBMITTED,
                    ]
                ),
            )


class SolicitationReviewFactory(factory.django.DjangoModelFactory):
    """Factory for creating solicitation reviews"""

    class Meta:
        model = SolicitationReview

    response = factory.SubFactory(SolicitationResponseFactory)
    reviewer = factory.SubFactory(UserFactory)
    score = factory.Faker("random_int", min=1, max=100)
    tags = factory.Faker("words", nb=3)
    notes = factory.Faker("text", max_nb_chars=200)
    recommendation = factory.Iterator(
        [
            SolicitationReview.Recommendation.RECOMMENDED,
            SolicitationReview.Recommendation.NOT_RECOMMENDED,
            SolicitationReview.Recommendation.NEUTRAL,
        ]
    )
