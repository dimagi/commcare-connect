# Removed unused imports

import pytest
from django.db import IntegrityError

from commcare_connect.solicitations.models import Solicitation, SolicitationQuestion, SolicitationResponse
from commcare_connect.solicitations.tests.factories import SolicitationFactory, SolicitationResponseFactory


class BaseSolicitationModelTest:
    """Base class for solicitation model tests with common fixtures"""

    @pytest.fixture(autouse=True)
    def setup(self, user, organization):
        self.solicitation = SolicitationFactory()
        self.user = user
        self.org = organization
        # Create membership relationship
        self.user.memberships.create(organization=self.org)


@pytest.mark.django_db
class TestSolicitation(BaseSolicitationModelTest):
    def test_can_accept_responses(self):
        """Test that solicitations can only accept responses when active"""
        # Active solicitation
        active_solicitation = SolicitationFactory(
            status=Solicitation.Status.ACTIVE,
        )

        # Draft solicitation
        draft_solicitation = SolicitationFactory(
            status=Solicitation.Status.DRAFT,
        )

        # Closed solicitation
        closed_solicitation = SolicitationFactory(
            status=Solicitation.Status.CLOSED,
        )

        assert active_solicitation.can_accept_responses
        assert not draft_solicitation.can_accept_responses
        assert not closed_solicitation.can_accept_responses


@pytest.mark.django_db
class TestSolicitationQuestion(BaseSolicitationModelTest):
    def test_question_ordering(self):
        """Test that questions are ordered by their order field"""
        # Create questions in reverse order
        question3 = SolicitationQuestion.objects.create(
            solicitation=self.solicitation, question_text="Third question", order=3
        )
        question1 = SolicitationQuestion.objects.create(
            solicitation=self.solicitation, question_text="First question", order=1
        )
        question2 = SolicitationQuestion.objects.create(
            solicitation=self.solicitation, question_text="Second question", order=2
        )

        # Should be ordered by order field
        questions = list(self.solicitation.questions.all())
        assert questions[0] == question1
        assert questions[1] == question2
        assert questions[2] == question3


@pytest.mark.django_db
class TestSolicitationResponse(BaseSolicitationModelTest):
    def test_response_defaults_to_draft_status(self):
        """Test that newly created responses default to DRAFT status"""
        response = SolicitationResponse.objects.create(
            solicitation=self.solicitation,
            organization=self.org,
            submitted_by=self.user,
            responses={"question_1": "Our response"},
        )

        assert response.status == SolicitationResponse.Status.DRAFT

    def test_unique_response_per_org(self):
        """Test that organizations can only submit one response per solicitation"""
        # First response should succeed
        SolicitationResponse.objects.create(
            solicitation=self.solicitation,
            organization=self.org,
            submitted_by=self.user,
            responses={"question_1": "First response"},
        )

        # Second response from same org should fail
        with pytest.raises(IntegrityError):
            SolicitationResponse.objects.create(
                solicitation=self.solicitation,
                organization=self.org,
                submitted_by=self.user,
                responses={"question_1": "Second response"},
            )

    def test_submit_method(self):
        """Test that submit() method changes status from DRAFT to SUBMITTED"""
        # Create a draft response
        response = SolicitationResponseFactory(
            solicitation=self.solicitation,
            organization=self.org,
            submitted_by=self.user,
            status=SolicitationResponse.Status.DRAFT,
        )
        assert response.is_draft
        assert not response.is_submitted

        # Submit it
        response.submit()
        response.refresh_from_db()

        assert not response.is_draft
        assert response.is_submitted
        assert response.status == SolicitationResponse.Status.SUBMITTED

    def test_submit_method_only_works_on_drafts(self):
        """Test that submit() method only works on draft responses"""
        # Create an already submitted response
        response = SolicitationResponseFactory(
            solicitation=self.solicitation,
            organization=self.org,
            submitted_by=self.user,
            status=SolicitationResponse.Status.SUBMITTED,
        )
        original_status = response.status

        # Try to submit it again - should not change
        response.submit()
        response.refresh_from_db()

        assert response.status == original_status
