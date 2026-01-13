"""
Tests for audit creation services.

These tests verify the high-level audit creation and preview workflows
using the service layer.
"""

from unittest.mock import MagicMock

import pytest
from django.test import TestCase

from commcare_connect.audit.models import Audit
from commcare_connect.audit.services.audit_creator import create_audit_sessions, preview_audit_sessions


@pytest.mark.django_db
class AuditCreationServiceTest(TestCase):
    """Test audit creation service"""

    def setUp(self):
        """Set up mocked facade"""
        self.mock_facade = MagicMock()

    def test_create_audit_sessions_combined_granularity(self):
        """Test creating a combined audit session across opportunities"""
        # Mock all required facade methods
        self.mock_facade.get_opportunity_details.return_value = [
            {"id": 1, "name": "Test Opp", "organization_id": 1, "organization_name": "Org", "active": True}
        ]
        self.mock_facade.get_users_for_opportunities.return_value = [
            {"id": 1, "username": "user1", "name": "User One", "email": "user1@test.com", "is_active": True}
        ]
        self.mock_facade.get_deliver_units_for_visits.return_value = []
        self.mock_facade.get_user_visits_for_audit.return_value = []

        # Create audit with combined granularity
        criteria = {"type": "last_n_per_flw", "granularity": "combined", "countPerFlw": 10}

        result = create_audit_sessions(
            facade=self.mock_facade, opportunity_ids=[1], criteria=criteria, auditor_username="auditor"
        )

        # Verify success and one audit created
        assert result.success is True
        assert result.audits_created == 1
        assert result.first_audit.status == Audit.Status.IN_PROGRESS

    def test_create_audit_sessions_per_flw_granularity(self):
        """Test creating separate audit sessions per FLW"""
        from datetime import datetime

        # Mock all required facade methods
        self.mock_facade.get_opportunity_details.return_value = [
            {"id": 1, "name": "Test Opp", "organization_id": 1, "organization_name": "Org", "active": True}
        ]
        self.mock_facade.get_users_for_opportunities.return_value = [
            {"id": 1, "username": "user1", "name": "User One", "email": "user1@test.com", "is_active": True},
            {"id": 2, "username": "user2", "name": "User Two", "email": "user2@test.com", "is_active": True},
        ]
        self.mock_facade.get_deliver_units_for_visits.return_value = []

        # Return visits for each user_id
        def mock_get_visits(opportunity_ids, audit_type, start_date=None, end_date=None, count=None, user_id=None):
            if user_id == 1:
                return [
                    {
                        "xform_id": f"form{i}",
                        "user_id": 1,
                        "opportunity_id": 1,
                        "visit_date": datetime.now(),
                        "entity_id": f"entity{i}",
                        "entity_name": f"Entity {i}",
                        "status": "approved",
                    }
                    for i in range(5)
                ]
            elif user_id == 2:
                return [
                    {
                        "xform_id": f"form{i+10}",
                        "user_id": 2,
                        "opportunity_id": 1,
                        "visit_date": datetime.now(),
                        "entity_id": f"entity{i}",
                        "entity_name": f"Entity {i}",
                        "status": "approved",
                    }
                    for i in range(5)
                ]
            return []

        self.mock_facade.get_user_visits_for_audit.side_effect = mock_get_visits
        self.mock_facade.get_unique_flws_across_opportunities.return_value = [
            {"user_id": 1, "username": "user1"},
            {"user_id": 2, "username": "user2"},
        ]

        # Create audit with per-FLW granularity
        criteria = {"type": "last_n_per_flw", "granularity": "per_flw", "countPerFlw": 5}

        result = create_audit_sessions(
            facade=self.mock_facade, opportunity_ids=[1], criteria=criteria, auditor_username="auditor"
        )

        # Verify success and two audits created (one per FLW)
        assert result.success is True
        assert result.audits_created == 2

    def test_create_audit_sessions_with_flw_limit(self):
        """Test that limit_flws parameter works correctly"""
        from datetime import datetime

        # Mock many FLWs
        self.mock_facade.get_opportunity_details.return_value = [
            {"id": 1, "name": "Test Opp", "organization_id": 1, "organization_name": "Org", "active": True}
        ]
        self.mock_facade.get_users_for_opportunities.return_value = [
            {"id": i, "username": f"user{i}", "name": f"User {i}", "email": f"user{i}@test.com", "is_active": True}
            for i in range(1, 11)
        ]
        self.mock_facade.get_deliver_units_for_visits.return_value = []

        # Return visits for each user_id
        def mock_get_visits(opportunity_ids, audit_type, start_date=None, end_date=None, count=None, user_id=None):
            if user_id:
                return [
                    {
                        "xform_id": f"form{user_id}_{i}",
                        "user_id": user_id,
                        "opportunity_id": 1,
                        "visit_date": datetime.now(),
                        "entity_id": f"entity{i}",
                        "entity_name": f"Entity {i}",
                        "status": "approved",
                    }
                    for i in range(5)
                ]
            return []

        self.mock_facade.get_user_visits_for_audit.side_effect = mock_get_visits
        self.mock_facade.get_unique_flws_across_opportunities.return_value = [
            {"user_id": i, "username": f"user{i}"} for i in range(1, 11)
        ]

        # Create audit with limit of 3 FLWs
        criteria = {"type": "last_n_per_flw", "granularity": "per_flw", "countPerFlw": 5}

        result = create_audit_sessions(
            facade=self.mock_facade,
            opportunity_ids=[1],
            criteria=criteria,
            auditor_username="auditor",
            limit_flws=3,
        )

        # Verify only 3 audits created (limited)
        assert result.success is True
        assert result.audits_created == 3

    def test_preview_audit_sessions(self):
        """Test preview returns accurate counts without creating audits"""
        # Mock opportunity search and counts
        mock_opp = MagicMock()
        mock_opp.id = 1
        mock_opp.name = "Test Opportunity"
        self.mock_facade.search_opportunities.return_value = [mock_opp]

        self.mock_facade.get_flw_visit_counts_last_n_per_flw.return_value = {
            "total_flws": 5,
            "total_visits": 50,
            "date_range": ["2024-01-01", "2024-12-31"],
            "flws": [],
        }

        # Generate preview
        criteria = {"type": "last_n_per_flw", "granularity": "per_flw", "countPerFlw": 10}

        result = preview_audit_sessions(facade=self.mock_facade, opportunity_ids=[1], criteria=criteria)

        # Verify preview data
        assert result.success is True
        assert len(result.preview_data) == 1
        preview = result.preview_data[0]
        assert preview["opportunity_id"] == 1
        assert preview["total_flws"] == 5
        assert preview["total_visits"] == 50
        assert preview["sessions_to_create"] == 5  # per_flw means one session per FLW
        assert preview["granularity"] == "per_flw"

    def test_preview_combined_vs_per_flw_sessions_count(self):
        """Test that preview correctly calculates sessions_to_create based on granularity"""
        mock_opp = MagicMock()
        mock_opp.id = 1
        mock_opp.name = "Test Opportunity"
        self.mock_facade.search_opportunities.return_value = [mock_opp]

        self.mock_facade.get_flw_visit_counts_last_n_per_flw.return_value = {
            "total_flws": 10,
            "total_visits": 100,
            "date_range": ["2024-01-01", "2024-12-31"],
            "flws": [],
        }

        # Test combined granularity
        criteria_combined = {"type": "last_n_per_flw", "granularity": "combined", "countPerFlw": 10}
        result_combined = preview_audit_sessions(
            facade=self.mock_facade, opportunity_ids=[1], criteria=criteria_combined
        )
        assert result_combined.preview_data[0]["sessions_to_create"] == 1  # Combined = 1 session

        # Test per_flw granularity
        criteria_per_flw = {"type": "last_n_per_flw", "granularity": "per_flw", "countPerFlw": 10}
        result_per_flw = preview_audit_sessions(
            facade=self.mock_facade, opportunity_ids=[1], criteria=criteria_per_flw
        )
        assert result_per_flw.preview_data[0]["sessions_to_create"] == 10  # Per FLW = 10 sessions
