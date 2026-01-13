from datetime import date, timedelta

import pytest

from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.solicitations.forms import SolicitationForm, SolicitationResponseForm
from commcare_connect.solicitations.models import Solicitation, SolicitationQuestion
from commcare_connect.solicitations.tests.factories import SolicitationWithQuestionsFactory
from commcare_connect.users.tests.factories import UserFactory


class BaseSolicitationFormTest:
    """Base class for solicitation form tests with common fixtures"""

    @pytest.fixture(autouse=True)
    def setup(self, user, organization):
        self.user = user
        self.org = organization
        # Create membership relationship
        self.user.memberships.create(organization=self.org)
        self.program = ProgramFactory()

    def _get_solicitation_form_data(self, **overrides):
        """Helper method to generate solicitation form data"""
        base_data = {
            "title": "Test Solicitation",
            "description": "A test solicitation description",
            "solicitation_type": Solicitation.Type.EOI,
            "expected_start_date": date.today() + timedelta(days=60),
            "expected_end_date": date.today() + timedelta(days=365),
            "application_deadline": date.today() + timedelta(days=30),
            "status": Solicitation.Status.DRAFT,
            "is_publicly_listed": True,
        }
        base_data.update(overrides)
        return base_data


@pytest.mark.django_db
class TestSolicitationForm(BaseSolicitationFormTest):
    def test_valid_form_creation(self):
        """Test that a valid solicitation form can be created and saved"""
        form_data = self._get_solicitation_form_data()

        form = SolicitationForm(data=form_data, program=self.program)

        assert form.is_valid()
        assert len(form.errors) == 0

        solicitation = form.save(commit=False)
        solicitation.created_by = self.user.email
        solicitation.save()

        assert solicitation.title == "Test Solicitation"
        assert solicitation.program == self.program
        assert solicitation.target_population == "To be determined"  # Default value
        assert solicitation.scope_of_work == "Details will be provided through application questions"  # Default value

    def test_form_validation_date_ranges(self):
        """Test that form validates date ranges correctly"""
        # Test end date before start date
        form_data = self._get_solicitation_form_data(
            expected_start_date=date.today() + timedelta(days=60),
            expected_end_date=date.today() + timedelta(days=30),  # Before start date
            application_deadline=date.today() + timedelta(days=15),
        )

        form = SolicitationForm(data=form_data, program=self.program)

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "Expected end date must be after the start date." in str(form.errors)

    def test_form_validation_deadline_after_start(self):
        """Test that form validates deadline is before start date"""
        # Test deadline after start date
        form_data = self._get_solicitation_form_data(
            expected_start_date=date.today() + timedelta(days=30),
            application_deadline=date.today() + timedelta(days=60),  # After start date
        )

        form = SolicitationForm(data=form_data, program=self.program)

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "Application deadline must be before the expected start date." in str(form.errors)


@pytest.mark.django_db
class TestSolicitationResponseForm(BaseSolicitationFormTest):
    @pytest.fixture(autouse=True)
    def response_form_setup(self):
        """Additional setup for response form tests"""
        self.solicitation_with_questions = SolicitationWithQuestionsFactory()
        self.questions = list(self.solicitation_with_questions.questions.all())

    def test_form_creates_dynamic_fields(self):
        """Test that form creates fields based on solicitation questions"""
        form = SolicitationResponseForm(solicitation=self.solicitation_with_questions, user=self.user)

        # Check that dynamic fields were created using actual question IDs
        for question in self.questions:
            field_name = f"question_{question.id}"
            assert field_name in form.fields

            field = form.fields[field_name]
            assert field.label == question.question_text

            # File fields are always optional regardless of question.is_required
            if question.question_type == "file":
                assert field.required is False
            else:
                assert field.required == question.is_required

    def test_form_validation_requires_organization_membership(self):
        """Test that form requires user to have organization membership"""
        user_no_org = UserFactory()  # No organization membership

        form_data = {}
        form = SolicitationResponseForm(
            data=form_data, solicitation=self.solicitation_with_questions, user=user_no_org
        )

        assert not form.is_valid()
        assert len(form.errors) >= 1
        assert "You must be a member of an organization to submit responses." in str(form.errors)

    def test_draft_save_makes_fields_optional(self):
        """Test that draft saves make all fields optional"""
        # Get a required question from our fixture
        required_question = next(q for q in self.questions if q.is_required)

        # Test with is_draft_save=True
        form_data = {
            "action": "save_draft",
            f"question_{required_question.id}": "",  # Empty required field
        }

        form = SolicitationResponseForm(
            data=form_data, solicitation=self.solicitation_with_questions, user=self.user, is_draft_save=True
        )

        # Should be valid because it's a draft save
        assert form.is_valid()

    def test_submission_requires_required_fields(self):
        """Test that final submission validates required fields"""
        # Get a required question from our fixture
        required_question = next(q for q in self.questions if q.is_required)

        # Test final submission with empty required field
        form_data = {
            "action": "submit",
            f"question_{required_question.id}": "",  # Empty required field
        }

        form = SolicitationResponseForm(
            data=form_data, solicitation=self.solicitation_with_questions, user=self.user, is_draft_save=False
        )

        # Should be invalid because required field is empty
        assert not form.is_valid()
        assert len(form.errors) >= 1
        assert f"question_{required_question.id}" in form.errors

    def test_form_save_creates_response_with_correct_data(self):
        """Test that form save creates response with correct organization and user"""
        # Use a non-required question for this test
        test_question = next((q for q in self.questions if not q.is_required), self.questions[0])

        form_data = {
            f"question_{test_question.id}": "Test answer",
        }

        # Add all required fields to make form valid
        for question in self.questions:
            if question.is_required and question != test_question:
                if question.question_type == SolicitationQuestion.Type.NUMBER:
                    form_data[f"question_{question.id}"] = "10"
                else:
                    form_data[f"question_{question.id}"] = "Required answer"

        form = SolicitationResponseForm(data=form_data, solicitation=self.solicitation_with_questions, user=self.user)

        assert form.is_valid()
        assert len(form.errors) == 0

        response = form.save()

        assert response.solicitation == self.solicitation_with_questions
        assert response.organization == self.org
        assert response.submitted_by == self.user
        # Check that our test answer is in the responses
        assert test_question.question_text in response.responses
        assert response.responses[test_question.question_text] == "Test answer"
