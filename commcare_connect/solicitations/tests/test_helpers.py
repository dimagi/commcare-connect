import json

import pytest
from django.db.models import Count

from commcare_connect.solicitations.helpers import (
    calculate_response_permissions,
    get_solicitation_dashboard_statistics,
    process_solicitation_questions,
)
from commcare_connect.solicitations.models import Solicitation, SolicitationResponse
from commcare_connect.solicitations.tests.factories import SolicitationFactory, SolicitationResponseFactory
from commcare_connect.users.tests.factories import UserFactory

# Removed unused imports


@pytest.mark.django_db
class TestCalculateResponsePermissions:
    """Test the complex business logic for response permissions"""

    @pytest.fixture(autouse=True)
    def setup(self, user, organization):
        self.user = user
        self.org = organization
        # Create membership relationship
        self.user.memberships.create(organization=self.org)

    def test_can_respond_to_active_solicitation(self):
        """Test that users can respond to active solicitations"""
        solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE)

        result = calculate_response_permissions(self.user, solicitation)

        assert result["can_respond"] is True
        assert result["error_message"] is None
        assert result["redirect_needed"] is False

    def test_cannot_respond_to_draft_solicitation(self):
        """Test that users cannot respond to draft solicitations"""
        solicitation = SolicitationFactory(status=Solicitation.Status.DRAFT)

        result = calculate_response_permissions(self.user, solicitation)

        assert result["can_respond"] is False
        assert "not currently accepting responses" in result["error_message"]
        assert result["redirect_needed"] is True

    def test_cannot_respond_to_closed_solicitation(self):
        """Test that users cannot respond to closed solicitations"""
        solicitation = SolicitationFactory(status=Solicitation.Status.CLOSED)

        result = calculate_response_permissions(self.user, solicitation)

        assert result["can_respond"] is False
        assert "not currently accepting responses" in result["error_message"]
        assert result["redirect_needed"] is True

    def test_user_without_organization_cannot_respond(self):
        """Test that users without organization membership cannot respond"""
        user_no_org = UserFactory()
        solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE)

        result = calculate_response_permissions(user_no_org, solicitation)

        assert result["can_respond"] is False
        assert result["error_message"] == "organization_required"

    def test_organization_with_submitted_response_cannot_respond_again(self):
        """Test that organizations with submitted responses cannot respond again"""
        solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE)
        SolicitationResponseFactory(
            solicitation=solicitation, organization=self.org, status=SolicitationResponse.Status.SUBMITTED
        )

        result = calculate_response_permissions(self.user, solicitation)

        assert result["can_respond"] is False
        assert "already submitted a response" in result["error_message"]
        assert result["redirect_needed"] is True
        assert result["existing_submitted_response"] is not None

    def test_organization_with_draft_can_continue_editing(self):
        """Test that organizations with draft responses can continue editing"""
        solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE)
        draft_response = SolicitationResponseFactory(
            solicitation=solicitation, organization=self.org, status=SolicitationResponse.Status.DRAFT
        )

        result = calculate_response_permissions(self.user, solicitation)

        assert result["can_respond"] is True
        assert result["existing_draft"] == draft_response


@pytest.mark.django_db
class TestProcessSolicitationQuestions:
    """Test question processing logic"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.solicitation = SolicitationFactory()

    def test_process_valid_questions_data(self):
        """Test processing valid questions JSON data"""
        questions_data = [
            {
                "question_text": "What is your experience?",
                "question_type": "textarea",
                "is_required": True,
                "order": 1,
            },
            {
                "question_text": "How many staff do you have?",
                "question_type": "number",
                "is_required": False,
                "order": 2,
            },
        ]
        questions_json = json.dumps(questions_data)

        success, error = process_solicitation_questions(questions_json, self.solicitation)

        assert success is True
        assert error is None
        assert self.solicitation.questions.count() == 2

        question1 = self.solicitation.questions.get(order=1)
        assert question1.question_text == "What is your experience?"
        assert question1.question_type == "textarea"
        assert question1.is_required is True

    def test_process_empty_questions_data(self):
        """Test that empty questions data is handled gracefully"""
        success, error = process_solicitation_questions("", self.solicitation)

        assert success is True
        assert error is None
        assert self.solicitation.questions.count() == 0

    def test_process_invalid_json(self):
        """Test handling of malformed JSON"""
        invalid_json = '{"question_text": "incomplete json'

        success, error = process_solicitation_questions(invalid_json, self.solicitation)

        assert success is False
        assert "Malformed JSON in questions data" in error

    def test_process_questions_with_missing_fields(self):
        """Test processing questions with missing required fields"""
        questions_data = [{"question_text": "", "question_type": "textarea"}]  # Empty text
        questions_json = json.dumps(questions_data)

        success, error = process_solicitation_questions(questions_json, self.solicitation)

        # Should still succeed but create question with empty text
        assert success is True
        assert self.solicitation.questions.count() == 1

        question = self.solicitation.questions.first()
        assert question.question_text == ""
        assert question.question_type == "textarea"
        assert question.is_required is True  # Default value


@pytest.mark.django_db
class TestGetSolicitationDashboardStatistics:
    """Test dashboard statistics calculation"""

    def test_statistics_with_no_solicitations(self):
        """Test statistics calculation with empty queryset"""
        empty_queryset = Solicitation.objects.none()

        stats = get_solicitation_dashboard_statistics(empty_queryset)

        assert stats["total_solicitations"] == 0
        assert stats["total_responses"] == 0
        assert stats["draft_count"] == 0
        assert stats["active_count"] == 0
        assert stats["closed_count"] == 0

    def test_statistics_with_mixed_solicitations(self):
        """Test statistics with various solicitation types and statuses"""
        # Create solicitations with different statuses
        SolicitationFactory(status=Solicitation.Status.DRAFT)
        active_sol = SolicitationFactory(status=Solicitation.Status.ACTIVE)
        closed_sol = SolicitationFactory(status=Solicitation.Status.CLOSED)

        # Add some responses
        SolicitationResponseFactory(solicitation=active_sol)
        SolicitationResponseFactory(solicitation=active_sol)
        SolicitationResponseFactory(solicitation=closed_sol)

        queryset = Solicitation.objects.all().annotate(total_responses=Count("responses"))
        stats = get_solicitation_dashboard_statistics(queryset)

        assert stats["total_solicitations"] == 3
        assert stats["total_responses"] == 3
        assert stats["draft_count"] == 1
        assert stats["active_count"] == 1
        assert stats["closed_count"] == 1
