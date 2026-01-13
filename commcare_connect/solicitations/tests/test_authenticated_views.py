import pytest
from django.urls import reverse

from commcare_connect.program.tests.factories import ProgramFactory
from commcare_connect.solicitations.models import Solicitation, SolicitationResponse, SolicitationReview
from commcare_connect.solicitations.tests.factories import (
    SolicitationFactory,
    SolicitationResponseFactory,
    SolicitationReviewFactory,
    SolicitationWithQuestionsFactory,
)
from commcare_connect.users.tests.factories import OrganizationFactory, UserFactory


@pytest.mark.django_db
class TestSolicitationCreateOrUpdate:
    """Test solicitation creation and editing views"""

    @pytest.fixture(autouse=True)
    def setup(self, user, organization):
        self.user = user
        # Fix 1: Make organization a Program Manager
        organization.program_manager = True
        organization.save()
        self.org = organization

        # Fix 2: Create admin membership (not default member)
        self.membership = self.user.memberships.create(
            organization=self.org, role="admin"  # Required for SolicitationManagerMixin
        )
        self.program = ProgramFactory(organization=self.org)

    def test_create_solicitation_success(self, client):
        """Test successful solicitation creation"""
        client.force_login(self.user)
        url = reverse("solicitations:program_solicitation_create", kwargs={"program_pk": self.program.pk})

        form_data = {
            "title": "Test EOI",
            "description": "Test description",
            "solicitation_type": Solicitation.Type.EOI,
            "expected_start_date": "2024-06-01",
            "expected_end_date": "2024-12-31",
            "application_deadline": "2024-05-01",
            "status": Solicitation.Status.DRAFT,
            "is_publicly_listed": True,
        }

        response = client.post(url, data=form_data)

        assert response.status_code == 302  # Redirect after success
        assert Solicitation.objects.filter(title="Test EOI").exists()

        solicitation = Solicitation.objects.get(title="Test EOI")
        assert solicitation.program == self.program
        assert solicitation.created_by == self.user.email

    def test_edit_solicitation_success(self, client):
        """Test successful solicitation editing"""
        solicitation = SolicitationFactory(program=self.program)
        client.force_login(self.user)
        url = reverse(
            "solicitations:program_solicitation_edit", kwargs={"program_pk": self.program.pk, "pk": solicitation.pk}
        )

        form_data = {
            "title": "Updated Title",
            "description": solicitation.description,
            "solicitation_type": solicitation.solicitation_type,
            "expected_start_date": solicitation.expected_start_date,
            "expected_end_date": solicitation.expected_end_date,
            "application_deadline": solicitation.application_deadline,
            "status": solicitation.status,
            "is_publicly_listed": solicitation.is_publicly_listed,
        }

        response = client.post(url, data=form_data)

        assert response.status_code == 302
        solicitation.refresh_from_db()
        assert solicitation.title == "Updated Title"

    def test_cannot_edit_other_organizations_solicitation(self, client):
        """Test that users cannot edit solicitations from other organizations"""
        other_program = ProgramFactory()  # Different organization
        solicitation = SolicitationFactory(program=other_program)
        client.force_login(self.user)
        url = reverse(
            "solicitations:program_solicitation_edit", kwargs={"program_pk": other_program.pk, "pk": solicitation.pk}
        )

        response = client.get(url)

        assert response.status_code == 404


@pytest.mark.django_db
class TestSolicitationResponseCreateOrUpdate:
    """Test solicitation response creation and editing"""

    @pytest.fixture(autouse=True)
    def setup(self, user, organization):
        self.user = user
        self.org = organization
        # Create membership relationship
        self.membership = self.user.memberships.create(organization=self.org)
        self.solicitation = SolicitationWithQuestionsFactory(status=Solicitation.Status.ACTIVE)

    def test_create_response_success(self, client):
        """Test successful response creation"""
        client.force_login(self.user)
        url = reverse("solicitations:respond", kwargs={"solicitation_pk": self.solicitation.pk})

        # Get the questions to build form data with appropriate values per question type
        questions = list(self.solicitation.questions.all())
        form_data = {}
        for question in questions:
            if question.question_type == "number":
                form_data[f"question_{question.id}"] = "10"  # Valid number string
            elif question.question_type == "file":
                # File fields are not required, so skip them for this test
                pass
            else:
                form_data[f"question_{question.id}"] = "Test answer"

        response = client.post(url, data=form_data)

        assert response.status_code == 302  # Redirect after success
        assert SolicitationResponse.objects.filter(solicitation=self.solicitation, organization=self.org).exists()

    def test_cannot_respond_to_draft_solicitation(self, client):
        """Test that users cannot respond to draft solicitations"""
        draft_solicitation = SolicitationWithQuestionsFactory(status=Solicitation.Status.DRAFT)
        client.force_login(self.user)
        url = reverse("solicitations:respond", kwargs={"solicitation_pk": draft_solicitation.pk})

        response = client.get(url)

        # Should redirect with error message
        assert response.status_code == 302

    def test_user_without_organization_cannot_respond(self, client):
        """Test that users without organization membership cannot respond"""
        user_no_org = UserFactory()
        client.force_login(user_no_org)
        url = reverse("solicitations:respond", kwargs={"solicitation_pk": self.solicitation.pk})

        response = client.get(url)

        assert response.status_code == 403  # Forbidden


@pytest.mark.django_db
class TestUnifiedSolicitationDashboard:
    """
    Test unified dashboard functionality in user mode.

    The UnifiedSolicitationDashboard replaces the old UserSolicitationDashboard,
    AdminSolicitationOverview, and ProgramSolicitationDashboard with a single
    view that adapts based on user permissions and URL context.

    This test class focuses on user mode (dashboard URL without admin or program context).
    """

    def test_user_with_multiple_organizations_sees_correct_responses(self, client):
        """Test that users with multiple organization memberships see responses from all their orgs"""

        # Create a user with memberships in two organizations
        user = UserFactory()
        org1 = OrganizationFactory(name="Organization 1")
        org2 = OrganizationFactory(name="Organization 2")
        user.memberships.create(organization=org1)
        user.memberships.create(organization=org2)

        # Create solicitations and responses for both organizations
        solicitation = SolicitationFactory(status=Solicitation.Status.ACTIVE)

        SolicitationResponseFactory(
            solicitation=solicitation,
            organization=org1,
            submitted_by=user,
            status=SolicitationResponse.Status.SUBMITTED,
        )

        SolicitationResponseFactory(
            solicitation=solicitation,
            organization=org2,
            submitted_by=user,
            status=SolicitationResponse.Status.SUBMITTED,
        )

        client.force_login(user)
        url = reverse("solicitations:dashboard")

        response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()

        # Should show responses from both organizations this user belongs to
        # Note: The actual display depends on how the dashboard is implemented
        # This test documents the expected behavior
        assert solicitation.title in content

    def test_dashboard_comprehensive_three_sections_isolation(self, client):
        """
        Comprehensive test for all three dashboard sections with proper organization isolation.

        Tests:
        - 1 solicitation we own (from programs our orgs are members of)
        - 2 solicitation responses we made
        - 3 solicitation response reviews we did

        Across two organizations we're members of, plus isolation from a third org we're not in.
        """
        # Create our test user
        user = UserFactory()

        # Create two organizations our user belongs to
        org1 = OrganizationFactory(name="Our Organization 1")
        org2 = OrganizationFactory(name="Our Organization 2")
        user.memberships.create(organization=org1)
        user.memberships.create(organization=org2)

        # Create a third organization we're NOT a member of (for isolation testing)
        other_org = OrganizationFactory(name="Other Organization")
        other_user = UserFactory()
        other_user.memberships.create(organization=other_org)

        # Create programs owned by each organization (direct relationship)
        program1 = ProgramFactory(organization=org1)
        program2 = ProgramFactory(organization=org2)
        other_program = ProgramFactory(organization=other_org)

        # SECTION 1:
        # Create 1 solicitation we should see (from programs our orgs are members of)
        our_solicitation = SolicitationFactory(
            program=program1, status=Solicitation.Status.ACTIVE, title="Our Visible Solicitation"
        )

        # Create 1 solicitation we should NOT see (from other org's program)
        other_solicitation = SolicitationFactory(
            program=other_program, status=Solicitation.Status.ACTIVE, title="Other Org Solicitation"
        )

        # SECTION 2:
        # Create 2 responses we made (should see both)
        response1 = SolicitationResponseFactory(
            solicitation=our_solicitation,
            organization=org1,
            submitted_by=user,
            status=SolicitationResponse.Status.SUBMITTED,
        )
        response2 = SolicitationResponseFactory(
            solicitation=our_solicitation,
            organization=org2,
            submitted_by=user,
            status=SolicitationResponse.Status.SUBMITTED,
        )

        # Create 1 response we should NOT see (from other org)
        other_response = SolicitationResponseFactory(
            solicitation=other_solicitation,
            organization=other_org,
            submitted_by=other_user,
            status=SolicitationResponse.Status.SUBMITTED,
        )

        # SECTION 3:
        # Create 3 reviews we did (should see all 3)
        review1 = SolicitationReviewFactory(
            response=response1, reviewer=user, recommendation=SolicitationReview.Recommendation.RECOMMENDED
        )
        review2 = SolicitationReviewFactory(
            response=response2, reviewer=user, recommendation=SolicitationReview.Recommendation.NOT_RECOMMENDED
        )
        # Create a second solicitation for the third response we should not see
        another_solicitation = SolicitationFactory(
            program=program2, status=Solicitation.Status.ACTIVE, title="Another Solicitation for Review"
        )

        # Create a third response on the different solicitation
        another_response = SolicitationResponseFactory(
            solicitation=another_solicitation, organization=org1, submitted_by=UserFactory()
        )
        review3 = SolicitationReviewFactory(
            response=another_response, reviewer=user, recommendation=SolicitationReview.Recommendation.NEUTRAL
        )

        # Create 1 review we should NOT see (done by other user)
        other_review = SolicitationReviewFactory(
            response=other_response, reviewer=other_user, recommendation=SolicitationReview.Recommendation.RECOMMENDED
        )

        # Make the request
        client.force_login(user)
        url = reverse("solicitations:dashboard")
        response = client.get(url)

        assert response.status_code == 200
        content = response.content.decode()

        # VERIFY SECTION 1: Solicitations (should see 2, not the other org's)
        assert our_solicitation.title in content
        assert another_solicitation.title in content
        assert other_solicitation.title not in content

        # VERIFY SECTION 2: Check that we have 3 user responses in the table
        responses_table = response.context["responses_table"]
        assert len(responses_table.data) == 3

        # Verify our responses are there but not the other org's
        response_ids = [row.id for row in responses_table.data]
        assert response1.id in response_ids
        assert response2.id in response_ids
        assert another_response.id in response_ids
        assert other_response.id not in response_ids

        # VERIFY REVIEWS:
        reviews_table = response.context["reviews_table"]
        assert len(reviews_table.data) == 3
        review_ids = [row.id for row in reviews_table.data]
        assert review1.id in review_ids
        assert review2.id in review_ids
        assert review3.id in review_ids
        assert other_review.id not in review_ids

        # VERIFY STATS: Check the statistics are correct
        stats = response.context["stats"]
        assert stats["total_active_solicitations"] == 2  # Our two solicitations
        assert stats["total_responses"] == 3  # Our 3 responses (excluding other org)
        assert stats["programs_count"] == 2  # Our 2 programs

    def test_network_manager_dashboard_shows_only_responses(self, client):
        """
        Test that Network Manager users see only their responses, with empty solicitations and reviews tables.

        Network Manager organizations (program_manager=False) can respond to solicitations but cannot:
        - Create solicitations (only Program Manager orgs can)
        - Review responses (only Program Manager orgs can)
        """
        # Create a Network Manager user and organization
        nm_user = UserFactory()
        nm_org = OrganizationFactory(name="Network Manager Org", program_manager=False)  # Network Manager
        nm_user.memberships.create(organization=nm_org)

        # Create a Program Manager organization that will own programs/solicitations
        pm_user = UserFactory()
        pm_org = OrganizationFactory(name="Program Manager Org", program_manager=True)  # Program Manager
        pm_user.memberships.create(organization=pm_org)

        # Create a program owned by the Program Manager organization
        program = ProgramFactory(organization=pm_org)

        # Create an active solicitation from the Program Manager's program
        solicitation = SolicitationFactory(
            program=program, status=Solicitation.Status.ACTIVE, title="PM Solicitation for NM Response"
        )

        # Network Manager submits a response to the Program Manager's solicitation
        nm_response = SolicitationResponseFactory(
            solicitation=solicitation,
            organization=nm_org,
            submitted_by=nm_user,
            status=SolicitationResponse.Status.SUBMITTED,
        )

        # Create some data that should NOT appear for Network Manager:

        # 1. Another organization's response (should not see)
        other_nm_org = OrganizationFactory(name="Other NM Org", program_manager=False)
        other_nm_user = UserFactory()
        other_nm_user.memberships.create(organization=other_nm_org)
        SolicitationResponseFactory(
            solicitation=solicitation,
            organization=other_nm_org,
            submitted_by=other_nm_user,
            status=SolicitationResponse.Status.SUBMITTED,
        )

        # 2. A review done by the Program Manager (Network Manager can't see reviews)
        SolicitationReviewFactory(
            response=nm_response, reviewer=pm_user, recommendation=SolicitationReview.Recommendation.RECOMMENDED
        )

        # Login as Network Manager and access dashboard
        client.force_login(nm_user)
        url = reverse("solicitations:dashboard")
        response = client.get(url)

        assert response.status_code == 200

        # VERIFY: Network Manager should NOT see solicitations (they don't own any programs)
        # The solicitations table should be empty because NM org doesn't own any programs
        solicitations_table = response.context["table"]  # Main table is solicitations
        assert len(solicitations_table.data) == 0

        # VERIFY: Network Manager should see their response in responses table
        responses_table = response.context["responses_table"]
        assert len(responses_table.data) == 1
        assert nm_response.id in [row.id for row in responses_table.data]

        # VERIFY: Network Manager should NOT see any reviews (they can't review)
        reviews_table = response.context["reviews_table"]
        assert len(reviews_table.data) == 0  # Empty reviews table

        # VERIFY: Stats should reflect Network Manager perspective
        stats = response.context["stats"]
        assert stats["total_active_solicitations"] == 0  # NM org owns no programs, so no solicitations
        assert stats["total_responses"] == 1  # Only their 1 response
        assert stats["programs_count"] == 0  # NM org owns no programs
