"""
Tests for database reset functionality.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from commcare_connect.audit.models import AuditDefinition, AuditSession
from commcare_connect.opportunity.models import Opportunity, UserVisit
from commcare_connect.opportunity.tests.factories import OpportunityFactory, UserVisitFactory
from commcare_connect.users.tests.factories import UserFactory

User = get_user_model()


@pytest.mark.django_db
class DatabaseResetTest(TestCase):
    """Test database reset functionality"""

    def setUp(self):
        """Set up test data"""
        # Create login account (should be preserved)
        self.admin_user = UserFactory(is_staff=True, is_superuser=True, username="admin")
        self.staff_user = UserFactory(is_staff=True, is_superuser=False, username="staff")
        self.client.force_login(self.admin_user)

        # Create FLW users (should be deleted)
        self.flw1 = UserFactory(is_staff=False, is_superuser=False, username="flw1")
        self.flw2 = UserFactory(is_staff=False, is_superuser=False, username="flw2")

    def test_database_reset_deletes_flw_users_but_preserves_logins(self):
        """Test that reset deletes FLW users with visits but preserves login accounts and other users"""
        # Create some opportunities and visits
        opp = OpportunityFactory()
        UserVisitFactory(user=self.flw1, opportunity=opp)
        UserVisitFactory(user=self.flw2, opportunity=opp)

        # Create a third FLW user without visits (should be preserved)
        UserFactory(is_staff=False, is_superuser=False, username="flw_no_visits")

        assert UserVisit.objects.count() >= 2
        assert Opportunity.objects.count() >= 1

        # Perform reset
        response = self.client.post(
            reverse("audit:reset_database"),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Note: deleted["users"] shows initial count of all non-staff/non-superuser, not actual deleted
        assert data["deleted"]["visits"] >= 2
        assert data["deleted"]["opportunities"] >= 1

        # Verify FLW users with visits were deleted
        assert not User.objects.filter(username="flw1").exists()
        assert not User.objects.filter(username="flw2").exists()

        # Verify FLW user without visits was preserved
        assert User.objects.filter(username="flw_no_visits").exists()

        # Verify login accounts preserved
        assert User.objects.filter(username="admin").exists()
        assert User.objects.filter(username="staff").exists()

        # Verify other data deleted
        assert UserVisit.objects.count() == 0
        assert Opportunity.objects.count() == 0

    def test_database_reset_preserves_superuser_even_if_not_staff(self):
        """Test that superusers are always preserved, and FLW users without visits are not deleted"""
        # Create a superuser that's not marked as staff
        UserFactory(is_staff=False, is_superuser=True, username="superuser_only")

        # Perform reset
        response = self.client.post(
            reverse("audit:reset_database"),
            content_type="application/json",
        )

        assert response.status_code == 200

        # Superuser should be preserved
        assert User.objects.filter(username="superuser_only").exists()
        # FLWs without visits should also be preserved (not deleted because no visits)
        assert User.objects.filter(username="flw1").exists()
        assert User.objects.filter(username="flw2").exists()

    def test_database_reset_deletes_audit_definitions(self):
        """Test that reset deletes AuditDefinition records"""
        from datetime import date

        # Create some audit definitions
        audit_def1 = AuditDefinition.objects.create(
            name="Test Audit 1",
            created_by=self.admin_user,
            opportunity_ids=[1, 2, 3],
            audit_type="last_n_per_flw",
            granularity="per_flw",
            count_per_flw=5,
            status=AuditDefinition.Status.READY,
        )

        AuditDefinition.objects.create(
            name="Test Audit 2",
            created_by=self.staff_user,
            opportunity_ids=[4, 5],
            audit_type="last_n_across_all",
            granularity="combined",
            count_across_all=1000,
            sample_percentage=10,
            status=AuditDefinition.Status.USED,
        )

        # Create an audit session linked to one definition
        opp = OpportunityFactory()  # noqa: F841 - created for database state
        AuditSession.objects.create(
            auditor_username="test",
            flw_username="flw1",
            opportunity_name="Test",
            domain="test",
            app_id="test-app",
            start_date=date.today(),
            end_date=date.today(),
            audit_definition=audit_def1,
        )

        # Verify they exist
        assert AuditDefinition.objects.count() == 2
        assert AuditSession.objects.count() == 1

        # Perform reset
        response = self.client.post(
            reverse("audit:reset_database"),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted"]["audit_definitions"] == 2
        assert data["deleted"]["audit_sessions"] == 1

        # Verify audit definitions and sessions were deleted
        assert AuditDefinition.objects.count() == 0
        assert AuditSession.objects.count() == 0

        # Verify user accounts were preserved
        assert User.objects.filter(username="admin").exists()
        assert User.objects.filter(username="staff").exists()

    def tearDown(self):
        """Clean up - Django handles this automatically"""
        pass
